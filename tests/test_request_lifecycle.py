"""Request-lifecycle behaviour this binding owns, measured through Django's own handler.

Every test drives a real request through ``django.test.Client``, which emulates a WSGI server
by calling ``response.close()`` once the response is complete — the point at which Django
fires ``request_finished``. Unless a test says otherwise the core client runs with
``debounce=None`` and ``auto_flush=False``, so a registration observed after a request can only
have been sent by the binding's request-boundary hook: nothing else in the process sends.
"""

from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Optional

import httpx
import pytest
from django.http import HttpRequest, HttpResponse, StreamingHttpResponse
from django.template import RequestContext, Template
from django.test import Client, RequestFactory
from django.urls import path
from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.client import DEFAULT_DEBOUNCE_SECONDS

from langsys_django import t
from langsys_django.client import reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource
from langsys_django.middleware import LangsysMiddleware

pytestmark = [
    pytest.mark.urls("tests.test_request_lifecycle"),
    # The read-only tests register an items endpoint the core must never reach.
    pytest.mark.httpx_mock(assert_all_responses_were_requested=False),
]

AUTH = re.compile(r"https://api\.test/api/authorize-project/")
TRANS = re.compile(r"https://api\.test/api/translations")
ITEMS = re.compile(r"https://api\.test/api/translatable-items")

#: Ordered record of what happened during a request, appended to by views, the recording
#: middleware and the registration endpoint.
EVENTS: list[str] = []
INTERLEAVE: dict[str, Optional[threading.Barrier]] = {"locales-set": None, "translated": None}
SCOPES: dict[str, threading.Event] = {}


# -- views ----------------------------------------------------------------------


def queue_a_miss(request: HttpRequest) -> HttpResponse:
    t("A phrase the catalog has never seen", "UI")
    EVENTS.append("rendered")
    return HttpResponse("ok")


def queue_another_miss(request: HttpRequest) -> HttpResponse:
    t("A second unseen phrase", "UI")
    return HttpResponse("ok")


def queue_a_miss_then_keep_rendering(request: HttpRequest) -> HttpResponse:
    t("A phrase the catalog has never seen", "UI")
    # A render that outlasts the debounce. Inside the request's scope the core sends nothing
    # here, so this bounded wait for a send times out and the render carries on; a send that did
    # happen would release it at once and come first in the recorded order.
    SCOPES["posted"].wait(timeout=DEFAULT_DEBOUNCE_SECONDS * 3)
    EVENTS.append("rendered")
    return HttpResponse("ok")


def record_a_miss_and_hold_the_render(request: HttpRequest) -> HttpResponse:
    t("Recorded inside a request that is still rendering", "UI")
    SCOPES["recorded"].set()
    SCOPES["release"].wait(timeout=5)
    EVENTS.append("held-rendered")
    return HttpResponse("ok")


def stream_a_miss_then_keep_streaming(request: HttpRequest) -> StreamingHttpResponse:
    def body():
        yield t("Streamed and never seen", "UI")
        # A stream that outlasts the debounce, waiting on a send exactly as the slow render does.
        SCOPES["posted"].wait(timeout=DEFAULT_DEBOUNCE_SECONDS * 3)
        yield "|done"
        EVENTS.append("body-complete")

    return StreamingHttpResponse(body())


def stream_a_render(request: HttpRequest) -> StreamingHttpResponse:
    def body():
        # Rendered lazily: this runs while the server iterates the body, after every
        # middleware has already returned.
        yield t("Pricing", "UI")
        yield "|"
        yield t("Streamed and never seen", "UI")
        EVENTS.append("body-complete")

    return StreamingHttpResponse(body())


def render_template(request: HttpRequest) -> HttpResponse:
    template = Template(
        '{% load langsys %}{% t "Pricing" "UI" %}|{% t "Not in the catalog" "UI" %}'
    )
    return HttpResponse(template.render(RequestContext(request)))


def interleave(request: HttpRequest) -> HttpResponse:
    locales_set, translated = INTERLEAVE["locales-set"], INTERLEAVE["translated"]
    assert locales_set is not None and translated is not None
    first = t("Pricing", "UI")
    locales_set.wait(timeout=5)  # both requests have now set their locale
    second = t("Pricing", "UI")
    translated.wait(timeout=5)  # and neither has returned, so neither has reset it
    return HttpResponse(f"{first}|{second}")


def render_every_entry_point(request: HttpRequest) -> HttpResponse:
    Template('{% load langsys %}{% t "From the tag" "UI" %}{{ "From the filter"|t:"UI" }}').render(
        RequestContext(request)
    )
    t("From the helper", "UI")
    return HttpResponse("ok")


