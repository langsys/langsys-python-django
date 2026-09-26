"""Server messages for Django (spec MSG family): a failed form's errors as translatable entries.

The core's ``langsys.messages`` owns the entry shape, the fill, the template checks and the listing
command. This module supplies what only Django knows, and changes nothing Django does:

* each entry is built from the ``ValidationError`` Django raised: its ``message`` before Django
  fills it, its ``params`` and its ``code``, never from the rendered text (MSG-9). The code is
  Django's own, passed through; a failure raised without one carries none (MSG-2);
* the template is Django's own sentence, in the source language, as Django wrote it: ``This field is
  required.`` stays exactly that (MSG-3). Where the sentence names the field or the model —
  ``%(field_label)s``, ``%(field_labels)s``, ``%(model_name)s``, ``%(date_field_label)s`` — the
  label Django prints there is written in; every other ``%(name)s`` becomes a ``{name}`` marker,
  filled from Django's own params;
* :func:`error_response` answers with Django's own error body, ``form.errors.get_json_data()``, with
  the entries attached under a configurable key (MSG-1);
* a provider lists every template a set of forms can emit, for the ``langsys_messages`` management
  command (MSG-7).

A custom validator follows Django's convention and is translatable as it stands: raise
``ValidationError("That name is reserved.", code="reserved")``, or with ``%(name)s`` placeholders
and ``params``. Name the messages a validator function can raise with :func:`declares` so the
listing has them ahead of time; a class-based validator's ``message`` attribute is listed as it is.

Usage::

    form = SignupForm(request.POST)
    if not form.is_valid():
        return error_response(form)   # Django's errors, plus the entries under "langsys_errors"
"""

from __future__ import annotations

import contextlib
import inspect
import re
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar, Union

from django import forms
from django.core import validators
from django.core.exceptions import NON_FIELD_ERRORS, FieldDoesNotExist, ValidationError
from django.forms.utils import pretty_name
from django.http import JsonResponse
from django.utils import translation
from langsys.messages import TemplateProblem, attach_server_messages, server_message

from .client import get_client
from .conf import get_settings

if TYPE_CHECKING:
    from langsys import LangsysClient

__all__ = [
    "LABEL_PLACEHOLDERS",
    "LabelAdvice",
    "declared_templates",
    "declares",
    "entries_from_form",
    "error_response",
]

Entry = dict[str, Any]
F = TypeVar("F", bound=Callable[..., Any])


@dataclass(frozen=True)
class LabelAdvice:
    """A validated field with no declared label (MSG-10). Django names it from its key, which is
    sometimes a raw key the app would rather not show. The listing command prints this as advice;
    it never fails the command."""

    source: str
    field: str
    label: str

    def __str__(self) -> str:
        return (
            f"{self.source} field {self.field!r} has no declared label, so Django names it "
            f"{self.label!r} - declare one to choose the name users see"
        )


Declared = Union[str, Mapping[str, Any], TemplateProblem, LabelAdvice]

#: Django's placeholders that stand for a label or a model's name. What they hold is translatable,
#: so the name Django prints is written into the sentence rather than left as a marker (MSG-3). A
#: template that still holds one is refused when it is added (MSG-11).
LABEL_NAMES = frozenset({"field_label", "field_labels", "model_name", "date_field_label"})
LABEL_PLACEHOLDERS = tuple(f"%({name})s" for name in sorted(LABEL_NAMES))

#: A %-format placeholder as Django's messages write them: `%(limit_value)d`, `%(value)r`, `%%`.
_PERCENT = re.compile(
    r"%(?:\((?P<name>[^)]*)\))?(?P<spec>[#0\- +]*\d*(?:\.\d+)?)(?P<kind>[diouxXeEfFgGcrsa%])"
)


def declares(*messages: str) -> Callable[[F], F]:
    """Name the messages a custom validator function or ``clean`` method can raise, written as
    Django writes them (``%(name)s`` placeholders), so :func:`declared_templates` lists them rather
    than reporting the validator."""

    def mark(func: F) -> F:
        func.__langsys_templates__ = messages  # type: ignore[attr-defined]
        return func

    return mark


# -- Django's message -> template and params (MSG-3, MSG-4, MSG-9) -----------------------------


def unfilled(
    message: Any, number: Any = None, *, params: Optional[Mapping[str, Any]] = None
) -> Optional[str]:
    """Django's sentence for a failure before its values are filled, in the active language, or
    None when it cannot be read.

    A message made with ``ngettext_lazy(singular, plural, "name")`` picks its form from a param
    when Django fills it; the form is chosen here the same way, from ``params`` or ``number``.
    """
    resolved = message
    cast = getattr(message, "_proxy____cast", None)
    if cast is not None:
        resolved = cast()
    choose = getattr(resolved, "_translate", None)
    if choose is None:
        return str(resolved)
    if params is not None:
        with contextlib.suppress(KeyError):
            number = resolved._get_number_value(params)
    return None if number is None else str(choose(number))


