"""Server messages for Django REST framework serializers (spec MSG family).

The same contract as ``langsys_django.messages`` gives Django's forms, over a serializer's errors:

* each entry is built from the failed rule's ``code`` and the failing field's own bounds, never from
  DRF's rendered text (MSG-9). DRF's ``ErrorDetail`` carries a code but no params, so a bound such
  as ``min_length`` is read from the field;
* labels come from a declared ``label`` or, on a ``ModelSerializer``, the model field's declared
  ``verbose_name`` (MSG-10);
* a body that is not JSON, a body that is not an object, and a nested value that is not an object
  take the core's MSG-2 ``WORDINGS`` sentences;
* :func:`exception_handler` answers DRF's parse and validation errors with the default langsys
  envelope, and :func:`declared_templates` lists every template a set of serializers can emit, for
  the ``langsys_messages`` management command (MSG-7).

DRF gives its own ``invalid`` failures and a custom validator's ``ValidationError("…")`` the same
code. A failure is DRF's own when its text is the field's own ``invalid`` message; anything else is
the validator's sentence, used as written. Raise ``serializers.ValidationError(template, code=…)``
with a vocabulary code to declare one.

Setup::

    REST_FRAMEWORK = {"EXCEPTION_HANDLER": "langsys_django.drf.exception_handler"}
"""

from __future__ import annotations

import contextlib
import re
from collections.abc import Iterable, Iterator, Mapping
from datetime import timedelta
from typing import TYPE_CHECKING, Any, Callable, Optional

from django.core.exceptions import FieldDoesNotExist
from django.utils import translation
from langsys.messages import (
    MESSAGE_CODES,
    WORDINGS,
    TemplateProblem,
    server_message,
    size_code,
    with_label,
)
from rest_framework import fields as drf
from rest_framework import relations, serializers, status
from rest_framework.exceptions import ParseError
from rest_framework.exceptions import ValidationError as DRFValidationError
from rest_framework.response import Response
from rest_framework.settings import api_settings
from rest_framework.validators import (
    ProhibitSurrogateCharactersValidator,
    UniqueTogetherValidator,
    UniqueValidator,
)
from rest_framework.views import exception_handler as drf_exception_handler

from .client import get_client
from .messages import (
    _DIGITS,
    _LENGTH,
    _VALUE,
    CHOICE,
    DATE,
    EMAIL,
    FAILED,
    FILE,
    FORMAT,
    GENERIC,
    IMAGE,
    JSON_VALUE,
    LIST,
    NOT_FOUND,
    NUMBER,
    REQUIRED,
    TAKEN,
    TAKEN_TOGETHER,
    URL,
    UUID_,
    WHOLE,
    Declared,
    Entry,
    _custom,
    _is_django_validator,
    _number,
)

if TYPE_CHECKING:
    from langsys import LangsysClient

__all__ = [
    "declared_templates",
    "entries_from_errors",
    "entries_from_serializer",
    "exception_handler",
]

TEXT = ("invalid_type", "The :attribute must be text.")
BOOLEAN = ("invalid_type", "The :attribute must be true or false.")

#: A field's own `invalid`, by field type. Order matters: DRF's email, URL, regex, slug and IP
#: fields are CharFields, and an ImageField is a FileField.
_INVALID: tuple[tuple[type, tuple[str, str]], ...] = (
    (drf.EmailField, EMAIL),
    (drf.URLField, URL),
    (drf.UUIDField, UUID_),
    (drf.RegexField, FORMAT),
    (drf.SlugField, FORMAT),
    (drf.IPAddressField, FORMAT),
    (drf.IntegerField, WHOLE),
    (drf.FloatField, NUMBER),
    (drf.DecimalField, NUMBER),
    (drf.DateTimeField, DATE),
    (drf.DateField, DATE),
    (drf.TimeField, DATE),
    (drf.BooleanField, BOOLEAN),
    (drf.JSONField, JSON_VALUE),
    (drf.ImageField, IMAGE),
    (drf.FileField, FILE),
    (drf.CharField, TEXT),
)
_MANY = (
    drf.ListField,
    drf.MultipleChoiceField,
    serializers.ListSerializer,
    relations.ManyRelatedField,
)
_RELATED = (
    relations.PrimaryKeyRelatedField,
    relations.SlugRelatedField,
    relations.HyperlinkedRelatedField,
)
_REQUIRED_CODES = ("required", "null", "blank", "empty")
_FORMAT_CODES = (
    "invalid_unicode",
    "make_aware",
    "overflow",
    "max_string_length",
    "incorrect_type",
    "no_name",
)
_DIGIT_BOUNDS = {
    "max_digits": "max_digits",
    "max_decimal_places": "decimal_places",
    "max_whole_digits": "max_whole_digits",
}

