"""GATE-10, the producing half: a page rendered in a non-base locale says so on its root.

``{% t %}`` prints translated text with no element of its own, so the layout's root is where the
page states the fact, through ``{% langsys_resolved %}``. A browser SDK loaded on the page then
records nothing inside it; a base-locale render stays unmarked, because it is source.

The same marker scopes MARK-3 when the app passes its page through the core's
``translate_page``: a ``data-ls-contentblock`` host inside a marked page registers nothing, and one
in an unmarked page registers its content under its id. The binding's own tags stamp no identity
host.
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from django.http import HttpRequest, HttpResponse
from django.template import Context, RequestContext, Template
from django.test import Client
from django.urls import path
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django.client import get_client, reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource

pytestmark = [
    pytest.mark.urls("tests.test_resolved_marker"),
    pytest.mark.httpx_mock(assert_all_responses_were_requested=False),
]

AUTH = re.compile(r"https://api\.test/api/authorize-project/")
TRANS = re.compile(r"https://api\.test/api/translations")
LAYOUT = '{% load langsys %}<html {% langsys_resolved %}><p>{% t "Pricing" "UI" %}</p></html>'


BLOCK_LAYOUT = (
    "{% load langsys %}<html {% langsys_resolved %}><body>"
    '<div data-ls-contentblock="promo"><p>Hello</p></div></body></html>'
)
ITEMS = re.compile(r"https://api\.test/api/translatable-items")
#: Every identity attribute MARK-1..4 define, in both spellings.
IDENTITY = re.compile(r"data-(?:ls|langsys)-(?:phrase|contentblock|category)\b")


def page(request: HttpRequest) -> HttpResponse:
    return HttpResponse(Template(LAYOUT).render(RequestContext(request)))


def block_page(request: HttpRequest) -> HttpResponse:
    """A layout carrying a stamped block, passed through the core's page translation."""
    html = Template(BLOCK_LAYOUT).render(RequestContext(request))
    return HttpResponse(get_client().translate_page(html, category="UI"))


urlpatterns = [path("page/", page), path("block-page/", block_page)]


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


# -- MARK-3: the marker scopes a stamped block; the binding stamps none -------------------------


def _registered_blocks(httpx_mock) -> list[str]:
    ids = []
    for request in httpx_mock.get_requests(url=ITEMS):
        for item in json.loads(request.content)["translatable_items"]:
            if item.get("custom_id"):
                ids.append(item["custom_id"])
    return ids


def test_MARK3_a_stamped_block_in_a_marked_page_registers_nothing(httpx_mock, langsys):
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)

    body = Client().get("/block-page/?locale=it-IT").content.decode()

    assert "data-ls-resolved" in body, "control: the page was marked"
    assert _registered_blocks(httpx_mock) == []


def test_MARK3_the_same_block_in_an_unmarked_page_registers_under_its_id(httpx_mock, langsys):
    """The control: without the binding's marker, the core registers the block it lacks."""
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    httpx_mock.add_response(url=ITEMS, json={"status": True}, is_reusable=True)

    body = Client().get("/block-page/").content.decode()

    assert "data-ls-resolved" not in body, "control: a base-locale page is unmarked"
    assert _registered_blocks(httpx_mock) == ["promo"]


def test_MARK3_the_bindings_tags_stamp_no_identity_host(httpx_mock, langsys):
    httpx_mock.add_response(url=AUTH, json=authorize(), is_reusable=True)
    source = (
        "{% load langsys langsys_i18n %}<html {% langsys_resolved %}>"
        '{% t "Pricing" "UI" %}{{ "Pricing"|t:"UI" }}{% translate "Save" %}'
        "{% blocktranslate %}Hi {{ name }}{% endblocktranslate %}</html>"
    )

    def render(request: HttpRequest) -> str:
        return Template(source).render(RequestContext(request, {"name": "Ada"}))

    from django.test import RequestFactory

    from langsys_django.middleware import LangsysMiddleware

    out = LangsysMiddleware(lambda request: HttpResponse(render(request)))(
        RequestFactory().get("/?locale=it-IT")
    ).content.decode()

    assert "Prezzi" in out and "data-ls-resolved" in out, "control: a translated, marked render"
    assert not IDENTITY.search(out)
