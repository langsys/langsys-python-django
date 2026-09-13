"""WIRE-5 — the API base is redirectable to a test double through this binding's settings.

The seam an integrator reads is ``LANGSYS["API_URL"]`` (README, settings reference). It is read
when the shared client is first built, so a change made afterwards is too late until
``reset_client()``. That is the silent late-override failure the rule names, proven here by
redirecting too late and observing where the request went.
"""

from __future__ import annotations

import re
from typing import Any

import pytest
from langsys.cache import MemoryCache

from langsys_django import get_client, reset_client

CATALOG = {"status": True, "words": 0, "untranslatedWords": 0, "data": {"UI": {"Save": "Guardar"}}}
BASE = {"API_KEY": "k", "PROJECT_ID": "proj-1", "BASE_LOCALE": "en-US"}


@pytest.fixture(autouse=True)
def _settings_built_client(monkeypatch):
    """The settings-built client uses the core's default file cache; keep these tests off disk."""
    monkeypatch.setattr("langsys.client.FileCache", MemoryCache)
    reset_client()
    yield
    reset_client()


def hosts(httpx_mock: Any) -> list[str]:
    return [request.url.host for request in httpx_mock.get_requests()]


def test_WIRE5_API_URL_sends_the_client_to_the_double(httpx_mock, settings):
    settings.LANGSYS = {**BASE, "API_URL": "https://double.test/api"}
    httpx_mock.add_response(url=re.compile(r"https://double\.test/api/translations"), json=CATALOG)

    assert get_client().translate("Save", category="UI", locale="es-ES") == "Guardar"
    assert hosts(httpx_mock) == ["double.test"]


def test_WIRE5_a_redirect_after_the_client_is_built_is_too_late_until_reset(httpx_mock, settings):
    settings.LANGSYS = {**BASE, "API_URL": "https://first.test/api"}
    httpx_mock.add_response(
        url=re.compile(r"https://(first|second)\.test/api/translations"),
        json=CATALOG,
        is_reusable=True,
    )

    get_client().get_translations("es-ES", use_cache=False)
    settings.LANGSYS = {**BASE, "API_URL": "https://second.test/api"}
    get_client().get_translations("es-ES", use_cache=False)  # too late: the client is built
    reset_client()
    get_client().get_translations("es-ES", use_cache=False)

    assert hosts(httpx_mock) == ["first.test", "first.test", "second.test"]
