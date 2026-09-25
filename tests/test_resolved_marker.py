"""GATE-10, the producing half: a page rendered in a non-base locale says so on its root.

``{% t %}`` prints translated text with no element of its own, so the layout's root is where the
page states the fact, through ``{% langsys_resolved %}``. A browser SDK loaded on the page then
records nothing inside it; a base-locale render stays unmarked, because it is source.
"""

from __future__ import annotations

import re

import httpx
import pytest
from django.http import HttpRequest, HttpResponse
from django.template import Context, RequestContext, Template
from django.test import Client
from django.urls import path
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django.client import reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource

pytestmark = [
    pytest.mark.urls("tests.test_resolved_marker"),
    pytest.mark.httpx_mock(assert_all_responses_were_requested=False),
]

AUTH = re.compile(r"https://api\.test/api/authorize-project/")
TRANS = re.compile(r"https://api\.test/api/translations")
LAYOUT = '{% load langsys %}<html {% langsys_resolved %}><p>{% t "Pricing" "UI" %}</p></html>'


def page(request: HttpRequest) -> HttpResponse:
    return HttpResponse(Template(LAYOUT).render(RequestContext(request)))


urlpatterns = [path("page/", page)]


def authorize() -> dict:
    return {
        "status": True,
        "data": {
            "id": "proj-1",
            "title": "T",
            "base_locale": "en-us",
            "target_locales": ["it-it"],
            "default_locales": {},
            "key_type": "write",
            "write_enabled": True,
            "langsys_settings": {"translatable_items": {"batch_limit": 200}},
        },
    }


@pytest.fixture()
def langsys(settings, httpx_mock):
    settings.MIDDLEWARE = ["langsys_django.middleware.LangsysMiddleware"]
    httpx_mock.add_response(
        url=TRANS,
        json={
            "status": True,
            "words": 0,
            "untranslatedWords": 0,
            "data": {"UI": {"Pricing": "Prezzi"}},
        },
        is_reusable=True,
    )
    reset_client()
    set_client(
        LangsysClient(
            "k",
            "proj-1",
            api_url="https://api.test/api",
            base_locale="en-US",
            cache=MemoryCache(),
            locale_source=ContextVarLocaleSource(),
            debounce=None,
            auto_flush=False,
        )
    )
    yield
    reset_client()


def test_GATE10_a_render_in_a_non_base_locale_marks_its_root(httpx_mock, langsys):
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)

    body = Client().get("/page/?locale=it-IT").content.decode()

    assert body.startswith("<html data-ls-resolved>")


def test_GATE10_a_base_locale_render_stays_unmarked(httpx_mock, langsys):
    """The control: marking a base-locale page would hide exactly the text discovery exists to find."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)

    body = Client().get("/page/").content.decode()

    assert body.startswith("<html >")
    assert "data-ls-resolved" not in body


def test_GATE10_unreadable_project_locales_leave_the_page_unmarked(httpx_mock, langsys):
    """Without the project's locales the SDK serves its configured base locale, which is source."""
    httpx_mock.add_exception(
        httpx.ConnectError("authorize unreachable"), url=AUTH, is_reusable=True
    )

    response = Client().get("/page/?locale=it-IT")

    assert response.status_code == 200
    assert "data-ls-resolved" not in response.content.decode()


def test_GATE10_outside_a_request_the_marker_prints_nothing(langsys):
    assert Template("{% load langsys %}[{% langsys_resolved %}]").render(Context({})) == "[]"
