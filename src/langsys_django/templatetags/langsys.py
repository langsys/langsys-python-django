"""Template tag + filter for translating in Django templates.

``{% load langsys %}`` then::

    {% t "Save" %}
    {% t "Save" "UI" %}
    {% t "Hello, {name}!" "Greetings" name=user.get_full_name %}
    {% t "Save" "UI" as label %}

    {{ "Save"|t }}
    {{ "Save"|t:"UI" }}

Output is auto-escaped by Django like any template variable. A keyword argument whose variable
does not exist reaches the SDK as missing rather than as ``""``, so its interpolation recovery
shows the gap (``{name}``) instead of an empty value or the raw ICU source.
"""

from __future__ import annotations

from inspect import getfullargspec
from typing import Any, Optional

from django import template
from django.template.library import SimpleNode, parse_bits

from ..client import t as _translate

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
