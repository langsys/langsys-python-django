"""Known gap: a placeholder named ``category`` or ``phrase`` cannot pass through ``t()`` or ``{% t %}``.

Measured first in the FastAPI lane and passed on by Reviewer on 838-django-push-to-100. This
binding's ``t(phrase, category=None, **params)`` shape takes both names for itself, while the
core's ``translate`` takes ``params`` as a dict and has no such collision. So the collision is this
binding narrowing a core guarantee (BIND-1).

These tests pin the behaviour as it is, not as it should be. The fix is one signature decision
across the core and both Python bindings, because a positional-only ``category`` would silently
reroute every existing ``t("Save", category="UI")`` call. When that decision lands these tests fail,
and they are replaced by tests of the chosen shape.
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Template
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django import get_client, t
from langsys_django.client import reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource, reset_current_locale, set_current_locale

# The raising tests never reach the catalog.
pytestmark = pytest.mark.httpx_mock(assert_all_responses_were_requested=False)

TRANS = re.compile(r"https://api\.test/api/translations")


@pytest.fixture()
def langsys(httpx_mock):
    httpx_mock.add_response(
        url=TRANS,
        json={"status": True, "words": 0, "untranslatedWords": 0, "data": {"UI": {}}},
        is_reusable=True,
    )
    reset_client()
    client = LangsysClient(
        "k",
        "proj-1",
        api_url="https://api.test/api",
        base_locale="en-US",
        cache=MemoryCache(),
        locale_source=ContextVarLocaleSource(),
        debounce=None,
        auto_flush=False,
    )
    set_client(client)
    token = set_current_locale("en-US")
    yield client
    reset_current_locale(token)
    reset_client()


def test_gap_a_category_keyword_is_taken_as_the_catalog_category(langsys):
    """The silent form: the placeholder is served unfilled, and the phrase queues under a
    category named after the value."""
    assert t("Browse {category}", category="Shoes") == "Browse {category}"
    assert langsys.pending_phrases == [{"phrase": "Browse {category}", "category": "Shoes"}]


def test_gap_the_tag_takes_a_category_keyword_as_the_catalog_category(langsys):
    rendered = Template('{% load langsys %}{% t "Browse {category}" category="Shoes" %}').render(
        Context({})
    )
    assert rendered == "Browse {category}"
    assert langsys.pending_phrases == [{"phrase": "Browse {category}", "category": "Shoes"}]


def test_gap_a_category_beside_a_category_keyword_raises(langsys):
    with pytest.raises(TypeError, match="multiple values for argument 'category'"):
        t("Browse {category}", "UI", category="Shoes")


def test_gap_in_a_template_the_collision_raises_while_rendering(langsys):
    """Not a ``TemplateSyntaxError`` when the template compiles: the page fails when it renders."""
    template = Template('{% load langsys %}{% t "Browse {category}" "UI" category="Shoes" %}')
    with pytest.raises(TypeError, match="multiple values for argument 'category'"):
        template.render(Context({}))


def test_gap_a_phrase_keyword_collides_too(langsys):
    with pytest.raises(TypeError, match="multiple values for argument 'phrase'"):
        t("Hi {phrase}!", "UI", phrase="Ada")


def test_workaround_the_core_takes_any_parameter_name(langsys):
    """The control, and the workaround the README documents: the core's ``params`` dict carries
    a placeholder of any name."""
    translated = get_client().translate(
        "Browse {category}", category="UI", params={"category": "Shoes"}
    )
    assert translated == "Browse Shoes"
