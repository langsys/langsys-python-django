"""``{% translate %}`` and ``{% blocktranslate %}``, served by Langsys (spec MIG-8).

A drop-in for Django's ``i18n`` library while an app moves from gettext to Langsys: change
``{% load i18n %}`` to ``{% load langsys_i18n %}`` and the tags stay as they are, ``trans`` and
``blocktrans`` included. Django's own parser reads them, so every option Django accepts —
``context``, ``noop``, ``as``/``asvar``, ``with``, ``count``/``{% plural %}``, ``trimmed`` — means
what it means in Django; only the lookup is Langsys's, through the core's ``translate_legacy``.

* ``{% translate %}`` is a ``gettext`` call: it fills nothing, so its text registers as written,
  and its ``context`` is the category.
* ``{% blocktranslate %}`` fills every ``{{ name }}`` in its block, so each becomes a ``{name}``
  marker, filled with the value Django would print, escaped as Django escapes it. A block with
  ``{% plural %}`` is an ``ngettext`` call in the form Django itself looks up (``%(name)s``), with
  its counter as the plural's ``count``.

As in Django, a block's own text is printed as translated, and a ``{% translate %}`` of a
variable is escaped.
"""

from __future__ import annotations

import copy
from decimal import Decimal
from typing import Any

from django import template
from django.template.base import Parser, Token, TokenType
from django.template.context import Context
from django.templatetags import i18n
from django.utils.safestring import SafeString, mark_safe
from django.utils.translation import trim_whitespace

from ..client import get_client

register = template.Library()


class TranslateNode(i18n.TranslateNode):
    def __init__(self, parsed: i18n.TranslateNode) -> None:
        super().__init__(
            parsed.filter_expression, parsed.noop, parsed.asvar, parsed.message_context
        )
        # The source text is read untranslated; Langsys translates it.
        self.filter_expression.var.translate = False

    def render(self, context: Context) -> str:
        text = self.filter_expression.var.resolve(context)
        if not self.noop:
            category = self.message_context.resolve(context) if self.message_context else None
            text = get_client().translate_legacy(str(text), category=category)
        expression = copy.copy(self.filter_expression)
        expression.is_var, expression.var = False, text
        value = str(template.base.render_value_in_context(expression.resolve(context), context))
        is_safe = isinstance(value, SafeString)
        value = value.replace("%%", "%")
        value = mark_safe(value) if is_safe else value
        if self.asvar:
            context[self.asvar] = value
            return ""
        return value


class BlockTranslateNode(i18n.BlockTranslateNode):
    def __init__(self, parsed: i18n.BlockTranslateNode) -> None:
        super().__init__(
            parsed.extra_context,
            parsed.singular,
            parsed.plural,
            parsed.countervar,
            parsed.counter,
            parsed.message_context,
            parsed.trimmed,
            parsed.asvar,
            parsed.tag_name,
        )

    def render(self, context: Context, nested: bool = False) -> str:
        category = self.message_context.resolve(context) if self.message_context else None
        context.update({var: val.resolve(context) for var, val in self.extra_context.items()})
        if self.plural and self.countervar and self.counter:
            count = self.counter.resolve(context)
            if not isinstance(count, (Decimal, float, int)):
                return self._not_a_number(context)
            context[self.countervar] = count
            singular, names = self.render_token_list(self.singular)
            plural, plural_names = self.render_token_list(self.plural)
            values = self._values(context, [*names, *plural_names])
            values["count"] = count
            context.pop()
            result = get_client().translate_legacy(
                singular, entry_point="ngettext", plural=plural, category=category, params=values
            )
        else:
            text, names = self._as_written(self.singular)
            values = self._values(context, names)
            context.pop()
            result = get_client().translate_legacy(
                text, entry_point="blocktranslate", category=category, params=values or None
            )
        if self.asvar:
            context[self.asvar] = SafeString(result)
            return ""
        return result

    def _as_written(self, tokens: list[Any]) -> tuple[str, list[str]]:
        """The block's text as the template writes it, ``{{ name }}`` and all."""
        parts, names = [], []
        for token in tokens:
            if token.token_type == TokenType.TEXT:
                parts.append(token.contents)
            elif token.token_type == TokenType.VAR:
                parts.append("{{ %s }}" % token.contents)
                names.append(token.contents)
        text = "".join(parts)
        return (trim_whitespace(text) if self.trimmed else text), names

    def _values(self, context: Context, names: list[str]) -> dict[str, Any]:
        """Each filled name's value as Django prints it into the block."""
        default = context.template.engine.string_if_invalid

        def value(name: str) -> Any:
            if name in context:
                found = context[name]
            else:
                found = default % name if "%s" in default else default
            return template.base.render_value_in_context(found, context)

        return {name: value(name) for name in names}

    def _not_a_number(self, context: Context) -> str:
        """Django's own refusal of a count that is not a number, left to Django's node."""
        context.pop()
        return str(super().render(context))


def _translate(parser: Parser, token: Token) -> TranslateNode:
    return TranslateNode(i18n.do_translate(parser, token))


def _block_translate(parser: Parser, token: Token) -> BlockTranslateNode:
    return BlockTranslateNode(i18n.do_block_translate(parser, token))


for _name in ("translate", "trans"):
    register.tag(_name, _translate)
for _name in ("blocktranslate", "blocktrans"):
    register.tag(_name, _block_translate)
