"""Template tag + filter for translating in Django templates.

``{% load langsys %}`` then::

    {% t "Save" %}
    {% t "Save" "UI" %}
    {% t "Hello, {name}!" "Greetings" name=user.get_full_name %}

    {{ "Save"|t }}
    {{ "Save"|t:"UI" }}

Output is auto-escaped by Django like any template variable.
"""

from __future__ import annotations

from typing import Any, Optional

from django import template

from ..client import t as _translate

register = template.Library()


@register.simple_tag(name="t")
def t_tag(phrase: str, category: Optional[str] = None, **params: Any) -> str:
    return _translate(phrase, category, **params)


@register.filter(name="t")
def t_filter(phrase: str, category: Optional[str] = None) -> str:
    return _translate(phrase, category)