urlpatterns = [
    path("miss/", queue_a_miss),
    path("miss-again/", queue_another_miss),
    path("slow-miss/", queue_a_miss_then_keep_rendering),
    path("held-render/", record_a_miss_and_hold_the_render),
    path("stream/", stream_a_render),
    path("slow-stream/", stream_a_miss_then_keep_streaming),
    path("render/", render_template),
    path("interleave/", interleave),
    path("every-entry-point/", render_every_entry_point),
]


class RecordingMiddleware:
    """Outermost middleware: records the moment the response leaves the stack."""

    def __init__(self, get_response: Any) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        EVENTS.append("response-returned")
        return response


# -- fixtures and doubles -------------------------------------------------------


@pytest.fixture(autouse=True)
def _events():
    EVENTS.clear()
    SCOPES.clear()
    yield
    EVENTS.clear()
    SCOPES.clear()


def _install(settings: Any, debounce: Optional[float]) -> LangsysClient:
    settings.MIDDLEWARE = [
        "tests.test_request_lifecycle.RecordingMiddleware",
        "langsys_django.middleware.LangsysMiddleware",
    ]
    reset_client()
    instance = LangsysClient(
        "k",
        "proj-1",
        api_url="https://api.test/api",
        base_locale="en-US",
        cache=MemoryCache(),
        locale_source=ContextVarLocaleSource(),
        debounce=debounce,
        auto_flush=False,
    )
    set_client(instance)
    return instance


@pytest.fixture()
def langsys(settings):
    yield _install(settings, debounce=None)
    reset_client()


@pytest.fixture()
def langsys_debounced(settings):
    """The core's default debounce, as the settings-built client runs it."""
    yield _install(settings, debounce=DEFAULT_DEBOUNCE_SECONDS)
    reset_client()


def authorize(key_type: str = "write", write_enabled: Optional[bool] = True) -> dict[str, Any]:
    data: dict[str, Any] = {
        "id": "proj-1",
        "title": "T",
        "base_locale": "en-us",
        "target_locales": ["es-es", "it-it", "de-de"],
        "default_locales": {},
        "key_type": key_type,
        "langsys_settings": {"translatable_items": {"batch_limit": 200}},
    }
    if write_enabled is not None:
        data["write_enabled"] = write_enabled
    return {"status": True, "data": data}


def catalog(data: dict[str, Any]) -> dict[str, Any]:
    return {"status": True, "words": 0, "untranslatedWords": 0, "data": data}


def serve_catalogs(httpx_mock: Any, by_locale: dict[str, dict[str, Any]]) -> None:
    """Answer each catalog request with the catalog for the locale it asked for."""

    def _catalog(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=catalog(by_locale.get(request.url.params["locale"], {"UI": {}}))
        )

    httpx_mock.add_callback(_catalog, url=TRANS, is_reusable=True)


def accept_items(httpx_mock: Any) -> None:
    def _accept(request: httpx.Request) -> httpx.Response:
        EVENTS.append("posted")
        if "posted" in SCOPES:
            SCOPES["posted"].set()
        return httpx.Response(200, json={"status": True})

    httpx_mock.add_callback(_accept, url=ITEMS, is_reusable=True)


def accept_items_naming_phrases(httpx_mock: Any) -> None:
    def _accept(request: httpx.Request) -> httpx.Response:
        for item in json.loads(request.content)["translatable_items"]:
            EVENTS.append(f"posted:{item['phrase']}")
        return httpx.Response(200, json={"status": True})

    httpx_mock.add_callback(_accept, url=ITEMS, is_reusable=True)


def posts(httpx_mock: Any) -> list[httpx.Request]:
    return [r for r in httpx_mock.get_requests() if r.url.path.endswith("translatable-items")]


def registered_phrases(httpx_mock: Any) -> list[str]:
    return [
        item["phrase"]
        for request in posts(httpx_mock)
        for item in json.loads(request.content)["translatable_items"]
    ]


# -- REG-3 ----------------------------------------------------------------------


def test_REG3_the_queue_is_registered_at_the_end_of_every_request(httpx_mock, langsys):
    """No explicit flush, no debounce and no exit hook: the request boundary is the send path."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/miss/?locale=es-ES")

    assert registered_phrases(httpx_mock) == ["A phrase the catalog has never seen"]
    assert langsys.has_pending is False


# -- SRV-3 ----------------------------------------------------------------------


def test_SRV3_registration_happens_only_after_the_response_is_complete(httpx_mock, langsys):
    """Order of events, not eventual collection: an inline flush collects too, and hands the
    visitor the latency."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/miss/?locale=es-ES")

    assert EVENTS == ["rendered", "response-returned", "posted"]