def to_template(
    text: str, params: Optional[Mapping[str, Any]] = None
) -> Optional[tuple[str, dict[str, Any]]]:
    """A Django sentence as a template, and the params that fill it.

    ``%(name)s`` becomes the marker ``{name}`` and its value a param, as Django prints it: ``%r``
    as the value's ``repr``, a number as a number. A label placeholder is written in as Django
    prints it. Without ``params`` (listing ahead of time) only the markers are made, and a label
    placeholder stays in place, for the template check to refuse (MSG-11). ``%%`` is a percent
    sign. None when a placeholder has no name, or no value in ``params``.
    """
    values: dict[str, Any] = {}
    names = [m["name"] for m in _PERCENT.finditer(text) if m["kind"] != "%"]
    if None in names or (params is not None and not set(names) <= set(params)):
        return None

    def convert(match: re.Match[str]) -> str:
        kind, name = match["kind"], match["name"]
        if kind == "%":
            return "%"
        if params is None:
            return match.group(0) if name in LABEL_NAMES else "{" + name + "}"
        if name in LABEL_NAMES:
            return match.group(0) % {name: params[name]}
        values[name] = _param(params[name], kind)
        return "{" + name + "}"

    return _PERCENT.sub(convert, text), values


def _param(value: Any, kind: str) -> Any:
    """A value as Django prints it into the sentence; numbers stay numbers (MSG-4)."""
    if kind == "r":
        return repr(value)
    if kind in "diouxX" and not isinstance(value, bool):
        with contextlib.suppress(TypeError, ValueError):
            return int(value)
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return str(value)


def entry_parts(error: ValidationError) -> tuple[Optional[str], str, Optional[dict[str, Any]]]:
    """``(code, template, params)`` for one of Django's ``ValidationError``s, in the source
    language. A message that cannot be read unfilled is the finished text it is, with no params,
    and the listing reports the validator that raised it (MSG-9)."""
    params = dict(error.params or {})
    with translation.override(None):
        text = unfilled(error.message, params=params)
        read = to_template(text, params) if text is not None else None
        template, values = read if read is not None else (str(error.messages[0]), {})
    return error.code, template, values or None


# -- errors -> entries (MSG-1, MSG-9) ---------------------------------------------------------


def entries_from_form(
    form: forms.BaseForm, *, client: Optional[LangsysClient] = None
) -> list[Entry]:
    """Entries for a bound form's errors, in the order Django reports them. With ``client``, each
    entry goes through ``client.server_message`` (MSG-8 registration, MSG-11 check); without, the
    core's pure constructor. A form-wide failure carries no ``field``."""
    build = client.server_message if client is not None else server_message
    entries: list[Entry] = []
    for name, errors in form.errors.as_data().items():
        field = None if name == NON_FIELD_ERRORS else name
        for error in errors:
            code, template, params = entry_parts(error)
            entries.append(build(template, params, field=field, code=code))
    return entries


def error_response(
    form: forms.BaseForm, *, status: int = 400, key: Optional[str] = None
) -> JsonResponse:
    """Answer a failed form with Django's own error body, ``form.errors.get_json_data()``, and the
    form's entries attached beside it under ``key``: the ``RESPONSE_KEY`` setting, or the core's
    default key."""
    entries = entries_from_form(form, client=get_client())
    key = key or get_settings().response_key
    body = attach_server_messages(
        form.errors.get_json_data(), entries, **({"key": key} if key else {})
    )
    return JsonResponse(body, status=status)


# -- labels (MSG-10) --------------------------------------------------------------------------


def _label(form_class: type, name: str, field: Optional[forms.Field]) -> tuple[str, bool]:
    """The label Django shows for ``name``, and whether the app declared it: a form field's
    ``label``, ``Meta.labels``, or the model field's ``verbose_name``. Otherwise Django derives
    one from the key."""
    declared = getattr(form_class, "declared_fields", {}).get(name)
    if declared is not None and declared.label is not None:
        return str(declared.label), True
    meta = getattr(form_class, "_meta", None)
    labels = getattr(meta, "labels", None) or {}
    if name in labels:
        return str(labels[name]), True
    model = getattr(meta, "model", None)
    if model is not None:
        with contextlib.suppress(FieldDoesNotExist):
            model_field = model._meta.get_field(name)
            declared_name = getattr(model_field, "_verbose_name", None)
            return str(model_field.verbose_name), declared_name is not None
    if field is not None and field.label is not None:
        return str(field.label), True
    return pretty_name(name), False


# -- listing (MSG-7) --------------------------------------------------------------------------


