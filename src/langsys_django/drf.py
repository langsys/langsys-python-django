"""Server messages for Django REST framework serializers (spec MSG family).

The same contract ``langsys_django.messages`` gives Django's forms, over a serializer's errors, and
nothing DRF does changes:

* each entry carries DRF's own ``code``, passed through, and DRF's own sentence as its template, in
  the source language (MSG-2, MSG-3). DRF writes its messages as ``str.format`` sentences —
  ``Ensure this field has no more than {max_length} characters.`` — so a template is DRF's message
  as written, each ``{name}`` a marker. DRF's ``ErrorDetail`` holds the rendered text and the code,
  not the values, so the values are read where DRF read them: the field's own bounds
  (``max_length``), and the input it rejected. ``{field_names}``, the one DRF placeholder that names
  fields, is written in as DRF prints it;
* a failure that holds a Django validator's message is read from that validator, as
  ``langsys_django.messages`` reads a form's (MSG-9);
* a message is taken as DRF's own only when DRF's rendering of it, in the language the request was
  served in, is the text DRF reported. Anything else — a custom validator's
  ``ValidationError("That name is reserved.")`` — is the finished text it is, with its code and no
  params, and the listing reports the validator that raised it;
* :func:`exception_handler` leaves DRF's own error response as it is and attaches the entries beside
  it under a configurable key (MSG-1). :func:`declared_templates` lists every template a set of
  serializers can emit, for the ``langsys_messages`` management command (MSG-7).

A field's path is its keys through nested serializers and lists, dotted: ``items.1.name``.

Setup::

    REST_FRAMEWORK = {"EXCEPTION_HANDLER": "langsys_django.drf.exception_handler"}
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Iterator, Mapping
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Optional

from django.core.exceptions import FieldDoesNotExist
from django.core.exceptions import ValidationError as DjangoValidationError
from django.utils import translation
from langsys.messages import attach_server_messages, server_message
from rest_framework import fields as drf
from rest_framework import serializers
from rest_framework.exceptions import APIException
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.utils import humanize_datetime
from rest_framework.utils.formatting import lazy_format
from rest_framework.views import exception_handler as drf_exception_handler

from .client import get_client
from .conf import get_settings
from .messages import (
    Declared,
    Entry,
    LabelAdvice,
    custom_templates,
    entry_parts,
    validator_templates,
)

if TYPE_CHECKING:
    from langsys import LangsysClient

__all__ = [
    "LABEL_PLACEHOLDERS",
    "declared_templates",
    "entries_from_errors",
    "entries_from_serializer",
    "exception_handler",
]

#: DRF's one placeholder that names fields. What it holds is written in (MSG-3), and a template
#: that still holds it is refused when it is added (MSG-11).
LABEL_PLACEHOLDERS = ("{field_names}",)

#: A `str.format` field as DRF's messages write them, and the doubled braces that escape one.
_FORMAT = re.compile(r"\{\{|\}\}|\{(?P<name>\w+)(?P<conv>![rsa])?(?::(?P<spec>[^{}]*))?\}")

#: DRF's placeholders for the input a field rejected, and for that input's type.
_INPUT = frozenset({"input", "pk_value", "value"})
_INPUT_TYPE = frozenset({"input_type", "datatype", "data_type"})

_MISSING = object()

Build = Callable[..., Entry]


# -- errors -> entries (MSG-1, MSG-9) ---------------------------------------------------------


def entries_from_serializer(
    serializer: serializers.BaseSerializer, *, client: Optional[LangsysClient] = None
) -> list[Entry]:
    """Entries for a validated serializer's errors. With ``client``, each entry goes through
    ``client.server_message`` (MSG-8 registration, MSG-11 check); without, the core's pure
    constructor."""
    return entries_from_errors(serializer.errors, serializer, client=client)


def entries_from_errors(
    errors: Any,
    serializer: Optional[serializers.BaseSerializer] = None,
    *,
    client: Optional[LangsysClient] = None,
) -> list[Entry]:
    """Entries for DRF errors (``serializer.errors`` or a ``ValidationError``'s ``detail``), read
    against ``serializer``'s fields and the data it was given. Call it in the request's language:
    that is the language DRF rendered the errors in."""
    build: Build = client.server_message if client is not None else server_message
    data = getattr(serializer, "initial_data", _MISSING)
    entries: list[Entry] = []
    _walk(errors, serializer, data, "", build, entries, translation.get_language())
    return entries


def exception_handler(exc: Exception, context: Mapping[str, Any]) -> Optional[Response]:
    """DRF's ``EXCEPTION_HANDLER``: DRF's own handler answers, and a failed validation's response
    also carries its entries, beside DRF's body under the ``RESPONSE_KEY`` setting or the core's
    default key. A body that is not an object has nowhere to put them beside, and is left as DRF
    wrote it."""
    response = drf_exception_handler(exc, context)
    if response is None or not isinstance(exc, DRFValidationError):
        return response
    if not isinstance(response.data, Mapping):
        return response
    client = get_client()
    entries = entries_from_errors(exc.detail, _serializer_for(context), client=client)
    key = get_settings().response_key
    response.data = attach_server_messages(
        dict(response.data), entries, **({"key": key} if key else {})
    )
    return response


def _serializer_for(context: Mapping[str, Any]) -> Optional[serializers.BaseSerializer]:
    """The view's serializer, given the request's data, for the fields and input its errors name."""
    view, request = context.get("view"), context.get("request")
    if getattr(view, "serializer_class", None) is None:
        return None
    with contextlib.suppress(APIException):
        return view.get_serializer(data=request.data)  # type: ignore[union-attr]
    return view.get_serializer()  # type: ignore[union-attr]


def _walk(
    errors: Any,
    node: Any,
    data: Any,
    path: str,
    build: Build,
    entries: list[Entry],
    active: Optional[str],
) -> None:
    """Turn the errors belonging to ``node`` (a serializer, or a field) into entries."""
    if isinstance(errors, Mapping):
        for key, value in errors.items():
            if key == api_settings.NON_FIELD_ERRORS_KEY:
                _leaves(value, node, data, path, build, entries, active)
                continue
            child = _child(node, key)
            _walk(value, child, _item(data, key), _join(path, key), build, entries, active)
        return
    if isinstance(errors, list) and any(isinstance(item, Mapping) for item in errors):
        # A many=True serializer's errors: one per item, empty for an item that passed.
        child = getattr(node, "child", None)
        for index, item in enumerate(errors):
            if item:
                _walk(item, child, _item(data, index), _join(path, index), build, entries, active)
        return
    _leaves(errors, node, data, path, build, entries, active)


def _leaves(
    errors: Any,
    node: Any,
    data: Any,
    path: str,
    build: Build,
    entries: list[Entry],
    active: Optional[str],
) -> None:
    for detail in errors if isinstance(errors, (list, tuple)) else [errors]:
        code = getattr(detail, "code", None)
        template, params = _parts(node, detail, data, active)
        entries.append(build(template, params or None, field=path or None, code=code))


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def _child(node: Any, key: Any) -> Any:
    fields = getattr(node, "fields", None)
    if isinstance(fields, Mapping) and key in fields:
        return fields[key]
    return getattr(node, "child", None)


def _item(data: Any, key: Any) -> Any:
    if isinstance(data, Mapping):
        return data.get(key, _MISSING)
    if isinstance(data, (list, tuple)):
        with contextlib.suppress(ValueError, IndexError):
            return data[int(key)]
    return _MISSING


def _parts(node: Any, detail: Any, data: Any, active: Optional[str]) -> tuple[str, dict[str, Any]]:
    """``(template, params)`` for one reported failure: DRF's own message for it when DRF's
    rendering of that message is the reported text, else the reported text as it is."""
    code, text = getattr(detail, "code", None), str(detail)
    for message, written_in in _drf_messages(node, code):
        values = _values(node, message, data, written_in)
        if values is None:
            continue
        with translation.override(active):
            if str(lazy_format(message, **values)) != text:
                continue
        with translation.override(None):
            return _template(str(message), values, written_in)
    for error in _django_failures(node, code, data):
        with translation.override(active):
            if error.messages[0] != text:
                continue
        _, template, params = entry_parts(error)
        return template, params or {}
    return text, {}


def _drf_messages(node: Any, code: Optional[str]) -> Iterator[tuple[Any, dict[str, str]]]:
    """DRF's own messages that can report ``code`` on ``node``: its ``error_messages``, then its
    DRF validators', each with the field names it writes in."""
    message = getattr(node, "error_messages", {}).get(code)
    if message is not None:
        yield message, {}
    for validator in getattr(node, "validators", ()):
        if _is_drf_validator(validator):
            yield validator.message, _written_in(validator)


def _django_failures(node: Any, code: Optional[str], data: Any) -> Iterator[DjangoValidationError]:
    """The failures ``node``'s Django validators raise for ``data``, carrying Django's own message
    and params."""
    validators = [v for v in getattr(node, "validators", ()) if _is_django_validator(v)]
    if not validators or data is _MISSING:
        return
    try:
        value = node.to_internal_value(data)
    except (DRFValidationError, DjangoValidationError, TypeError, ValueError):
        return  # a value DRF could not read reached no validator
    for validator in validators:
        try:
            validator(value)
        except DjangoValidationError as failure:
            yield from (error for error in failure.error_list if error.code == code)


def _values(
    node: Any, message: Any, data: Any, written_in: Mapping[str, str]
) -> Optional[dict[str, Any]]:
    """The values DRF fills ``message`` with, read where DRF reads them; None when one of them
    cannot be read."""
    with translation.override(None):
        names = [m["name"] for m in _FORMAT.finditer(str(message)) if m["name"]]
    values = {
        name: written_in[name] if name in written_in else _value(node, name, data) for name in names
    }
    return None if any(value is _MISSING for value in values.values()) else values


def _value(node: Any, name: str, data: Any) -> Any:
    if name in _INPUT or name in _INPUT_TYPE:
        if data is _MISSING or name in _INPUT:
            return data
        return type(data).__name__
    if name == "max_decimal_places":
        return node.decimal_places
    if name == "format":
        return _formats(node)
    value = getattr(node, name, None)
    return _MISSING if value is None else value


def _formats(node: Any) -> Any:
    """The input formats a date, time or duration field names in its ``invalid`` message."""
    if isinstance(node, drf.DurationField):
        return "[DD] [HH:[MM:]]ss[.uuuuuu]"
    kinds = (
        (drf.DateTimeField, humanize_datetime.datetime_formats, "DATETIME_INPUT_FORMATS"),
        (drf.DateField, humanize_datetime.date_formats, "DATE_INPUT_FORMATS"),
        (drf.TimeField, humanize_datetime.time_formats, "TIME_INPUT_FORMATS"),
    )
    for kind, humanize, setting in kinds:
        if isinstance(node, kind):
            formats = getattr(node, "input_formats", None)
            return humanize(formats if formats is not None else getattr(api_settings, setting))
    return _MISSING


def _template(
    text: str, values: Optional[Mapping[str, Any]], written_in: Mapping[str, str]
) -> tuple[str, dict[str, Any]]:
    """A DRF sentence as a template: each ``{name}`` a marker, and each written-in name printed as
    DRF prints it. Without ``values`` (listing ahead of time) only the markers are made."""
    params: dict[str, Any] = {}

    def convert(match: re.Match[str]) -> str:
        whole, name = match.group(0), match["name"]
        if name is None:
            return whole[0]
        if name in written_in:
            return str(written_in[name])
        if values is not None:
            params[name] = _param(values[name], match["conv"], match["spec"])
        return "{" + name + "}"

    return _FORMAT.sub(convert, text), params


def _param(value: Any, conv: Optional[str], spec: Optional[str]) -> Any:
    """A value as DRF prints it into the sentence; numbers stay numbers (MSG-4)."""
    if conv or spec:
        shown = {"!r": repr, "!a": ascii}.get(conv or "", str)(value) if conv else value
        return format(shown, spec or "")
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value
    return str(value)


def _written_in(validator: Any) -> dict[str, str]:
    fields = getattr(validator, "fields", None)
    return {"field_names": ", ".join(fields)} if fields else {}


def _is_drf_validator(validator: Any) -> bool:
    return type(validator).__module__.startswith("rest_framework.") and hasattr(
        validator, "message"
    )


def _is_django_validator(validator: Any) -> bool:
    """A Django validator carrying Django's own message. DRF's own bounds (``max_length`` and the
    like) are Django validators holding DRF's message already formatted; those are read from the
    field's ``error_messages`` instead."""
    if not type(validator).__module__.startswith("django."):
        return False
    return not isinstance(getattr(validator, "message", None), lazy_format)


# -- labels (MSG-10) --------------------------------------------------------------------------


def _label(field: Any, key: str, parent: Any) -> tuple[str, bool]:
    """The label DRF shows for ``key``, and whether the app declared it: a field's ``label``, or on
    a ``ModelSerializer`` the model field's ``verbose_name``. A bound field always has a label,
    because DRF fills a missing one from the key, so only one actually given counts."""
    declared = getattr(parent, "_declared_fields", {}).get(key)
    if declared is not None and declared.label is not None:
        return str(declared.label), True
    model = getattr(getattr(parent, "Meta", None), "model", None)
    source = getattr(field, "source", None) or key
    if model is not None and declared is None:
        with contextlib.suppress(FieldDoesNotExist):
            model_field = model._meta.get_field(source)
            if getattr(model_field, "_verbose_name", None) is not None:
                return str(model_field.verbose_name), True
    given = getattr(field, "_kwargs", {}).get("label")
    if given is not None:
        return str(given), True
    return str(getattr(field, "label", None) or key), False


# -- listing (MSG-7) --------------------------------------------------------------------------


def declared_templates(serializer_classes: Iterable[type]) -> Iterator[Declared]:
    """Every template ``serializer_classes`` can emit, for the ``langsys_messages`` command (MSG-7).

    A custom validator or ``validate`` method whose messages are not named with
    ``langsys_django.messages.declares`` is reported; a field with no declared label is advice.
    Point the command at a provider::

        def templates():
            return declared_templates([SignupSerializer, OrderSerializer])

        # python manage.py langsys_messages --provider myapp.langsys:templates [--register]
    """
    with translation.override(None):
        for serializer_class in serializer_classes:
            source = f"{serializer_class.__module__}.{serializer_class.__qualname__}"
            yield from _serializer_templates(serializer_class(), source, "")


def _serializer_templates(serializer: Any, source: str, prefix: str) -> Iterator[Declared]:
    yield from _own_templates(serializer, source, prefix)
    for name, field in serializer.fields.items():
        if field.read_only:
            continue
        path = _join(prefix, name)
        label, declared = _label(field, name, serializer)
        if not declared:
            yield LabelAdvice(source, path, label)
        yield from _field_templates(field, source, path)
    for attr, func in sorted(vars(type(serializer)).items()):
        if attr == "validate" or attr.startswith("validate_"):
            field = _join(prefix, attr[9:]) if attr != "validate" else prefix
            yield from custom_templates(func, source, field, attr)


def _field_templates(field: Any, source: str, path: str) -> Iterator[Declared]:
    if isinstance(field, serializers.ListSerializer):
        yield from _own_templates(field, source, path)
        yield from _serializer_templates(field.child, source, path)
        return
    if isinstance(field, serializers.BaseSerializer):
        yield from _serializer_templates(field, source, path)
        return
    yield from _own_templates(field, source, path)
    child = getattr(field, "child", None)
    if child is not None:
        yield from _field_templates(child, source, path)


def _own_templates(node: Any, source: str, path: str) -> Iterator[Declared]:
    """The messages ``node`` itself can fail with: its ``error_messages`` it has the values for,
    and its validators'."""
    for code, message in node.error_messages.items():
        if code == "required" and not node.required:
            continue
        text = str(message)
        names = [m["name"] for m in _FORMAT.finditer(text) if m["name"]]
        if any(_bound_unset(node, name) for name in names):
            continue
        yield _listed(_template(text, None, {})[0], source, path)
    for validator in node.validators:
        if _is_drf_validator(validator):
            text = str(validator.message)
            yield _listed(_template(text, None, _written_in(validator))[0], source, path)
        elif _is_django_validator(validator):
            yield from validator_templates(validator, source, path)
        elif not isinstance(getattr(validator, "message", None), lazy_format):
            name = getattr(validator, "__name__", type(validator).__name__)
            yield from custom_templates(validator, source, path, name)


def _bound_unset(node: Any, name: str) -> bool:
    """Whether a placeholder names a bound the field does not set, so the message cannot occur."""
    if name in _INPUT or name in _INPUT_TYPE or name == "format":
        return False
    attr = "decimal_places" if name == "max_decimal_places" else name
    return hasattr(node, attr) and getattr(node, attr) is None


def _listed(template: str, source: str, path: str) -> Mapping[str, Any]:
    item = {"template": template, "source": source}
    if path:
        item["field"] = path
    return item
