"""Server messages from Django forms (spec MSG family).

Entries are built from the validators that failed, their ``code`` and ``params``, never from
Django's rendered text (MSG-9). The label written into each sentence is the one the field declares,
never a name guessed from its key (MSG-10). Every template a form can emit is listable ahead of time,
and a message that cannot be listed fails the listing (MSG-7).
"""

from __future__ import annotations

import json
import re
from datetime import date

import pytest
from django import forms
from django.core import validators
from django.core.exceptions import ValidationError
from django.core.management import call_command
from django.db import models
from django.template import Context, Template
from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.messages import TemplateProblem

from langsys_django.client import reset_client, set_client
from langsys_django.locale import ContextVarLocaleSource, reset_current_locale, set_current_locale
from langsys_django.messages import (
    declared_templates,
    declares,
    entries_from_form,
    error_response,
    message_error,
)

TRANS = re.compile(r"https://api\.test/api/translations")


class Account(models.Model):
    email = models.EmailField(verbose_name="email address", max_length=40)
    cc_number = models.CharField(max_length=20)

    class Meta:
        app_label = "langsys_django"


class Membership(models.Model):
    handle = models.CharField(verbose_name="handle", max_length=20, unique=True)
    team = models.CharField(verbose_name="team", max_length=20)
    role = models.CharField(verbose_name="role", max_length=20)

    class Meta:
        app_label = "langsys_django"
        unique_together = [("team", "role")]


class Signup(forms.Form):
    email = forms.EmailField(label="email address", min_length=8)


def no_reserved_names(value: str) -> None:
    if "admin" in value:
        raise ValidationError("That name is reserved.")


@declares("That name is reserved.")
def declared_no_reserved_names(value: str) -> None:
    no_reserved_names(value)


def entry_view(entries):
    return [(e["code"], e["template"], e.get("params")) for e in entries]


def clean_templates():
    return declared_templates([Signup])


def loose_templates():
    class Loose(forms.Form):
        code = forms.CharField()

    return declared_templates([Loose])


# -- MSG-9: the rules that failed, never the rendered text --------------------------------------


def test_MSG9_two_failed_rules_on_one_field_become_two_entries():
    form = Signup({"email": "a@b"})
    assert not form.is_valid()

    entries = entries_from_form(form)

    assert entry_view(entries) == [
        ("invalid_format", "The email address must be a valid email address.", None),
        ("too_short", "The email address must be at least {min} characters.", {"min": 8}),
    ]
    assert entries[1]["message"] == "The email address must be at least 8 characters."
    assert {e["field"] for e in entries} == {"email"}


def test_MSG9_a_text_only_failure_becomes_invalid_with_its_own_text():
    class Named(forms.Form):
        name = forms.CharField(label="name", validators=[no_reserved_names])

    entries = entries_from_form(Named({"name": "admin"}))

    assert entry_view(entries) == [("invalid", "That name is reserved.", None)]


def test_MSG9_a_declared_template_is_used_as_written():
    class Taken(forms.Form):
        email = forms.EmailField(label="email address")

        def clean_email(self):
            raise message_error("already_taken", "The email address has already been taken.")

    entries = entries_from_form(Taken({"email": "ada@example.com"}))

    assert entry_view(entries) == [
        ("already_taken", "The email address has already been taken.", None)
    ]


def test_MSG9_a_form_wide_failure_carries_no_field():
    class Whole(forms.Form):
        def clean(self):
            raise ValidationError("The two dates overlap.")

    entries = entries_from_form(Whole({}))

    assert entry_view(entries) == [("invalid", "The two dates overlap.", None)]
    assert "field" not in entries[0]


# -- MSG-10: the label the field declares ------------------------------------------------------


def test_MSG10_a_model_field_labels_with_its_verbose_name_and_an_undeclared_one_by_its_key():
    class AccountForm(forms.ModelForm):
        class Meta:
            model = Account
            fields = ["email", "cc_number"]

    entries = entries_from_form(AccountForm({"email": "", "cc_number": ""}))

    assert [e["template"] for e in entries] == [
        "The email address is required.",
        "The cc_number is required.",
    ], "a label guessed from the key reads 'Cc number'"


def test_MSG10_meta_labels_and_form_field_labels_are_declarations():
    class Relabelled(forms.ModelForm):
        class Meta:
            model = Account
            fields = ["email"]
            labels = {"email": "work email"}

    class Plain(forms.Form):
        cc_number = forms.CharField()

    assert entry_view(entries_from_form(Relabelled({"email": ""}))) == [
        ("required", "The work email is required.", None)
    ]
    assert entry_view(entries_from_form(Plain({}))) == [
        ("required", "The cc_number is required.", None)
    ]


# -- MSG-2 / MSG-3 / MSG-4: codes by type, whole sentences, numbers as numbers ------------------