Wording = tuple[str, str, Optional[str], Any]
Build = Callable[..., Entry]


# -- errors -> entries (MSG-9, MSG-10) --------------------------------------------------------


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
    """Entries for DRF errors (``serializer.errors`` or a ``ValidationError``'s ``detail``), with
    fields and labels found on ``serializer``. Each entry's ``field`` is a dotted path through
    nested serializers and lists, such as ``items.1.name``."""
    build: Build = client.server_message if client is not None else server_message
    active = translation.get_language()
    entries: list[Entry] = []
    # Labels and templates are source text, so they are read with Django's own translation off.
    with translation.override(None):
        _walk(errors, serializer, "", "", True, build, entries, active)
    return entries


def exception_handler(exc: Exception, context: Mapping[str, Any]) -> Optional[Response]:
    """DRF's ``EXCEPTION_HANDLER``: a body that is not JSON and a failed validation answer with the
    default langsys envelope, ``{"status": false, "error": {…, "errors": [entry, …]}}``; anything
    else goes to DRF's own handler."""
    if isinstance(exc, ParseError):
        client = get_client()
        entry = client.server_message(*WORDINGS["body_not_json"])
        return _envelope(client, [entry])
    if isinstance(exc, DRFValidationError):
        client = get_client()
        view = context.get("view")
        serializer = None
        if getattr(view, "serializer_class", None) is not None:
            serializer = view.get_serializer()  # type: ignore[union-attr]
        return _envelope(client, entries_from_errors(exc.detail, serializer, client=client))
    return drf_exception_handler(exc, context)


def _envelope(client: LangsysClient, entries: list[Entry]) -> Response:
    failed = client.server_message(*FAILED)
    return Response(
        {"status": False, "error": {**failed, "errors": entries}},
        status=status.HTTP_400_BAD_REQUEST,
    )


def _walk(
    errors: Any,
    node: Any,
    path: str,
    label: str,
    root: bool,
    build: Build,
    entries: list[Entry],
    active: Optional[str],
) -> None:
    """Turn the errors belonging to ``node`` (a serializer, or a field) into entries."""
    if isinstance(errors, Mapping):
        for key, value in errors.items():
            if key == api_settings.NON_FIELD_ERRORS_KEY:
                for detail in _details(value):
                    code, template, params = _node_rule(node, detail, root, label, active)
                    entries.append(build(code, template, params or None, path or None))
                continue
            field, child_label = _child(node, key, label)
            _walk(value, field, _join(path, key), child_label, False, build, entries, active)
        return
    if isinstance(errors, list) and any(isinstance(item, Mapping) for item in errors):
        # A many=True serializer's errors: one per item, empty for an item that passed.
        child = getattr(node, "child", None)
        for index, item in enumerate(errors):
            if item:
                _walk(item, child, _join(path, index), label, False, build, entries, active)
        return
    for detail in _details(errors):
        if root:
            code, template, params = _node_rule(node, detail, True, label, active)
        else:
            code, template, params = _rule(node, detail, label, active)
        entries.append(build(code, template, params or None, path or None))


def _details(errors: Any) -> list[Any]:
    return list(errors) if isinstance(errors, (list, tuple)) else [errors]


def _join(path: str, key: Any) -> str:
    return f"{path}.{key}" if path else str(key)


def _child(node: Any, key: Any, parent_label: str) -> tuple[Any, str]:
    """The field an error key belongs to, and its label. A list's or dict's item keeps the
    container's label."""
    fields = getattr(node, "fields", None)
    if isinstance(fields, Mapping) and key in fields:
        field = fields[key]
        return field, _label(field, str(key), node)[0]
    child = getattr(node, "child", None)
    if child is not None:
        return child, parent_label
    return None, str(key)


def _rule(
    field: Any, detail: Any, label: str, active: Optional[str]
) -> tuple[str, str, dict[str, Any]]:
    code = getattr(detail, "code", None)
    wording = _wording(field, code) if field is not None else None
    if wording is None or (code == "invalid" and not _own_invalid(field, detail, active)):
        return _text(detail)
    code, template, marker, value = wording
    return code, with_label(template, label), ({marker: value} if marker else {})


