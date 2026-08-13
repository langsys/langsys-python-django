"""Request-locale middleware.

Resolves the locale for each request (``?locale=`` -> cookie -> ``Accept-Language``),
exposes it to translations for the duration of the request, persists an explicit choice
to a cookie, and — with a write key and ``AUTO_FLUSH`` on — registers any phrases
discovered while rendering after the response is sent.
"""

from __future__ import annotations

import logging
from typing import Callable

from django.http import HttpRequest, HttpResponse
from langsys import canonicalize_locale

from .client import get_client
from .conf import get_settings
from .locale import reset_current_locale, set_current_locale

logger = logging.getLogger("langsys")


class LangsysMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._cfg = get_settings()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        client = get_client()
        locale, persist = self._resolve(request, client)
        token = set_current_locale(locale) if locale else None
        try:
            response = self.get_response(request)
        finally:
            if token is not None:
                reset_current_locale(token)

        if persist and locale:
            response.set_cookie(
                self._cfg.cookie_name,
                locale,
                max_age=self._cfg.cookie_max_age,
                samesite="Lax",
            )

        self._handle_pending(client)
        return response

    def _resolve(self, request: HttpRequest, client: object) -> tuple[str, bool]:
        query = request.GET.get(self._cfg.query_param)
        if query:
            return canonicalize_locale(query), True  # explicit choice -> persist
        cookie = request.COOKIES.get(self._cfg.cookie_name)
        if cookie:
            return canonicalize_locale(cookie), False
        header = request.META.get("HTTP_ACCEPT_LANGUAGE")
        detected = client.detect_preferred_locale(header, self._cfg.supported or None)  # type: ignore[attr-defined]
        return (detected or ""), False

    def _handle_pending(self, client: object) -> None:
        if not client.has_pending:  # type: ignore[attr-defined]
            return
        # Register on write keys (when enabled); otherwise just drop the queue so a
        # long-running read-key server doesn't accumulate it unbounded.
        try:
            if self._cfg.auto_flush and client.can_write:  # type: ignore[attr-defined]
                client.flush_pending()  # type: ignore[attr-defined]
            else:
                client.clear_pending()  # type: ignore[attr-defined]
        except Exception as exc:  # pragma: no cover - never break the response
            logger.warning("langsys: flushing pending registrations failed: %s", exc)
