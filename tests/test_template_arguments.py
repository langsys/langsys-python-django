"""Template arguments reach the core with their absence intact.

Django resolves a template variable that does not exist to ``string_if_invalid`` — ``""`` by
default — so a tag that takes Django's resolved value hands the core an argument that is present
and empty. The core's interpolation recovery (ICU-1 to ICU-3) engages only for an argument that
is absent or null, so that conversion is the binding narrowing a core guarantee (BIND-1).

Measured at 34a6a87 through ``{% t %}``: an undefined variable arrived as ``{'name': ''}``, a
simple placeholder rendered ``Hi !`` where the core renders the visible gap ``Hi {name}!``, and a
plural rendered its raw ICU source — the outcome the ICU family exists to prevent.
"""

from __future__ import annotations

import re

import pytest
from django.template import Context, Template
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django.client import reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource, reset_current_locale, set_current_locale

TRANS = re.compile(r"https://api\.test/api/translations")


@pytest.fixture()
def langsys(httpx_mock):
    httpx_mock.add_response(
        url=TRANS,
        json={"status": True, "words": 0, "untranslatedWords": 0, "data": {"UI": {}}},
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
    token = set_current_locale("en-US")
    yield
    reset_current_locale(token)
    reset_client()


def render(source: str, **context: object) -> str:
    return Template("{% load langsys %}" + source).render(Context(context))


def test_ICU2_an_undefined_template_variable_is_an_absent_argument(langsys):
    assert render('{% t "Hi {name}!" "UI" name=no_such_variable %}') == "Hi {name}!"


def test_ICU3_a_plural_over_an_undefined_variable_recovers_instead_of_leaking_its_source(langsys):
    rendered = render('{% t "{n, plural, one {# item} other {# items}}" "UI" n=no_such_variable %}')
    assert rendered == "{n} items"


def test_ICU2_a_context_value_of_none_is_an_absent_argument(langsys):
    assert render('{% t "Hi {name}!" "UI" name=value %}', value=None) == "Hi {name}!"


def test_control_an_explicit_empty_string_is_still_a_value(langsys):
    """Absence and emptiness stay distinct. Without this, a tag that maps every empty string
    to ``None`` passes every test above."""
    assert render('{% t "Hi {name}!" "UI" name="" %}') == "Hi !"


def test_control_a_defined_variable_is_interpolated(langsys):
    assert render('{% t "Hi {name}!" "UI" name=value %}', value="Ada") == "Hi Ada!"


def test_filters_still_apply_to_an_undefined_variable(langsys):
    assert (
        render('{% t "Hi {name}!" "UI" name=no_such_variable|default:"friend" %}') == "Hi friend!"
    )


def test_the_as_form_still_assigns(langsys):
    assert render('{% t "Save" "UI" as label %}[{{ label }}]') == "[Save]"