def _node_rule(
    node: Any, detail: Any, root: bool, label: str, active: Optional[str]
) -> tuple[str, str, dict[str, Any]]:
    """A failure that belongs to a serializer as a whole: the request body at the root, and a
    nested object below it."""
    code = getattr(detail, "code", None)
    if code == "invalid" and node is not None and _own_invalid(node, detail, active):
        if root:
            return (*WORDINGS["body_not_object"], {})
        return WORDINGS["object_type"][0], with_label(WORDINGS["object_type"][1], label), {}
    if code == "unique" and node is not None:
        for validator in getattr(node, "validators", ()):
            if isinstance(validator, UniqueTogetherValidator):
                return (
                    TAKEN_TOGETHER[0],
                    with_label(TAKEN_TOGETHER[1], _labels(node, validator.fields)),
                    {},
                )
    return _text(detail)


def _text(detail: Any) -> tuple[str, str, dict[str, Any]]:
    """A failure DRF did not word: its own text is the template, under a vocabulary code the
    raiser gave, and `invalid` otherwise."""
    code = getattr(detail, "code", None)
    declared = code if code in MESSAGE_CODES and code != GENERIC[0] else GENERIC[0]
    return declared, str(detail), {}


def _own_invalid(field: Any, detail: Any, active: Optional[str]) -> bool:
    """Whether an ``invalid`` failure is DRF's own: its text is the field's own message, in the
    language it was rendered in or in source."""
    message = getattr(field, "error_messages", {}).get("invalid")
    if message is None:
        return False
    text = str(detail)
    for language in (None, active):
        with translation.override(language):
            pattern = re.sub(r"\\\{\w+\\\}", ".*", re.escape(str(message)))
            if re.fullmatch(pattern, text, re.S):
                return True
    return False


def _wording(field: Any, code: Optional[str]) -> Optional[Wording]:
    """``(code, authoring template, marker, marker value)`` for one of DRF's own failures, with
    the bound read from the field; None for a failure DRF does not word."""
    if code in _REQUIRED_CODES:
        return (*REQUIRED, None, None)
    if code == "unique":
        return (*TAKEN, None, None)
    if code == "invalid_choice":
        return (*CHOICE, None, None)
    if code in ("does_not_exist", "no_match", "incorrect_match"):
        return (*NOT_FOUND, None, None)
    if code == "not_a_list":
        return (*LIST, None, None)
    if code == "not_a_dict":
        return (*WORDINGS["object_type"], None, None)
    if code in ("invalid", "invalid_image", "date", "datetime"):
        for kind, wording in _INVALID:
            if isinstance(field, kind):
                return (*wording, None, None)
        return (*FORMAT, None, None)
    if code in _FORMAT_CODES:
        return (*FORMAT, None, None)
    if code in _LENGTH:
        many = isinstance(field, _MANY)
        too, marker = ("small", "min") if code == "min_length" else ("large", "max")
        template = _LENGTH[code][1 if many else 0]
        return size_code([] if many else "", too), template, marker, getattr(field, code, None)
    if code in _VALUE:
        bound = getattr(field, code, None)
        if isinstance(bound, timedelta):
            return (*FORMAT, None, None)
        too, marker = ("small", "min") if code == "min_value" else ("large", "max")
        return size_code(0, too), _VALUE[code], marker, _number(bound)
    if code in _DIGITS:
        return "too_large", _DIGITS[code], "max", getattr(field, _DIGIT_BOUNDS[code], None)
    return None


# -- labels (MSG-10) --------------------------------------------------------------------------


def _label(field: Any, key: str, parent: Any) -> tuple[str, bool]:
    """The label declared for ``key``, and whether one was declared. A field with none is named by
    its key, never by a name guessed from it, and the listing reports it.

    A bound DRF field always has a ``label``, because binding fills a missing one with the key
    humanised. So only a label actually given counts: the unbound declared field's, or the one
    passed when the field was built, which a ``ModelSerializer`` takes from the model's
    ``verbose_name``."""
    declared = getattr(parent, "_declared_fields", {}).get(key)
    if declared is not None and declared.label is not None:
        return str(declared.label), True
    model = getattr(getattr(parent, "Meta", None), "model", None)
    source = getattr(field, "source", None) or key
    if model is not None and declared is None:
        with contextlib.suppress(FieldDoesNotExist):
            verbose = getattr(model._meta.get_field(source), "_verbose_name", None)
            return (str(verbose), True) if verbose else (key, False)
    given = getattr(field, "_kwargs", {}).get("label")
    if given is not None:
        return str(given), True
    return key, False