def test_SRV3_a_streamed_body_is_complete_before_its_misses_are_sent(httpx_mock, langsys):
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    serve_catalogs(httpx_mock, {"it-it": {"UI": {"Pricing": "Prezzi"}}})
    accept_items(httpx_mock)

    response = Client().get("/stream/?locale=it-IT")
    b"".join(response.streaming_content)

    assert EVENTS == ["response-returned", "body-complete", "posted"]
    assert registered_phrases(httpx_mock) == ["Streamed and never seen"]


def test_SRV3_the_core_debounce_never_sends_before_the_response_is_complete(
    httpx_mock, langsys_debounced
):
    """The settings-built client runs the core's default debounce. A render that runs on past it
    must still not have its miss sent before the response is complete: the request's scope holds
    it until the response is closed."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)
    SCOPES["posted"] = threading.Event()

    Client().get("/slow-miss/?locale=es-ES")

    assert EVENTS == ["rendered", "response-returned", "posted"]


def test_SRV3_another_requests_flush_never_sends_a_render_still_in_progress(httpx_mock, langsys):
    """A miss recorded inside a request's scope is sent by no flush before that request's response is
    complete, including the post-response flush of a concurrent request, which would otherwise
    drain the process-wide queue."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items_naming_phrases(httpx_mock)
    SCOPES.update(recorded=threading.Event(), release=threading.Event())

    held = threading.Thread(target=lambda: Client().get("/held-render/?locale=es-ES"))
    held.start()
    if not SCOPES["recorded"].wait(timeout=5):
        raise RuntimeError("the held request never recorded its miss")
    Client().get("/miss-again/?locale=es-ES")  # starts and finishes while the first still renders
    SCOPES["release"].set()
    held.join(timeout=10)

    held_miss = "posted:Recorded inside a request that is still rendering"
    if "posted:A second unseen phrase" not in EVENTS:
        raise RuntimeError(f"control: the finished request sent nothing: {EVENTS}")
    assert held_miss in EVENTS, "the held request's miss was never sent"
    held_response = len(EVENTS) - 1 - EVENTS[::-1].index("response-returned")
    assert EVENTS.index(held_miss) > held_response, EVENTS


def test_SRV3_a_streamed_body_holds_its_misses_until_it_is_complete(httpx_mock, langsys_debounced):
    """A streamed body renders after the middleware has returned, so the request's scope outlives
    ``__call__`` and ends only when the response is closed."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)
    SCOPES["posted"] = threading.Event()

    response = Client().get("/slow-stream/?locale=es-ES")
    b"".join(response.streaming_content)

    assert EVENTS == ["response-returned", "body-complete", "posted"]


def test_SRV3_a_view_that_raises_still_releases_its_misses(httpx_mock, langsys):
    """With no response to close, the scope ends when the exception leaves the middleware, so
    the request's misses go out with the next flush instead of waiting for the process to exit."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    def failing_view(request: HttpRequest) -> HttpResponse:
        t("Recorded before the view failed", "UI")
        raise RuntimeError("the view failed")

    with pytest.raises(RuntimeError):
        LangsysMiddleware(failing_view)(RequestFactory().get("/?locale=es-ES"))
    langsys.flush_pending()

    assert registered_phrases(httpx_mock) == ["Recorded before the view failed"]


