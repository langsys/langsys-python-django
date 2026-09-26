"""SRV-6: which locale a request is served in, and what the response says it varied on.

Where Django's ``LocaleMiddleware`` resolved the request's language, that is the locale served,
mapped to the project's form by the core, and the SDK adds nothing to ``Vary``. Where nothing
resolved it, the middleware hands the core its candidates (the URL, the app's locale cookie, the
``Accept-Language`` header) and serves what ``resolve_request_locale`` chooses, validated against
the project's own locales from authorization, with the ``Vary`` headers that choice depended on.
The binding never writes a cookie.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from django.http import HttpRequest, HttpResponse
from django.test import Client
from django.urls import path
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django.client import reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource, get_current_locale

pytestmark = pytest.mark.urls("tests.test_request_locale")

AUTH = re.compile(r"https://api\.test/api/authorize-project/")
SERVED: dict[str, str] = {}


def where_am_i(request: HttpRequest) -> HttpResponse:
    SERVED["locale"] = get_current_locale()
    return HttpResponse("ok")


urlpatterns = [path("where/", where_am_i), path("it/where/", where_am_i)]


@pytest.fixture()
def langsys(settings, httpx_mock):
    settings.MIDDLEWARE = ["langsys_django.middleware.LangsysMiddleware"]
    httpx_mock.add_response(
        url=AUTH,
        json={
            "status": True,
            "data": {
                "id": "proj-1",
                "title": "T",
                "base_locale": "en-us",
                "target_locales": ["es-es", "it-it", "de-de"],
                "default_locales": {"es": "es-es", "it": "it-it", "de": "de-de"},
                "key_type": "write",
                "write_enabled": True,
                "langsys_settings": {"translatable_items": {"batch_limit": 200}},
            },
        },
        is_reusable=True,
    )
    SERVED.clear()
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


def visit(url: str, *, cookie: str = "", accept_language: str = "") -> Any:
    visitor = Client()
    if cookie:
        visitor.cookies["langsys_locale"] = cookie
    extra = {"HTTP_ACCEPT_LANGUAGE": accept_language} if accept_language else {}
    return visitor.get(url, **extra)


def vary(response: Any) -> set[str]:
    return {part.strip() for part in response.get("Vary", "").split(",") if part.strip()}


def served() -> str:
    return SERVED["locale"].lower()


def test_SRV6_the_url_wins_and_needs_no_vary(langsys):
    response = visit("/where/?locale=it-IT", cookie="de-DE", accept_language="es-ES")

    assert served() == "it-it"
    assert vary(response) == set(), "the URL is already the cache key"


def test_SRV6_a_cookie_beats_the_header_and_varies_on_cookie(langsys):
    response = visit("/where/", cookie="it-IT", accept_language="de-DE")

    assert served() == "it-it"
    assert vary(response) == {"Cookie"}


def test_SRV6_the_header_decides_last_and_varies_on_it(langsys):
    response = visit("/where/", accept_language="de-DE,en;q=0.5")

    assert served() == "de-de"
    assert "Accept-Language" in vary(response)


def test_SRV6_an_unsupported_cookie_falls_through_and_is_never_rewritten(langsys):
    response = visit("/where/", cookie="fr-FR", accept_language="de-DE")

    assert served() == "de-de"
    assert "langsys_locale" not in response.cookies, "a candidate is never written back"
    assert "Accept-Language" in vary(response)


def test_SRV6_an_unsupported_url_locale_falls_through(langsys):
    visit("/where/?locale=fr-FR", accept_language="it-IT")

    assert served() == "it-it"


def test_SRV6_with_no_usable_candidate_the_base_locale_is_served(langsys):
    response = visit("/where/")

    assert served() == "en-us"
    assert {"Accept-Language", "Cookie"} <= vary(response)


def test_SRV6_no_resolution_writes_a_cookie(langsys):
    """An explicit ``?locale=`` is a URL locale, not a choice this binding stores."""
    response = visit("/where/?locale=es-ES")

    assert served() == "es-es"
    assert "langsys_locale" not in response.cookies


@pytest.mark.urls("tests.urls_i18n")
def test_SRV6_an_i18n_patterns_prefix_is_a_url_locale(langsys, settings):
    """Routing by prefix needs Django's own ``LocaleMiddleware``; this middleware reads the prefix
    whichever order the two are listed in."""
    settings.MIDDLEWARE = [
        "django.middleware.locale.LocaleMiddleware",
        "langsys_django.middleware.LangsysMiddleware",
    ]
    response = visit("/it/where/", cookie="de-DE", accept_language="es-ES")

    assert response.status_code == 200
    assert served() == "it-it"
    assert "Cookie" not in vary(response)


def test_control_a_path_that_starts_like_a_locale_is_not_one_without_i18n_patterns(langsys):
    visit("/it/where/", accept_language="de-DE")

    assert served() == "de-de"


# -- the locale Django resolved ---------------------------------------------------------------


@pytest.fixture()
def django_locale(langsys, settings):
    settings.MIDDLEWARE = [
        "django.middleware.locale.LocaleMiddleware",
        "langsys_django.middleware.LangsysMiddleware",
    ]
    settings.LANGUAGES = [
        ("en", "English"),
        ("es", "Spanish"),
        ("es-es", "Spain"),
        ("fr", "French"),
    ]


def framework_visit(language: str, **candidates: Any) -> Any:
    visitor = Client()
    visitor.cookies["django_language"] = language
    return visit_with(visitor, **candidates)


def visit_with(
    visitor: Client, *, url: str = "/where/", cookie: str = "", accept_language: str = ""
):
    if cookie:
        visitor.cookies["langsys_locale"] = cookie
    extra = {"HTTP_ACCEPT_LANGUAGE": accept_language} if accept_language else {}
    return visitor.get(url, **extra)


def test_SRV6_the_locale_django_resolved_wins_and_the_sdk_adds_no_vary(django_locale):
    response = framework_visit(
        "es-es", url="/where/?locale=it-IT", cookie="de-DE", accept_language="de-DE"
    )

    assert served() == "es-es"
    assert vary(response) == {"Accept-Language"}, "only what LocaleMiddleware itself varies on"


def test_SRV6_a_bare_language_from_django_is_the_projects_default_locale_for_it(django_locale):
    framework_visit("es", cookie="it-IT")

    assert served() == "es-es"


def test_SRV6_a_django_locale_the_project_does_not_serve_is_served_as_the_base(django_locale):
    framework_visit("fr", url="/where/?locale=it-IT")

    assert served() == "en-us"
