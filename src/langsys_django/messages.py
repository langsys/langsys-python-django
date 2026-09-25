"""Server messages for Django (spec MSG family): a failed form's validators as entries.

The core's ``langsys.messages`` owns the entry shape, the fill, the template checks and the listing
command. This module supplies what only Django knows:

* which validators failed, and with what parameters (MSG-9): each ``ValidationError``'s ``code`` and
  ``params``, never Django's rendered message;
* the label a field declares: a form field's ``label``, a ``ModelForm``'s ``Meta.labels``, or a
  model field's ``verbose_name`` (MSG-10);
* the default langsys envelope for a failed form, and the template tags that render entries (MSG-5);
* a provider that lists every template a set of forms can emit, for the ``langsys_messages``
  management command (MSG-7).

The wording is the reference's: the label is written into the sentence, and only a value that is
not translatable, a number or a date, stays a ``{name}`` marker (MSG-3). A size rule takes its code
from the field's type through the core's ``size_code``.

Usage::

    form = SignupForm(request.POST)
    if not form.is_valid():
        return error_response(form)   # 422 with the langsys envelope
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterable, Iterator, Mapping
from datetime import date, time
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Callable, Optional, TypeVar, Union

from django import forms
from django.core import validators
from django.core.exceptions import NON_FIELD_ERRORS, FieldDoesNotExist, ValidationError
from django.http import JsonResponse
from django.utils import translation
from langsys.messages import TemplateProblem, server_message, size_code, with_label

from .client import get_client

if TYPE_CHECKING:
    from langsys import LangsysClient

__all__ = [
    "declared_templates",
    "declares",
    "entries_from_form",
    "error_response",
    "message_error",
]

Entry = dict[str, Any]
Declared = Union[str, Mapping[str, Any], TemplateProblem]
F = TypeVar("F", bound=Callable[..., Any])

#: The envelope's own entry.
FAILED = ("validation_failed", "The request failed validation.")

#: Reference wording. `:attribute` is where the label is written in.
GENERIC = ("invalid", "The :attribute is invalid.")
FORMAT = ("invalid_format", "The :attribute format is invalid.")
REQUIRED = ("required", "The :attribute is required.")
TAKEN = ("already_taken", "The :attribute has already been taken.")
TAKEN_TOGETHER = ("already_taken", "This combination of :attribute has already been taken.")
CHOICE = ("invalid_option", "The selected :attribute is invalid.")
NOT_FOUND = ("not_found", "The selected :attribute is invalid.")
LIST = ("invalid_type", "The :attribute must be a list.")

#: A field's own `invalid`, by field type. Order matters: Django's FloatField and DecimalField are
#: IntegerFields, and an ImageField is a FileField.
_INVALID: tuple[tuple[type, tuple[str, str]], ...] = (
    (forms.EmailField, ("invalid_format", "The :attribute must be a valid email address.")),
    (forms.URLField, ("invalid_format", "The :attribute must be a valid URL.")),
    (forms.UUIDField, ("invalid_format", "The :attribute must be a valid UUID.")),
    (forms.FloatField, ("invalid_type", "The :attribute must be a number.")),
    (forms.DecimalField, ("invalid_type", "The :attribute must be a number.")),
    (forms.IntegerField, ("invalid_type", "The :attribute must be a whole number.")),
    (forms.DateTimeField, ("invalid_format", "The :attribute must be a valid date.")),
    (forms.DateField, ("invalid_format", "The :attribute must be a valid date.")),
    (forms.TimeField, ("invalid_format", "The :attribute must be a valid date.")),
    (forms.JSONField, ("invalid_format", "The :attribute must be valid JSON.")),
    (forms.ImageField, ("invalid_format", "The :attribute must be an image.")),
    (forms.FileField, ("invalid_type", "The :attribute must be a file.")),
)
_TEMPORAL = (forms.DateField, forms.DateTimeField, forms.TimeField)

#: Length rules: text wording, then the wording for a field holding a list.
_LENGTH = {
    "min_length": (
        "The :attribute must be at least {min} characters.",
        "The :attribute must have at least {min} items.",
    ),
    "max_length": (
        "The :attribute must not be longer than {max} characters.",
        "The :attribute must not have more than {max} items.",
    ),
}
_VALUE = {
    "min_value": "The :attribute must be at least {min}.",
    "max_value": "The :attribute must not be greater than {max}.",
}
#: A bound that is a date is a date comparison, worded as the reference's after/before rules.
_DATED = {
    "min_value": "The :attribute must be on or after {date}.",
    "max_value": "The :attribute must be on or before {date}.",
}
_DIGITS = {
    "max_digits": "The :attribute must not have more than {max} digits.",
    "max_decimal_places": "The :attribute must not have more than {max} decimal places.",
    "max_whole_digits": (
        "The :attribute must not have more than {max} digits before the decimal point."
    ),
}
_STEP = "The :attribute must be a multiple of {step}."
_REQUIRED_CODES = ("required", "blank", "null", "missing", "empty")
_FORMAT_CODES = ("null_characters_not_allowed", "invalid_extension", "overflow")

#: The params key `message_error()` carries its declared template under.
_TEMPLATE_KEY = "langsys_template"

Wording = tuple[str, str, Optional[str], Any]


def message_error(code: str, template: str, **params: Any) -> ValidationError:
    """A validator failure with a declared template (MSG-9). Raise it from a validator or a form's
    ``clean`` method::

        raise message_error("already_taken", "The email address has already been taken.")

    ``template`` is a whole sentence with the label written in; ``params`` fill its ``{name}``
    markers and hold only values that are not translatable. Name the template with
    :func:`declares` so the listing registers it ahead of time.
    """
    return ValidationError(template, code=code, params={**params, _TEMPLATE_KEY: template})


def declares(*templates: str) -> Callable[[F], F]:
    """Name the templates a custom validator or ``clean`` method can fail with, so
    :func:`declared_templates` lists them rather than reporting it as unlistable."""

    def mark(func: F) -> F:
        func.__langsys_templates__ = templates  # type: ignore[attr-defined]
        return func

    return mark


# -- errors -> entries (MSG-9, MSG-10) --------------------------------------------------------


def entries_from_form(
    form: forms.BaseForm, *, client: Optional[LangsysClient] = None
) -> list[Entry]:
    """Entries for a bound form's errors, in the order Django reports them. With ``client``, each
    entry goes through ``client.server_message`` (MSG-8 registration, MSG-11 check); without, the
    core's pure constructor."""
    build = client.server_message if client is not None else server_message
    form_class = type(form)
    entries: list[Entry] = []
    # Labels and templates are source text, so they are read with Django's own translation off.
    with translation.override(None):
        for name, errors in form.errors.as_data().items():
            field = form.fields.get(name) if name != NON_FIELD_ERRORS else None
            for error in errors:
                if field is None:
                    code, template, params = _form_rule(form_class, error)
                    entries.append(build(code, template, params or None, None))
                    continue
                label, _ = _label(form_class, name, field)
                code, template, params = _rule(field, error, label)
                entries.append(build(code, template, params or None, name))
    return entries