def test_MSG2_a_size_rule_takes_its_code_from_the_field_type():
    class Sized(forms.Form):
        nickname = forms.CharField(label="nickname", min_length=3)
        seats = forms.IntegerField(label="seats", min_value=10)
        guests = forms.IntegerField(label="guests", max_value=3)
        starts = forms.DateField(
            label="start date", validators=[validators.MinValueValidator(date(2026, 1, 1))]
        )
        price = forms.DecimalField(label="price", max_digits=3, decimal_places=1)

    form = Sized(
        {"nickname": "a", "seats": "5", "guests": "9", "starts": "2025-06-01", "price": "123.4"}
    )

    assert entry_view(entries_from_form(form)) == [
        ("too_short", "The nickname must be at least {min} characters.", {"min": 3}),
        ("too_small", "The seats must be at least {min}.", {"min": 10}),
        ("too_large", "The guests must not be greater than {max}.", {"max": 3}),
        ("invalid_date", "The start date must be on or after {date}.", {"date": "2026-01-01"}),
        ("too_large", "The price must not have more than {max} digits.", {"max": 3}),
    ]


def test_MSG3_each_label_is_its_own_phrase_and_a_markerless_template_is_its_message():
    class Pair(forms.Form):
        password = forms.CharField(label="password")
        name = forms.CharField(label="name")

    entries = entries_from_form(Pair({}))

    assert [e["template"] for e in entries] == [
        "The password is required.",
        "The name is required.",
    ]
    assert all(e["message"] == e["template"] and "params" not in e for e in entries)


def test_MSG4_a_numeric_param_is_a_json_number():
    entry = entries_from_form(Signup({"email": "a@b.co"}))[0]

    assert json.loads(json.dumps(entry))["params"] == {"min": 8}
    assert isinstance(entry["params"]["min"], int)


# -- MSG-7: every template listable ahead of time ----------------------------------------------


def test_MSG7_the_provider_lists_each_template_with_its_label_written_in():
    listed = list(declared_templates([Signup]))

    assert not [item for item in listed if isinstance(item, TemplateProblem)]
    assert {item["template"] for item in listed} >= {
        "The email address is required.",
        "The email address must be a valid email address.",
        "The email address must be at least {min} characters.",
    }


def test_MSG7_an_unlabelled_field_and_an_undeclared_validator_are_problems():
    class Loose(forms.Form):
        code = forms.CharField()
        name = forms.CharField(label="name", validators=[no_reserved_names])

    problems = [item for item in declared_templates([Loose]) if isinstance(item, TemplateProblem)]

    assert sorted(problem.field for problem in problems) == ["code", "name"]


def test_MSG7_a_declared_validator_is_listed_rather_than_reported():
    class Declared(forms.Form):
        name = forms.CharField(label="name", validators=[declared_no_reserved_names])

    listed = list(declared_templates([Declared]))

    assert not [item for item in listed if isinstance(item, TemplateProblem)]
    assert "That name is reserved." in {item["template"] for item in listed}


def test_MSG7_a_model_forms_uniqueness_is_listed():
    class MembershipForm(forms.ModelForm):
        class Meta:
            model = Membership
            fields = ["handle", "team", "role"]

    templates = {item["template"] for item in declared_templates([MembershipForm])}

    assert "The handle has already been taken." in templates
    assert "This combination of team and role has already been taken." in templates


def test_MSG7_the_command_passes_clean_and_fails_naming_each_problem(capsys):
    call_command("langsys_messages", "--provider", "tests.test_messages:clean_templates")
    assert "The email address must be a valid email address." in capsys.readouterr().out

    with pytest.raises(SystemExit) as failed:
        call_command("langsys_messages", "--provider", "tests.test_messages:loose_templates")
    assert failed.value.code != 0
    assert "PROBLEM" in capsys.readouterr().out


# -- MSG-5 and the default envelope ------------------------------------------------------------


@pytest.fixture()
def spanish(httpx_mock):
    httpx_mock.add_response(
        url=TRANS,
        json={
            "status": True,
            "words": 0,
            "untranslatedWords": 0,
            "data": {
                "Errors": {
                    "The email address is required.": "El correo electrónico es obligatorio.",
                    "The email address must be at least 8 characters.": "NOT A LOOKUP KEY",
                }
            },
        },
        is_reusable=True,
    )
    reset_client()
    set_client(
        LangsysClient(
            "k",
            "proj-1",
            api_url="https://api.test/api",
            base_locale="en-US",
            cache=MemoryCache(),
            locale_source=ContextVarLocaleSource(),
            debounce=None,
            auto_flush=False,
        )
    )
    token = set_current_locale("es-ES")
    yield
    reset_current_locale(token)
    reset_client()


def render(source: str, **context) -> str:
    return Template("{% load langsys %}" + source).render(Context(context))


def test_MSG5_an_entry_renders_its_translation_and_otherwise_its_message(spanish):
    rendered = render(
        "{% for entry in form|langsys_errors %}[{% t_message entry %}]{% endfor %}",
        form=Signup({}),
    )

    assert rendered == "[El correo electrónico es obligatorio.]"


def test_MSG5_the_filled_message_is_never_the_lookup_key(spanish):
    """The catalog holds a 'translation' keyed by the filled message; rendering must not use it."""
    rendered = render(
        "{% for entry in form|langsys_errors %}[{% t_message entry %}]{% endfor %}",
        form=Signup({"email": "a@x.com"}),  # valid, and one short of min_length
    )

    assert rendered == "[The email address must be at least 8 characters.]"


def test_MSG1_a_failed_form_answers_with_the_default_envelope(spanish):
    response = error_response(Signup({}))
    body = json.loads(response.content)

    assert response.status_code == 422
    assert body["status"] is False
    assert body["error"]["code"] == "validation_failed"
    assert [(e["code"], e["field"]) for e in body["error"]["errors"]] == [("required", "email")]