def _labels(serializer: Any, names: Iterable[str]) -> str:
    fields = getattr(serializer, "fields", {})
    return " and ".join(_label(fields.get(name), name, serializer)[0] for name in names)


# -- listing (MSG-7) --------------------------------------------------------------------------


def declared_templates(serializer_classes: Iterable[type]) -> Iterator[Declared]:
    """Every template ``serializer_classes`` can emit, for the core's listing command (MSG-7).

    A field with no declared label, and a custom validator or ``validate`` method whose templates
    are not named with ``langsys_django.messages.declares``, are reported as problems. Point the
    command at a provider::

        def templates():
            return declared_templates([SignupSerializer, OrderSerializer])

        # python manage.py langsys_messages --provider myapp.langsys:templates [--register]
    """
    yield {"template": FAILED[1], "source": "langsys_django"}
    for _, template in (WORDINGS["body_not_json"], WORDINGS["body_not_object"]):
        yield {"template": template, "source": "langsys_django.drf"}
    with translation.override(None):
        for serializer_class in serializer_classes:
            source = f"{serializer_class.__module__}.{serializer_class.__qualname__}"
            yield from _serializer_templates(serializer_class(), source, "")


def _serializer_templates(serializer: Any, source: str, prefix: str) -> Iterator[Declared]:
    for name, field in serializer.fields.items():
        if field.read_only:
            continue
        path = _join(prefix, name)
        label, declared = _label(field, name, serializer)
        if not declared:
            yield TemplateProblem(
                "the field has no label, so its key would be written into the sentence",
                source=source,
                field=path,
                fix="give the model field a verbose_name, or the serializer field a label (MSG-10)",
            )
            continue
        yield from _field_templates(field, label, source, path)
    for attr, func in sorted(vars(type(serializer)).items()):
        if attr == "validate" or attr.startswith("validate_"):
            yield from _custom(
                func, source, _join(prefix, attr[9:]) if attr != "validate" else prefix, attr
            )
    for validator in serializer.validators:
        if isinstance(validator, UniqueTogetherValidator):
            template = with_label(TAKEN_TOGETHER[1], _labels(serializer, validator.fields))
            yield {"template": template, "source": source}


def _field_templates(field: Any, label: str, source: str, path: str) -> Iterator[Declared]:
    target = field.child if isinstance(field, serializers.ListSerializer) else field
    templates: list[str] = []
    if field.required:
        templates.append(REQUIRED[1])
    if isinstance(target, serializers.BaseSerializer):
        templates.append(WORDINGS["object_type"][1])
        if target is not field:
            templates.append(LIST[1])
        yield from (
            {"template": with_label(t, label), "source": source, "field": path} for t in templates
        )
        yield from _serializer_templates(target, source, path)
        return
    templates.extend(_kind_templates(field))
    child = getattr(field, "child", None)
    if child is not None:
        templates.extend(_kind_templates(child))
    for template in dict.fromkeys(templates):
        yield {"template": with_label(template, label), "source": source, "field": path}
    for validator in (*field.validators, *getattr(child, "validators", ())):
        if not _known(validator):
            yield from _custom(
                validator, source, path, getattr(validator, "__name__", type(validator).__name__)
            )


def _kind_templates(field: Any) -> Iterator[str]:
    if isinstance(field, _MANY):
        yield LIST[1]
    if isinstance(field, drf.ChoiceField):
        yield CHOICE[1]
    if isinstance(field, _RELATED):
        yield NOT_FOUND[1]
    if isinstance(field, drf.DictField):
        yield WORDINGS["object_type"][1]
    if isinstance(field, tuple(kind for kind, _ in _INVALID)):
        wording = _wording(field, "invalid")
        if wording is not None:
            yield wording[1]
    for code in (*_LENGTH, *_VALUE, *_DIGITS):
        if getattr(field, _DIGIT_BOUNDS.get(code, code), None) is not None:
            wording = _wording(field, code)
            if wording is not None:
                yield wording[1]
    if any(isinstance(validator, UniqueValidator) for validator in field.validators):
        yield TAKEN[1]


def _known(validator: Any) -> bool:
    return _is_django_validator(validator) or isinstance(
        validator, (UniqueValidator, ProhibitSurrogateCharactersValidator)
    )