def test_SRV3_a_read_only_key_pushes_nothing(httpx_mock, langsys):
    httpx_mock.add_response(url=AUTH, json=authorize("read", write_enabled=False), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/miss/?locale=es-ES")

    assert posts(httpx_mock) == [], "a read-only key pushed misses"


def test_SRV3_control_a_write_key_on_the_same_render_pushes(httpx_mock, langsys):
    """Without this the read-only assertion passes against a binding that never pushes."""
    httpx_mock.add_response(url=AUTH, json=authorize("write", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/miss/?locale=es-ES")

    assert len(posts(httpx_mock)) == 1, (
        "the control did not push, so the read-only test proves nothing"
    )


# -- BIND-2 / GATE-2 ------------------------------------------------------------


def test_BIND2_capability_unknown_holds_the_queue_through_the_binding(httpx_mock, langsys):
    """Reviewer finding 1, measured at 34a6a87: with authorize unreachable the core holds the
    queue, while the middleware's own ``can_write`` branch collapsed unknown to "no" and
    discarded it with zero POSTs. The decision is the core's; the binding must not take it."""
    httpx_mock.add_exception(
        httpx.ConnectError("authorize unreachable"), url=AUTH, is_reusable=True
    )
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/miss/?locale=es-ES")

    assert posts(httpx_mock) == []
    assert langsys.pending_phrases == [
        {"phrase": "A phrase the catalog has never seen", "category": "UI"}
    ], "the queue was discarded because capability could not be determined"


# -- GATE-3 ---------------------------------------------------------------------


def test_GATE3_an_observed_write_decision_does_not_survive_the_request(httpx_mock, langsys):
    """``key_type`` and ``write_enabled`` disagree on purpose. A plain key on warm metadata is
    the one shape in which the core consults a decision it observed earlier, so it is the only
    shape in which a latched decision is visible at the send site — the same discriminating
    vector the core's GATE-1 pair uses."""
    httpx_mock.add_response(url=AUTH, json=authorize("read", write_enabled=True), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)
    web = Client()

    web.get("/miss/?locale=es-ES")
    assert len(posts(httpx_mock)) == 1, (
        "control: the first request's live decision was true and it sent"
    )

    web.get("/miss-again/?locale=es-ES")
    assert len(posts(httpx_mock)) == 1, "the second request acted on the first request's decision"


def test_GATE3_the_unusable_capability_notice_rearms_at_each_request(httpx_mock, langsys, caplog):
    """An ``ip_write`` key from a non-allow-listed address: a legitimate server answer. OBS-1
    fires once per session; the request boundary is what makes each request a new session."""
    httpx_mock.add_response(
        url=AUTH, json=authorize("ip_write", write_enabled=False), is_reusable=True
    )
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    web = Client()

    with caplog.at_level(logging.WARNING, logger="langsys"):
        web.get("/miss/?locale=es-ES")
        web.get("/miss-again/?locale=es-ES")

    notices = [r for r in caplog.records if "NOT write-enabled" in r.getMessage()]
    assert len(notices) == 2, "the second request inherited the first request's notice state"


# -- BIND-4 ---------------------------------------------------------------------


def test_BIND4_no_binding_setting_switches_registration_off(httpx_mock, langsys, settings):
    """``AUTO_FLUSH`` was a binding-only key that turned discovery off — product behaviour
    decided one layer too high. Setting it must now change nothing."""
    settings.LANGSYS = {**settings.LANGSYS, "AUTO_FLUSH": False}
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/miss/?locale=es-ES")

    assert len(posts(httpx_mock)) == 1


# -- SRV-1 ----------------------------------------------------------------------


def test_SRV1_served_bytes_carry_the_request_locale(httpx_mock, langsys):
    """The absent phrase in the same render is the control: it emits the base language and is
    registered, which separates this from a catalog that happened to be complete."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    serve_catalogs(httpx_mock, {"it-it": {"UI": {"Pricing": "Prezzi"}}})
    accept_items(httpx_mock)

    response = Client().get("/render/?locale=it-IT")

    assert response.content == b"Prezzi|Not in the catalog"
    assert registered_phrases(httpx_mock) == ["Not in the catalog"]


def test_SRV1_a_streamed_render_carries_the_request_locale(httpx_mock, langsys):
    """A streamed body is rendered while the server iterates it, after the middleware has
    returned — the path on which a request-scoped locale is easiest to lose."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    serve_catalogs(httpx_mock, {"it-it": {"UI": {"Pricing": "Prezzi"}}})
    accept_items(httpx_mock)

    response = Client().get("/stream/?locale=it-IT")

    assert b"".join(response.streaming_content) == b"Prezzi|Streamed and never seen"


# -- SRV-2 ----------------------------------------------------------------------


def test_SRV2_concurrent_requests_never_see_each_others_locale(httpx_mock, langsys):
    """Run sequentially this proves nothing; the failure is the interleave, so the barriers pin
    it. Each request's second translation runs after both requests have set their locale and
    before either has returned and reset it. A single barrier was measured green against a
    process-global locale: the request that finished first restored the other's value on its
    way out, so the shared value was never observed."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    serve_catalogs(
        httpx_mock,
        {"it-it": {"UI": {"Pricing": "Prezzi"}}, "de-de": {"UI": {"Pricing": "Preise"}}},
    )
    INTERLEAVE.update({"locales-set": threading.Barrier(2), "translated": threading.Barrier(2)})
    bodies: dict[str, bytes] = {}

    def fetch(locale: str) -> None:
        bodies[locale] = Client().get(f"/interleave/?locale={locale}").content

    threads = [threading.Thread(target=fetch, args=(locale,)) for locale in ("it-IT", "de-DE")]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    INTERLEAVE.update({"locales-set": None, "translated": None})

    assert bodies == {"it-IT": b"Prezzi|Prezzi", "de-DE": b"Preise|Preise"}


# -- GATE-7 ---------------------------------------------------------------------


def test_GATE7_every_entry_point_reaches_the_core_queue(httpx_mock, langsys):
    """The binding exposes three ways to meet unregistered content — the tag, the filter and the
    helper — and a path that fed no lane would be invisible. Each must reach the core's
    detection; which lane it then feeds is the core's decision."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {}}), is_reusable=True)
    accept_items(httpx_mock)

    Client().get("/every-entry-point/?locale=es-ES")

    assert sorted(registered_phrases(httpx_mock)) == [
        "From the filter",
        "From the helper",
        "From the tag",
    ]
