import re

import pytest
from django.http import HttpResponse
from django.template import Context, Template
from django.test import RequestFactory
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django import t
from langsys_django.client import reset_client, set_client
from langsys_django.locale import (
    ContextVarLocaleSource,
    get_current_locale,
    reset_current_locale,
    set_current_locale,
)
from langsys_django.middleware import LangsysMiddleware

TRANS = re.compile(r"https://api\.test/api/translations")


def catalog(data):
    return {"status": True, "words": 0, "untranslatedWords": 0, "data": data}


@pytest.fixture()
def client():
    reset_client()
    instance = LangsysClient(
        "k",
        "proj-1",
        api_url="https://api.test/api",
        base_locale="en-US",
        cache=MemoryCache(),
        locale_source=ContextVarLocaleSource(),
    )
    set_client(instance)
    yield instance
    reset_client()


def test_t_helper_uses_request_locale(httpx_mock, client):
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {"Save": "Guardar"}}))
    token = set_current_locale("es-ES")
    try:
        assert t("Save", "UI") == "Guardar"
    finally:
        reset_current_locale(token)


def test_template_tag_and_filter(httpx_mock, client):
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {"Save": "Guardar"}, "Greetings": {}}))
    token = set_current_locale("es-ES")
    try:
        out = Template(
            '{% load langsys %}{% t "Save" "UI" %}|{{ "Save"|t:"UI" }}|'
            '{% t "Hi {name}" "Greetings" name="Ada" %}'
        ).render(Context({}))
    finally:
        reset_current_locale(token)
    assert out == "Guardar|Guardar|Hi Ada"


def test_template_output_is_escaped(httpx_mock, client):
    httpx_mock.add_response(url=TRANS, json=catalog({"UI": {"<b>x</b>": "<b>x</b>"}}))
    token = set_current_locale("es-ES")
    try:
        out = Template('{% load langsys %}{% t "<b>x</b>" "UI" %}').render(Context({}))
    finally:
        reset_current_locale(token)
    assert out == "&lt;b&gt;x&lt;/b&gt;"


def test_middleware_locale_from_query_and_cookie_persist(httpx_mock, client):
    seen = {}

    def view(request):
        seen["locale"] = get_current_locale()
        return HttpResponse("ok")

    response = LangsysMiddleware(view)(RequestFactory().get("/?locale=es-es"))
    assert seen["locale"] == "es-ES"  # canonicalized
    assert response.cookies["langsys_locale"].value == "es-ES"  # explicit choice persisted


def test_middleware_locale_from_accept_language(httpx_mock, client):
    seen = {}

    def view(request):
        seen["locale"] = get_current_locale()
        return HttpResponse("ok")

    req = RequestFactory().get("/", HTTP_ACCEPT_LANGUAGE="es-ES,en;q=0.5")
    response = LangsysMiddleware(view)(req)
    assert seen["locale"] == "es-ES"
    assert "langsys_locale" not in response.cookies  # detected, not an explicit choice


def test_middleware_resets_locale_after_request(httpx_mock, client):
    LangsysMiddleware(lambda r: HttpResponse("ok"))(RequestFactory().get("/?locale=es-ES"))
    assert get_current_locale() == ""  # context var reset
