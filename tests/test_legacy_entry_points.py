"""MIG-8: Django's gettext entry points, served by Langsys through the core's one resolver.

``langsys_django.translation`` and the ``langsys_i18n`` template library are drop-ins for Django's
own names. A literal miss converts as Django treats it: only the placeholders the call fills
(MIG-2); a key in the app's ``.po`` source resolves to the same phrase and category ``t()`` gives
it. The ``calls`` rows of the shared ``mig-vectors.json`` for this core's Django entry points are
run here, through the binding's functions.
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import re

import httpx
import pytest
from django.template import Context, Template
from django.utils import translation as django_translation
from langsys import LangsysClient
from langsys.cache import MemoryCache

from langsys_django.client import get_client, reset_client, set_client, t
from langsys_django.locale import ContextVarLocaleSource, reset_current_locale, set_current_locale
from langsys_django.translation import (
    LegacyText,
    gettext,
    gettext_lazy,
    ngettext,
    ngettext_lazy,
    npgettext,
    pgettext,
)

pytestmark = pytest.mark.httpx_mock(assert_all_responses_were_requested=False)

VECTORS = pathlib.Path(__file__).parent / "fixtures" / "mig-vectors.json"
#: langsys-js-typescript tests/fixtures/mig-vectors.json, as the core at e834170 vendors it.
VECTORS_BLOB = "20f2bdd678cb33981e3064e42d43ca62783920ad"
DOC = json.loads(VECTORS.read_text(encoding="utf-8"))
#: The Django entry points among the core's own; `t` is the core's, and blocktranslate has no row.
DJANGO_ENTRY_POINTS = ("gettext", "ngettext", "pgettext")
ROWS = {row["id"]: row for row in DOC["calls"]}
DJANGO_ROWS = [row for row in DOC["calls"] if row["entry_point"] in DJANGO_ENTRY_POINTS]

CART_PO = r"""msgid ""
msgstr ""

msgctxt "cart"
msgid "%(count)s item"
msgid_plural "%(count)s items"
msgstr[0] ""
msgstr[1] ""

