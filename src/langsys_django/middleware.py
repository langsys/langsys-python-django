"""Request-locale middleware.

Resolves the locale for each request (``?locale=`` -> cookie -> ``Accept-Language``), exposes
it to translations for the duration of the request — including a streamed body rendered after
this middleware has returned — and persists an explicit choice to a cookie.

Each request runs inside one of the core's request scopes, so a phrase discovered while serving
it is held until its response has been sent — by the core's debounce, an explicit flush and every
other request's flush alike. The scope ends when the response is closed, just before Django fires
``request_finished``; that signal then flushes the core's queue and forgets the request's write
decision (see ``client._finish_request``). Nothing is registered on the visitor's time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any, Callable

from django.http import HttpRequest, HttpResponse
from langsys import (
    LangsysClient,
    RequestScope,
    begin_request_scope,
    canonicalize_locale,
    end_request_scope,
)

from .client import get_client
from .conf import get_settings
from .locale import reset_current_locale, set_current_locale


class LangsysMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._cfg = get_settings()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        client = get_client()
        locale, persist = self._resolve(request, client)
        token = set_current_locale(locale) if locale else None
        scope = begin_request_scope()
        served = False
        try:
            response = self.get_response(request)
            served = True
        finally:
            if token is not None:
                reset_current_locale(token)
            # Without a response there is nothing to close, so the scope ends here.
            if not served:
                end_request_scope(scope)

        _end_scope_on_close(response, scope)

        if locale and response.streaming:
            _stream_in_locale(response, locale)
        if persist and locale:
            response.set_cookie(
                self._cfg.cookie_name,
                locale,
                max_age=self._cfg.cookie_max_age,
                samesite="Lax",
            )
        return response

    def _resolve(self, request: HttpRequest, client: LangsysClient) -> tuple[str, bool]:
        query = request.GET.get(self._cfg.query_param)
        if query:
            return canonicalize_locale(query), True  # explicit choice -> persist
        cookie = request.COOKIES.get(self._cfg.cookie_name)
        if cookie:
            return canonicalize_locale(cookie), False
        header = request.META.get("HTTP_ACCEPT_LANGUAGE")
        detected = client.detect_preferred_locale(header, self._cfg.supported or None)
        return (detected or ""), False


def _end_scope_on_close(response: Any, scope: RequestScope) -> None:
    """End the request's scope when the response is closed, before ``request_finished`` fires.

    Servers close a response once its body is out: a WSGI server after iterating it, Django's
    ASGI handler after sending it. Ending the scope there releases the request's misses, and the
    ``request_finished`` flush that ``close()`` then triggers sends them. The handle travels in
    the closure, so it does not matter which thread or task the close runs in.
    """
    close = response.close

    def close_after_ending_scope() -> None:
        end_request_scope(scope)
        close()

    response.close = close_after_ending_scope


def _stream_in_locale(response: Any, locale: str) -> None:
    """Render each chunk of a streamed body under the request's locale.

    A streamed body is produced while the server iterates it, after ``__call__`` has returned
    and reset the context variable. The locale is set around fetching each chunk rather than
    held across ``yield``, so it never leaks into the code consuming the stream.
    """
    if response.is_async:
        response.streaming_content = _achunks_in_locale(response.streaming_content, locale)
    else:
        response.streaming_content = _chunks_in_locale(response.streaming_content, locale)


def _chunks_in_locale(content: Iterable[bytes], locale: str) -> Iterator[bytes]:
    chunks = iter(content)
    while True:
        token = set_current_locale(locale)
        try:
            chunk = next(chunks)
        except StopIteration:
            return
        finally:
            reset_current_locale(token)
        yield chunk


async def _achunks_in_locale(content: AsyncIterator[bytes], locale: str) -> AsyncIterator[bytes]:
    chunks = content.__aiter__()
    while True:
        token = set_current_locale(locale)
        try:
            chunk = await chunks.__anext__()
        except StopAsyncIteration:
            return
        finally:
            reset_current_locale(token)
        yield chunk