def declared_templates(form_classes: Iterable[type]) -> Iterator[Declared]:
    """Every template ``form_classes`` can emit, for the ``langsys_messages`` command (MSG-7).

    A custom validator or ``clean`` method whose messages are not named with :func:`declares` is
    reported: its messages register the first time they are emitted (MSG-8), and ``--strict``
    makes the report fail the command. A field with no declared label is advice. Point the command
    at a provider::

        def templates():
            return declared_templates([SignupForm, ProfileForm])

        # python manage.py langsys_messages --provider myapp.langsys:templates [--register]
    """
    with translation.override(None):
        for form_class in form_classes:
            yield from _form_templates(form_class)


def _form_templates(form_class: Any) -> Iterator[Declared]:
    source = f"{form_class.__module__}.{form_class.__qualname__}"
    model = getattr(getattr(form_class, "_meta", None), "model", None)
    for name, field in form_class.base_fields.items():
        label, declared = _label(form_class, name, field)
        if not declared:
            yield LabelAdvice(source, name, label)
        for code, message in field.error_messages.items():
            if code == "required" and not field.required:
                continue
            count = getattr(field, "max_length", None) if code == "max_length" else None
            yield from listed(message, source, name, number=count)
        for validator in field.validators:
            yield from validator_templates(validator, source, name)
        if model is not None:
            yield from _unique_templates(model, (name,), source, name)
    for attr in sorted(vars(form_class)):
        if attr == "clean" or attr.startswith("clean_"):
            yield from custom_templates(
                vars(form_class)[attr], source, attr[6:] if attr != "clean" else "", attr
            )
    if model is not None:
        for together in model._meta.unique_together:
            if all(name in form_class.base_fields for name in together):
                yield from _unique_templates(model, tuple(together), source, "")


def _unique_templates(
    model: Any, check: tuple[str, ...], source: str, field: str
) -> Iterator[Declared]:
    """Django's own uniqueness message for ``check``, as ``Model.unique_error_message`` builds it,
    with the model's and the fields' names written in."""
    if len(check) == 1:
        try:
            if not model._meta.get_field(check[0]).unique:
                return
        except FieldDoesNotExist:
            return
    _, template, _ = entry_parts(model().unique_error_message(model, check))
    yield _item(template, source, field)


def validator_templates(validator: Any, source: str, field: str) -> Iterator[Declared]:
    """The messages a validator can raise. By Django's convention a class-based validator keeps
    its message in a ``message`` attribute, and ``DecimalValidator`` in a ``messages`` dict; a
    function names its messages with :func:`declares` and is otherwise reported."""
    if isinstance(validator, validators.DecimalValidator):
        digits, places = validator.max_digits, validator.decimal_places
        counts = {
            "max_digits": digits,
            "max_decimal_places": places,
            "max_whole_digits": digits - places
            if digits is not None and places is not None
            else None,
        }
        for code, count in counts.items():
            if count is not None:
                yield from listed(validator.messages[code], source, field, number=count)
        return
    message = getattr(validator, "message", None)
    if message is not None and not inspect.isroutine(validator):
        limit = getattr(validator, "limit_value", None)
        yield from listed(message, source, field, number=None if callable(limit) else limit)
        return
    name = getattr(validator, "__name__", type(validator).__name__)
    yield from custom_templates(validator, source, field, name)


def custom_templates(func: Any, source: str, field: str, name: str) -> Iterator[Declared]:
    """What a validator function or ``clean`` method declares, or a report that it cannot be
    listed ahead of time."""
    declared = getattr(func, "__langsys_templates__", None)
    if declared is not None:
        for message in declared:
            yield from listed(message, source, field)
        return
    yield TemplateProblem(
        f"{name!r} can fail with a message that cannot be listed ahead of time; it registers the "
        "first time it is emitted",
        source=source,
        field=field,
        fix="name the messages it raises with @langsys_django.messages.declares(...)",
    )


def listed(message: Any, source: str, field: str, *, number: Any = None) -> Iterator[Declared]:
    """A Django message as the template it lists as. A plural message whose count is not known
    ahead of time lists both of its source forms."""
    chosen = unfilled(message, number)
    forms_: list[Optional[str]] = (
        [chosen] if chosen is not None else [unfilled(message, 1), unfilled(message, 2)]
    )
    for text in dict.fromkeys(str(form) for form in forms_):
        read = to_template(text)
        if read is None:
            yield TemplateProblem(
                f"{text!r} has a placeholder with no name, so it cannot be made a template",
                source=source,
                field=field,
                fix="name each placeholder, as %(name)s",
            )
            continue
        yield _item(read[0], source, field)


def _item(template: str, source: str, field: str) -> Mapping[str, Any]:
    item = {"template": template, "source": source}
    if field:
        item["field"] = field
    return item