def error_response(form: forms.BaseForm, *, status: int = 422) -> JsonResponse:
    """Answer a failed form with the default langsys envelope:
    ``{"status": false, "error": {code, message, template, "errors": [entry, …]}}``."""
    client = get_client()
    entries = entries_from_form(form, client=client)
    failed = client.server_message(*FAILED)
    return JsonResponse({"status": False, "error": {**failed, "errors": entries}}, status=status)


def _rule(
    field: forms.Field, error: ValidationError, label: str
) -> tuple[str, str, dict[str, Any]]:
    params = dict(error.params or {})
    declared = params.pop(_TEMPLATE_KEY, None)
    if declared is not None:  # message_error(): the app declared the sentence
        return error.code or GENERIC[0], str(declared), params
    wording = _wording(field, error.code, params.get("limit_value", params.get("max")))
    if wording is None:
        # Text only: whoever raised it wrote this sentence, so it is the template.
        return GENERIC[0], str(error.messages[0]), {}
    code, template, marker, value = wording
    return code, with_label(template, label), ({marker: value} if marker else {})


def _form_rule(form_class: type, error: ValidationError) -> tuple[str, str, dict[str, Any]]:
    """A failure that belongs to the form as a whole: it carries no ``field``."""
    params = dict(error.params or {})
    declared = params.pop(_TEMPLATE_KEY, None)
    if declared is not None:
        return error.code or GENERIC[0], str(declared), params
    if error.code == "unique_together":
        names = params.get("unique_check") or ()
        labels = " and ".join(_label(form_class, name, None)[0] for name in names)
        return TAKEN_TOGETHER[0], with_label(TAKEN_TOGETHER[1], labels), {}
    return GENERIC[0], str(error.messages[0]), {}


