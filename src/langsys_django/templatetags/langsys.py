"""Template tag + filter for translating in Django templates.

``{% load langsys %}`` then::

    {% t "Save" %}
    {% t "Save" "UI" %}
    {% t "Hello, {name}!" "Greetings" name=user.get_full_name %}
    {% t "Save" "UI" as label %}

    {{ "Save"|t }}
    {{ "Save"|t:"UI" }}

    <html {% langsys_resolved %}>

    {% for entry in form|langsys_errors %}{% t_message entry %}{% endfor %}

Output is auto-escaped by Django like any template variable. A keyword argument whose variable
does not exist reaches the SDK as missing rather than as ``""``, so its interpolation recovery
shows the gap (``{name}``) instead of an empty value or the raw ICU source.
"""

from __future__ import annotations

from inspect import getfullargspec
from typing import Any, Optional

from django import template
from django.template.library import SimpleNode, parse_bits
from django.utils.safestring import SafeString
from langsys import canonicalize_locale
from langsys.exceptions import ApiError, ConfigurationError, NetworkError

from ..client import get_client
from ..client import t as _translate
from ..locale import get_current_locale
from ..messages import entries_from_form

register = template.Library()


def t_tag(phrase: str, category: Optional[str] = None, **params: Any) -> str:
    return _translate(phrase, category, **params)


class _TranslateNode(SimpleNode):
    """``simple_tag``'s node, except that a keyword argument whose variable does not exist
    resolves to ``None`` rather than Django's ``string_if_invalid``.

    Django's default turns an absent argument into a present, empty one, and the SDK's
    interpolation recovery engages only for an absent one — so passing Django's value through
    would narrow a guarantee the SDK makes. Filters still apply (``name=x|default:"friend"``),
    and the phrase and category resolve exactly as ``simple_tag`` resolves them.
    """

    def get_resolved_arguments(self, context: Any) -> tuple[list[Any], dict[str, Any]]:
        resolved_args = [var.resolve(context) for var in self.args]
        resolved_kwargs = {
            name: var.resolve(context, ignore_failures=True) for name, var in self.kwargs.items()
        }
        return resolved_args, resolved_kwargs


_SIGNATURE = getfullargspec(t_tag)


@register.tag(name="t")
def do_t(parser: Any, token: Any) -> _TranslateNode:
    """Compiled exactly as ``simple_tag`` compiles, including the ``as <var>`` form."""
    bits = token.split_contents()[1:]
    target_var = None
    if len(bits) >= 2 and bits[-2] == "as":
        target_var = bits[-1]
        bits = bits[:-2]
    args, kwargs = parse_bits(
        parser,
        bits,
        _SIGNATURE.args,
        _SIGNATURE.varargs,
        _SIGNATURE.varkw,
        _SIGNATURE.defaults,
        _SIGNATURE.kwonlyargs,
        _SIGNATURE.kwonlydefaults,
        False,
        "t",
    )
    return _TranslateNode(t_tag, False, args, kwargs, target_var)


@register.filter(name="t")
def t_filter(phrase: str, category: Optional[str] = None) -> str:
    return _translate(phrase, category)


@register.simple_tag(name="langsys_resolved")
def resolved_marker() -> SafeString:
    """A bare ``data-ls-resolved`` for the element a layout puts it on, usually ``<html>``, when
    this request renders in a locale other than the project's base (GATE-10). Presence is what a
    reader acts on, and a bare marker means the same as one carrying the locale.

    Text ``{% t %}`` prints has no element of its own, so the layout's root is where a page states
    that it is already output in a resolved locale; a browser SDK loaded on the page then records
    nothing inside it rather than taking translated text for source. A render in the base locale is
    source and stays unmarked, as does one whose project locales cannot be read, because the SDK
    then serves the configured base locale.
    """
    locale = get_current_locale()
    if not locale:
        return SafeString("")
    try:
        base = get_client().project.base_locale
    except (NetworkError, ApiError, ConfigurationError):
        return SafeString("")
    if canonicalize_locale(locale) == canonicalize_locale(base):
        return SafeString("")
    return SafeString("data-ls-resolved")


@register.filter(name="langsys_errors")
def langsys_errors(form: Any) -> list[dict[str, Any]]:
    """A bound form's errors as server-message entries (MSG-9), for ``{% t_message %}``."""
    return entries_from_form(form, client=get_client())


@register.simple_tag(name="t_message")
def t_message(entry: dict[str, Any]) -> str:
    """Render an entry in the request's locale: its template's translation, filled from its params,
    or its ``message`` when the catalog has none (MSG-5). ``message`` is never a lookup key."""
    return get_client().render_server_message(entry)
