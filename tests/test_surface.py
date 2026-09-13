"""BIND-6 — the public surface is a subset of the core's, plus Django idioms."""

from __future__ import annotations

from langsys import LangsysClient
from langsys.cache import MemoryCache

import langsys_django
from langsys_django.client import reset_client, set_client


def test_BIND6_public_names_are_django_idioms_over_core_values():
    """A new behaviour name here is the smell the rule names. ``t`` mirrors the core's own alias;
    the rest hold the core instance or the request locale."""
    assert sorted(langsys_django.__all__) == [
        "get_client",
        "get_current_locale",
        "reset_client",
        "set_client",
        "set_current_locale",
        "t",
    ]
    assert LangsysClient.t is LangsysClient.translate


def test_BIND6_the_core_is_reachable_by_reference_not_through_a_wrapper():
    core = LangsysClient(
        "k",
        "proj-1",
        api_url="https://api.test/api",
        cache=MemoryCache(),
        debounce=None,
        auto_flush=False,
    )
    set_client(core)
    try:
        assert langsys_django.get_client() is core
    finally:
        reset_client()