def _wording(
    field: Optional[forms.Field], code: Optional[str], bound: Any = None
) -> Optional[Wording]:
    """``(code, authoring template, marker, marker value)`` for a Django failure, or None when the
    failure is not one of Django's own rules."""
    if code in _REQUIRED_CODES:
        return (*REQUIRED, None, None)
    if code == "unique":
        return (*TAKEN, None, None)
    if code == "invalid_choice":
        return (*(NOT_FOUND if isinstance(field, forms.ModelChoiceField) else CHOICE), None, None)
    if code == "invalid_pk_value":
        return (*NOT_FOUND, None, None)
    if code == "invalid_list":
        return (*LIST, None, None)
    if code in ("invalid", "invalid_image"):
        for kind, wording in _INVALID:
            if isinstance(field, kind):
                return (*wording, None, None)
        return (*FORMAT, None, None)
    if code in _FORMAT_CODES:
        return (*FORMAT, None, None)
    if code in _LENGTH:
        many = isinstance(field, forms.MultipleChoiceField)
        too, marker = ("small", "min") if code == "min_length" else ("large", "max")
        return size_code([] if many else "", too), _LENGTH[code][1 if many else 0], marker, bound
    if code in _VALUE:
        too, marker = ("small", "min") if code == "min_value" else ("large", "max")
        if isinstance(field, _TEMPORAL) or isinstance(bound, (date, time)):
            shown = bound.isoformat() if isinstance(bound, (date, time)) else bound
            return "invalid_date", _DATED[code], "date", shown
        return size_code(0, too), _VALUE[code], marker, _number(bound)
    if code in _DIGITS:
        return "too_large", _DIGITS[code], "max", bound
    if code == "step_size":
        return GENERIC[0], _STEP, "step", _number(bound)
    return None


def _number(value: Any) -> Any:
    if isinstance(value, Decimal):
        return int(value) if value == value.to_integral_value() else float(value)
    return value


# -- labels (MSG-10) --------------------------------------------------------------------------


def _label(form_class: type, name: str, field: Optional[forms.Field]) -> tuple[str, bool]:
    """The label declared for ``name``, and whether one was declared. A field with none is named by
    its key, never by a name guessed from it, and the listing reports it."""
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
            verbose = getattr(model._meta.get_field(name), "_verbose_name", None)
            return (str(verbose), True) if verbose else (name, False)
    if field is not None and field.label is not None:
        return str(field.label), True
    return name, False


# -- listing (MSG-7) --------------------------------------------------------------------------

#: Django's own validators, and the failure codes each can raise.
_VALIDATOR_CODES: tuple[tuple[Any, tuple[str, ...]], ...] = (
    (validators.MinLengthValidator, ("min_length",)),
    (validators.MaxLengthValidator, ("max_length",)),
    (validators.MinValueValidator, ("min_value",)),
    (validators.MaxValueValidator, ("max_value",)),
    (validators.ProhibitNullCharactersValidator, ("null_characters_not_allowed",)),
    (validators.FileExtensionValidator, ("invalid_extension",)),
    (validators.EmailValidator, ("invalid",)),
    (validators.RegexValidator, ("invalid",)),
)
if hasattr(validators, "StepValueValidator"):
    _VALIDATOR_CODES += ((validators.StepValueValidator, ("step_size",)),)
#: Django's validators that are plain functions; its slug and email validators are instances of
#: the classes above.
_FUNCTION_VALIDATORS = (
    validators.validate_ipv4_address,
    validators.validate_ipv6_address,
    validators.validate_ipv46_address,
    validators.validate_integer,
)


