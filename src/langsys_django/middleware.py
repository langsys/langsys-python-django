"""Request-locale middleware.

Serves each request in the locale Django resolved for it. Where the app runs Django's
``LocaleMiddleware`` (placed before this one), ``request.LANGUAGE_CODE`` is that locale, and the SDK
maps it to the project's form — ``es-ES`` and ``es_ES`` are ``es-es``, a bare ``es`` the project's
default Spanish locale — serving the base locale for one the project does not serve. The app varies
its responses on what Django decided, so nothing is added to ``Vary``.

Where nothing resolved a locale, the SDK resolves it: the URL (the query parameter, or the language
prefix of ``i18n_patterns``), then the app's locale cookie, then ``Accept-Language``, then the
project's base locale, each validated against the locales the project serves. The response gets
the ``Vary`` headers that choice depended on. No cookie is written: storing a visitor's choice is
the app's.

The locale is exposed to translations for the whole request, including a streamed body rendered
after this middleware has returned.

Each request runs inside one of the core's request scopes, so a phrase discovered while serving
it is held until its response has been sent — by the core's debounce, an explicit flush and every
other request's flush alike. The scope ends when the response is closed, just before Django fires
``request_finished``; that signal then flushes the core's queue and forgets the request's write
decision (see ``client._finish_request``). Nothing is registered on the visitor's time.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Iterable, Iterator
from typing import Any, Callable, Optional

from django.conf import settings
from django.conf.urls.i18n import is_language_prefix_patterns_used
from django.http import HttpRequest, HttpResponse
from django.utils.cache import patch_vary_headers
from django.utils.translation import get_language_from_path
from langsys import LocaleChoice, RequestScope, begin_request_scope, end_request_scope

from .client import get_client
from .conf import get_settings
from .locale import reset_current_locale, set_current_locale


class LangsysMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        self._cfg = get_settings()

    def __call__(self, request: HttpRequest) -> HttpResponse:
        choice = self._resolve(request)
        locale = choice.locale
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
        if choice.vary:
            patch_vary_headers(response, choice.vary)
        return response

    def _resolve(self, request: HttpRequest) -> LocaleChoice:
        """The locale Django resolved, when ``LocaleMiddleware`` ran; otherwise the SDK's own."""
        framework = getattr(request, "LANGUAGE_CODE", None)
        if framework:
            return get_client().resolve_request_locale(framework=framework)
        return get_client().resolve_request_locale(
            url=self._url_locale(request),
            cookie=request.COOKIES.get(self._cfg.cookie_name),
            accept_language=request.META.get("HTTP_ACCEPT_LANGUAGE"),
        )

    def _url_locale(self, request: HttpRequest) -> Optional[str]:
        """The locale the URL carries: the query parameter, or an ``i18n_patterns`` prefix.

        A path prefix counts only when the URLconf routes by one, which is the check Django's own
        ``LocaleMiddleware`` makes, so a path that merely starts with ``/de/`` is not a locale.
        """
        query: Optional[str] = request.GET.get(self._cfg.query_param)
        if query:
            return query
        urlconf = getattr(request, "urlconf", None) or settings.ROOT_URLCONF
        prefixed, _ = is_language_prefix_patterns_used(urlconf)
        return get_language_from_path(request.path_info) if prefixed else None


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
