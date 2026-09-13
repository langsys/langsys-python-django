"""The shared :class:`~langsys.LangsysClient`, the ``t()`` helper, and the request boundary."""

from __future__ import annotations

import threading
from typing import Any, Optional

from langsys import LangsysClient

from .conf import get_settings
from .locale import ContextVarLocaleSource

_client: Optional[LangsysClient] = None
_lock = threading.Lock()


def get_client() -> LangsysClient:
    """Return the process-wide Langsys client (built once from Django settings).

    The client's locale is driven by the request-scoped context variable, so a single
    shared instance is safe across concurrent requests.
    """
    global _client
    if _client is None:
        with _lock:
            if _client is None:
                cfg = get_settings()
                _client = LangsysClient(
                    api_key=cfg.api_key,
                    project_id=cfg.project_id,
                    api_url=cfg.api_url,
                    base_locale=cfg.base_locale,
                    locale_source=ContextVarLocaleSource(),
                )
    return _client


def set_client(client: LangsysClient) -> None:
    """Use a caller-built client instead of the one derived from settings.

    Handy for advanced setups (custom cache/timeout) and tests. The client should read
    its locale from :class:`ContextVarLocaleSource` to stay request-safe.
    """
    global _client
    _client = client


def reset_client() -> None:
    """Drop the cached client (mainly for tests / settings changes)."""
    global _client
    if _client is not None:
        _client.close()
    _client = None


def t(phrase: str, category: Optional[str] = None, **params: Any) -> str:
    """Translate ``phrase`` for the current request locale.

    ``t("Hello, {name}!", "Greetings", name="Sarah")`` — keyword args become interpolation
    params. Usable in views; the ``{% t %}`` template tag delegates here.
    """
    return get_client().translate(phrase, category=category, params=params or None)


def _finish_request(sender: Any, **kwargs: Any) -> None:
    """End the request for the shared client: register what it found, forget its decision.

    Connected to ``request_finished``, which Django fires from ``response.close()`` once the
    response has been sent. So registration never spends the visitor's time (SRV-3), still
    happens at the end of every request rather than only at process exit (REG-3), and the
    write decision never outlives the request (GATE-3).

    Both calls are the core's public seams, in this order on purpose: the flush sees the
    decision this request observed, and then it is forgotten. Whether the queue is sent, held
    or discarded is the core's decision alone — the binding never reads capability (BIND-2).
    """
    client = _client
    if client is None:  # nothing has translated in this process yet
        return
    client.flush_pending()
    client.reset_write_decision()