def declared_templates(form_classes: Iterable[type]) -> Iterator[Declared]:
    """Every template ``form_classes`` can emit, for the core's listing command (MSG-7).

    A field with no declared label, and a custom validator or ``clean`` method whose templates are
    not named with :func:`declares`, are reported as problems, so the command fails in CI rather
    than a raw key or an unlisted sentence reaching a user. Point the command at a provider::

        def templates():
            return declared_templates([SignupForm, ProfileForm])

        # python manage.py langsys_messages --provider myapp.langsys:templates [--register]
    """
    yield {"template": FAILED[1], "source": "langsys_django"}
    with translation.override(None):
        for form_class in form_classes:
            yield from _form_templates(form_class)


def _form_templates(form_class: Any) -> Iterator[Declared]:
    source = f"{form_class.__module__}.{form_class.__qualname__}"
    model = getattr(getattr(form_class, "_meta", None), "model", None)
    for name, field in form_class.base_fields.items():
        label, declared = _label(form_class, name, field)
        if not declared:
            yield TemplateProblem(
                "the field has no label, so its key would be written into the sentence",
                source=source,
                field=name,
                fix="give the model field a verbose_name, or the form field a label (MSG-10)",
            )
            continue
        for template in dict.fromkeys(_field_templates(field, model, name)):
            yield {"template": with_label(template, label), "source": source, "field": name}
        for validator in field.validators:
            if _is_django_validator(validator):
                continue
            yield from _custom(
                validator, source, name, getattr(validator, "__name__", type(validator).__name__)
            )
    for attr in sorted(vars(form_class)):
        if attr == "clean" or attr.startswith("clean_"):
            yield from _custom(
                vars(form_class)[attr], source, attr[6:] if attr != "clean" else "", attr
            )
    if model is not None:
        for together in model._meta.unique_together:
            labels = " and ".join(_label(form_class, name, None)[0] for name in together)
            yield {"template": with_label(TAKEN_TOGETHER[1], labels), "source": source}


def _field_templates(field: forms.Field, model: Any, name: str) -> Iterator[str]:
    if field.required:
        yield REQUIRED[1]
    if isinstance(field, forms.MultipleChoiceField):
        yield LIST[1]
    if isinstance(field, (forms.ChoiceField, forms.ModelChoiceField)):
        yield (NOT_FOUND if isinstance(field, forms.ModelChoiceField) else CHOICE)[1]
    if isinstance(field, tuple(kind for kind, _ in _INVALID)):
        wording = _wording(field, "invalid")
        if wording is not None:
            yield wording[1]
    for validator in field.validators:
        for code in _codes_of(validator):
            wording = _wording(field, code, getattr(validator, "limit_value", None))
            if wording is not None:
                yield wording[1]
    if model is not None:
        with contextlib.suppress(FieldDoesNotExist):
            if model._meta.get_field(name).unique:
                yield TAKEN[1]


def _codes_of(validator: Any) -> tuple[str, ...]:
    if isinstance(validator, validators.DecimalValidator):
        return tuple(
            code
            for code, limit in (
                ("max_digits", validator.max_digits),
                ("max_decimal_places", validator.decimal_places),
                ("max_whole_digits", validator.max_digits and validator.decimal_places),
            )
            if limit is not None
        )
    if any(validator is function for function in _FUNCTION_VALIDATORS):
        return ("invalid",)
    for kind, codes in _VALIDATOR_CODES:
        if isinstance(validator, kind):
            return codes
    return ()


def _is_django_validator(validator: Any) -> bool:
    return bool(_codes_of(validator)) or isinstance(validator, validators.DecimalValidator)


def _custom(func: Any, source: str, field: str, name: str) -> Iterator[Declared]:
    declared = getattr(func, "__langsys_templates__", None)
    if declared is not None:
        for template in declared:
            yield {"template": template, "source": source, "field": field}
        return
    yield TemplateProblem(
        f"{name!r} can fail with text that cannot be listed ahead of time",
        source=source,
        field=field,
        fix="fail with langsys_django.messages.message_error(code, template) and name its "
        "templates with @declares(...)",
    )