msgctxt "cart"
msgid "Remove"
msgstr ""
"""


def _mock_api(httpx_mock, catalog=None):
    httpx_mock.add_response(
        url=re.compile(r"https://api\.test/api/authorize-project/"),
        json={
            "status": True,
            "data": {
                "id": "proj-1",
                "title": "T",
                "base_locale": "en-us",
                "target_locales": ["es-es"],
                "default_locales": {"es": "es-es"},
                "key_type": "write",
                "write_enabled": True,
                "langsys_settings": {"translatable_items": {"batch_limit": 200}},
            },
        },
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=re.compile(r"https://api\.test/api/translations"),
        json={"status": True, "words": 0, "untranslatedWords": 0, "data": catalog or {}},
        is_reusable=True,
    )
    registered: list[tuple[str, str]] = []

    def accept(request):
        for item in json.loads(request.content)["translatable_items"]:
            registered.append((item["phrase"], item.get("category")))
        return httpx.Response(200, json={"status": True})

    httpx_mock.add_callback(
        accept, url=re.compile(r"https://api\.test/api/translatable-items"), is_reusable=True
    )
    return registered


def _client(legacy_files=None) -> LangsysClient:
    return LangsysClient(
        "k",
        "proj-1",
        api_url="https://api.test/api",
        base_locale="en-US",
        cache=MemoryCache(),
        locale_source=ContextVarLocaleSource(),
        debounce=None,
        auto_flush=False,
        legacy_files=legacy_files,
    )


class Langsys:
    """The shared client, its registrations, and the locale requests render in."""

    def __init__(self, httpx_mock, legacy_files=None, catalog=None, locale="es-ES"):
        self.registered = _mock_api(httpx_mock, catalog)
        reset_client()
        set_client(_client(legacy_files))
        self._token = set_current_locale(locale)

    def sent(self) -> list[tuple[str, str]]:
        get_client().flush_pending()
        return list(self.registered)

    def close(self) -> None:
        reset_current_locale(self._token)
        reset_client()


@pytest.fixture()
def langsys(httpx_mock):
    session = Langsys(httpx_mock)
    yield session
    session.close()


@pytest.fixture()
def cart_po(tmp_path):
    po = tmp_path / "django.po"
    po.write_text(CART_PO, encoding="utf-8")
    return po


def render(source: str, **context) -> str:
    return Template(source).render(Context(context))


def phrases(sent):
    return [phrase for phrase, _ in sent]


# -- the shared vectors -------------------------------------------------------------------------


def test_the_vendored_vectors_are_the_pinned_blob():
    content = VECTORS.read_bytes()
    blob = hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()

    assert blob == VECTORS_BLOB
    assert set(DJANGO_ENTRY_POINTS) <= set(DOC["core_entry_points"]["python"])
    assert {row["id"] for row in DJANGO_ROWS} == {
        "django-gettext-named",
        "django-ngettext-pair",
        "django-pgettext-context-is-category",
    }


def _call(row):
    text, params = row["text"], row.get("params") or {}
    if row["entry_point"] == "gettext":
        return gettext(text) % params
    if row["entry_point"] == "ngettext":
        return ngettext(text[0], text[1], params["count"]) % params
    return str(pgettext(row["context"], text))


@pytest.mark.parametrize("row", DJANGO_ROWS, ids=lambda row: row["id"])
def test_MIG2_django_entry_point_calls(langsys, row):
    _call(row)
    ((phrase, category),) = langsys.sent()

    assert phrase == row["expected"]
    if "category" in row:
        assert category == row["category"]
    if row.get("same_phrase_as"):
        other = ROWS[row["same_phrase_as"]]
        t(other["text"], **(other.get("params") or {}))
        assert phrases(langsys.sent())[-1] == phrase


# -- MIG-2: only what the call fills is converted -----------------------------------------------


def test_MIG2_a_placeholder_the_call_does_not_fill_stays_as_written(langsys):
    str(gettext("Hi %(name)s"))
    gettext("Hi %(name)s and %(other)s") % {"name": "Ada"}

    assert phrases(langsys.sent()) == ["Hi %(name)s", "Hi {name} and %(other)s"]


def test_MIG2_a_message_is_looked_up_once_its_values_are_known(langsys):
    greeting = gettext("Hello %(name)s")

    assert isinstance(greeting, LegacyText)
    assert langsys.sent() == [], "nothing is looked up before % says which names it fills"
    assert greeting % {"name": "Ada"} == "Hello Ada"
    assert phrases(langsys.sent()) == ["Hello {name}"]


def test_MIG2_a_message_with_nothing_to_fill_is_translated_text_at_once(httpx_mock):
    session = Langsys(httpx_mock, catalog={"__uncategorized__": {"Save": "Guardar"}})
    try:
        saved = gettext("Save")
        assert type(saved) is str
        assert saved == "Guardar"
    finally:
        session.close()


def test_MIG2_a_lazy_message_is_looked_up_in_the_locale_it_is_used_in(httpx_mock):
    session = Langsys(
        httpx_mock, catalog={"__uncategorized__": {"Save": "Guardar"}}, locale="en-US"
    )
    try:
        label = gettext_lazy("Save")
        token = set_current_locale("es-ES")
        try:
            assert str(label) == "Guardar"
        finally:
            reset_current_locale(token)
    finally:
        session.close()


def test_MIG2_a_plural_counts_from_the_call_or_from_the_key_it_names(langsys):
    assert ngettext("%(count)s item", "%(count)s items", 3) % {"count": 3} == "3 items"
    assert ngettext_lazy("%(num)d file", "%(num)d files", "num") % {"num": 1} == "1 file"

    assert phrases(langsys.sent()) == [
        "{count, plural, =1 {# item} other {# items}}",
        "{count, plural, =1 {{num} file} other {{num} files}}",
    ]


# -- MIG-8: one resolver for t() and Django's entry points --------------------------------------


@pytest.mark.parametrize(
    ("via_t", "via_django"),
    [
        (lambda: t("Remove"), lambda: str(gettext("Remove"))),
        (lambda: t("Remove"), lambda: str(pgettext("cart", "Remove"))),
        (
            lambda: t("%(count)s item", count=3),
            lambda: ngettext("%(count)s item", "%(count)s items", 3) % {"count": 3},
        ),
        (
            lambda: t("%(count)s item", count=3),
            lambda: npgettext("cart", "%(count)s item", "%(count)s items", 3) % {"count": 3},
        ),
        (
            lambda: t("Remove"),
            lambda: render('{% load langsys_i18n %}{% translate "Remove" context "cart" %}'),
        ),
        (
            lambda: t("%(count)s item", count=3),
            lambda: render(
                "{% load langsys_i18n %}{% blocktranslate count count=n %}{{ count }} item"
                "{% plural %}{{ count }} items{% endblocktranslate %}",
                n=3,
            ),
        ),
    ],
    ids=["gettext", "pgettext", "ngettext", "npgettext", "translate", "blocktranslate-count"],
)
def test_MIG8_t_and_the_django_entry_point_register_one_phrase_and_category(
    httpx_mock, cart_po, via_t, via_django
):
    session = Langsys(httpx_mock, legacy_files=[cart_po])
    try:
        via_t()
        through_t = session.sent()
        via_django()
        through_django = session.sent()[len(through_t) :]
    finally:
        session.close()

    assert through_t == through_django
    assert through_t[0][1] == "cart", "the .po entry's msgctxt is the category"


def test_MIG8_the_legacy_files_setting_reaches_the_core(settings, httpx_mock, cart_po):
    registered = _mock_api(httpx_mock)
    settings.LANGSYS = {
        "API_KEY": "k",
        "PROJECT_ID": "proj-1",
        "API_URL": "https://api.test/api",
        "LEGACY_FILES": [{"path": str(cart_po), "format": "gettext"}],
    }
    reset_client()
    token = set_current_locale("es-ES")
    try:
        t("Remove")
        get_client().flush_pending()
    finally:
        reset_current_locale(token)
        reset_client()

    assert registered == [("Remove", "cart")]


# -- the langsys_i18n template library ----------------------------------------------------------


def test_MIG8_translate_registers_its_text_as_written_under_its_context(langsys):
    out = render(
        '{% load langsys_i18n %}{% translate "Save" %}|{% trans "Save %(n)s" context "UI" %}'
    )

    assert out == "Save|Save %(n)s"
    assert langsys.sent() == [("Save", None), ("Save %(n)s", "UI")]


def test_MIG8_blocktranslate_fills_its_names_escaped_and_registers_markers(langsys):
    out = render(
        "{% load langsys_i18n %}"
        "{% blocktranslate with name=user trimmed %}\n  Hello {{ name }}\n{% endblocktranslate %}",
        user="<b>Ada</b>",
    )

    assert out == "Hello &lt;b&gt;Ada&lt;/b&gt;"
    assert phrases(langsys.sent()) == ["Hello {name}"]


def test_MIG8_blocktranslate_renders_its_translation(httpx_mock):
    session = Langsys(httpx_mock, catalog={"__uncategorized__": {"Hello {name}": "Hola {name}"}})
    try:
        out = render(
            "{% load langsys_i18n %}{% blocktrans %}Hello {{ name }}{% endblocktrans %}"
            "{% blocktranslate asvar greeting %}Hello {{ name }}{% endblocktranslate %}"
            "[{{ greeting }}]",
            name="Ada",
        )
    finally:
        session.close()

    assert out == "Hola Ada[Hola Ada]"


def test_MIG8_djangos_own_i18n_tags_and_functions_are_untouched(langsys):
    out = render('{% load i18n %}{% translate "Save" %}')
    plain = django_translation.gettext("Save")

    assert (out, plain) == ("Save", "Save")
    assert langsys.sent() == []
