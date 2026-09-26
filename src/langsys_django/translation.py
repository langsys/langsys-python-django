"""Django's gettext functions, served by Langsys (spec MIG-8).

For an app moving from gettext to Langsys, these are drop-in replacements for the functions of the
same names in ``django.utils.translation``: switch the import and the calls stay as they are::

    from langsys_django.translation import gettext as _

    _("Hello %(name)s") % {"name": user.name}

Each call goes through the core's ``translate_legacy``, the resolver ``t()`` uses. With
``LEGACY_FILES`` configured, the message is first a key in the app's source ``.po`` files, so a
message already there registers the same phrase, under the same category, as ``t()`` does for it.
Otherwise the message is literal source text, converted the way Django treats it (MIG-2): only
the placeholders the call fills become ``{name}`` markers, and a placeholder it leaves unfilled
is printed as written, by Django and here alike. A ``pgettext`` context is the phrase's category.

Django fills a message's placeholders after the call returns, with ``%``. So a message with named
placeholders comes back as a :class:`LegacyText`, which is looked up once ``%`` has said which
names it fills, or when it is used as text; a message with none comes back as the translated
``str``. The ``*_lazy`` forms always return a :class:`LegacyText`, looked up when used, in the
locale of that moment. Django's own translation functions are untouched.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Optional, Union

from django.utils.functional import Promise

from .client import get_client

__all__ = [
    "LegacyText",
    "gettext",
    "gettext_lazy",
    "gettext_noop",
    "ngettext",
    "ngettext_lazy",
    "npgettext",
    "npgettext_lazy",
    "pgettext",
    "pgettext_lazy",
]

#: A named %-placeholder, the kind Django's ``%`` fills from a mapping.
_NAMED = re.compile(r"%\([A-Za-z_][A-Za-z0-9_]*\)")

Number = Union[int, float, str]


class LegacyText(Promise):
    """A gettext-family message, looked up when its values are known.

    ``% mapping`` looks it up with those values passed, and returns the rendered ``str``; any other
    use as text looks it up with none. For a plural message, the count is the call's ``number`` or,
    when ``number`` names a key (``ngettext_lazy(..., "num")``), that key of the mapping.
    """

    def __init__(
        self,
        text: str,
        *,
        category: Optional[str] = None,
        plural_form: Optional[str] = None,
        number: Optional[Number] = None,
    ) -> None:
        self._text = text
        self._category = category
        self._plural_form = plural_form
        self._number = number

    def _render(self, values: Mapping[str, Any]) -> str:
        params = dict(values)
        extra: dict[str, Any] = {}
        if self._plural_form is not None:
            count = values.get(self._number) if isinstance(self._number, str) else self._number
            params.setdefault("count", count)
            extra = {"entry_point": "ngettext", "plural": self._plural_form}
        return get_client().translate_legacy(
            self._text, category=self._category, params=params or None, **extra
        )

    def __mod__(self, rhs: Any) -> str:
        if isinstance(rhs, Mapping):
            return self._render(rhs)
        return str(str(self) % rhs)

    def __str__(self) -> str:
        return self._render({})

    def __html__(self) -> str:
        return str(self)

    def __add__(self, other: Any) -> str:
        return str(self) + str(other)

    def __radd__(self, other: Any) -> str:
        return str(other) + str(self)

    def __eq__(self, other: object) -> bool:
        return str(self) == other

    def __hash__(self) -> int:
        return hash(str(self))

    def __repr__(self) -> str:
        return f"<LegacyText {self._text!r}>"

    def __getattr__(self, name: str) -> Any:
        # Everything else a str offers (`.upper()`, `.format()`, …) acts on the looked-up text.
        return getattr(str(self), name)


def _now(text: LegacyText, *messages: str) -> Union[str, LegacyText]:
    """The looked-up text now, unless a placeholder is still to be filled by ``%``."""
    if any(_NAMED.search(message) for message in messages):
        return text
    return str(text)


def gettext(message: str) -> Union[str, LegacyText]:
    return _now(LegacyText(message), message)


def pgettext(context: str, message: str) -> Union[str, LegacyText]:
    return _now(LegacyText(message, category=context), message)


def ngettext(singular: str, plural: str, number: Number) -> Union[str, LegacyText]:
    return _now(LegacyText(singular, plural_form=plural, number=number), singular, plural)


def npgettext(context: str, singular: str, plural: str, number: Number) -> Union[str, LegacyText]:
    text = LegacyText(singular, category=context, plural_form=plural, number=number)
    return _now(text, singular, plural)


def gettext_lazy(message: str) -> LegacyText:
    return LegacyText(message)


def pgettext_lazy(context: str, message: str) -> LegacyText:
    return LegacyText(message, category=context)


def ngettext_lazy(singular: str, plural: str, number: Number) -> LegacyText:
    return LegacyText(singular, plural_form=plural, number=number)


def npgettext_lazy(context: str, singular: str, plural: str, number: Number) -> LegacyText:
    return LegacyText(singular, category=context, plural_form=plural, number=number)


def gettext_noop(message: str) -> str:
    """Marks a message for extraction without translating it, as Django's does."""
    return message
