"""Server messages from Django forms (spec MSG family).

Entries are built from the ``ValidationError`` Django raised: its unfilled ``message``, its
``params`` and its ``code``, never Django's rendered text (MSG-9). Templates are Django's own
sentences in the source language; only a label Django writes into the sentence is written in, and
every other value is a ``{name}`` marker (MSG-3). Codes are Django's own (MSG-2). The error body is
Django's own, with the entries beside it (MSG-1).
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
    LabelAdvice,
    declared_templates,
    declares,
    entries_from_form,
    entry_parts,
    error_response,
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


@declares("%(field_label)s is reserved.")
def labelled_no_reserved_names(value: str) -> None:
    no_reserved_names(value)


def entry_view(entries):
    return [(e.get("code"), e["template"], e.get("params")) for e in entries]


def clean_templates():
    return declared_templates([Signup])


def loose_templates():
    class Loose(forms.Form):
        code = forms.CharField()
        name = forms.CharField(label="name", validators=[no_reserved_names])

    return declared_templates([Loose])


def labelled_templates():
    class Labelled(forms.Form):
        name = forms.CharField(label="name", validators=[labelled_no_reserved_names])

    return declared_templates([Labelled])


# -- MSG-9: the failure Django raised, never its rendered text ----------------------------------


def test_MSG9_two_failed_rules_on_one_field_become_two_entries():
    entries = entries_from_form(Signup({"email": "a@b"}))

    assert entry_view(entries) == [
        ("invalid", "Enter a valid email address.", None),
        (
            "min_length",
            "Ensure this value has at least {limit_value} characters (it has {show_value}).",
            {"limit_value": 8, "show_value": 3},
        ),
    ]
    assert entries[1]["message"] == "Ensure this value has at least 8 characters (it has 3)."
    assert {e["field"] for e in entries} == {"email"}


def test_MSG9_a_text_only_failure_is_its_own_text_with_no_code_or_params():
    class Named(forms.Form):
        name = forms.CharField(label="name", validators=[no_reserved_names])

    entries = entries_from_form(Named({"name": "admin"}))

    assert entry_view(entries) == [(None, "That name is reserved.", None)]
    assert "code" not in entries[0]


def test_MSG9_an_apps_own_message_and_code_follow_djangos_convention():
    class Taken(forms.Form):
        email = forms.EmailField(label="email address")

        def clean_email(self):
            raise ValidationError(
                "At most %(limit)s accounts share an address.",
                code="already_taken",
                params={"limit": 2},
            )

    assert entry_view(entries_from_form(Taken({"email": "ada@example.com"}))) == [
        ("already_taken", "At most {limit} accounts share an address.", {"limit": 2})
    ]


def test_MSG9_a_form_wide_failure_carries_no_field():
    class Whole(forms.Form):
        def clean(self):
            raise ValidationError("The two dates overlap.", code="overlap")

    entries = entries_from_form(Whole({}))

    assert entry_view(entries) == [("overlap", "The two dates overlap.", None)]
    assert "field" not in entries[0]


# -- MSG-2 / MSG-3 / MSG-4: Django's codes and sentences, values as markers ---------------------


def test_MSG2_each_failure_keeps_djangos_own_code_and_sentence():
    class Sized(forms.Form):
        seats = forms.IntegerField(label="seats", min_value=10)
        kind = forms.ChoiceField(label="kind", choices=[("a", "A")])
        starts = forms.DateField(
            label="start date", validators=[validators.MinValueValidator(date(2026, 1, 1))]
        )
        price = forms.DecimalField(label="price", max_digits=3, decimal_places=1)

    form = Sized({"seats": "5", "kind": "zz", "starts": "2025-06-01", "price": "123.4"})

    assert entry_view(entries_from_form(form)) == [
        (
            "min_value",
            "Ensure this value is greater than or equal to {limit_value}.",
            {"limit_value": 10},
        ),
        (
            "invalid_choice",
            "Select a valid choice. {value} is not one of the available choices.",
            {"value": "zz"},
        ),
        (
            "min_value",
            "Ensure this value is greater than or equal to {limit_value}.",
            {"limit_value": "2026-01-01"},
        ),
        ("max_digits", "Ensure that there are no more than {max} digits in total.", {"max": 3}),
    ]


def test_MSG3_a_sentence_that_names_no_field_is_one_template_as_django_wrote_it():
    class Pair(forms.Form):
        password = forms.CharField(label="password")
        name = forms.CharField(label="name")

    entries = entries_from_form(Pair({}))

    assert [e["template"] for e in entries] == ["This field is required."] * 2
    assert all(e["message"] == e["template"] and "params" not in e for e in entries)


def test_MSG3_a_sentence_that_names_the_field_has_djangos_label_written_in():
    handle = entry_parts(Membership().unique_error_message(Membership, ("handle",)))
    pair = entry_parts(Membership().unique_error_message(Membership, ("team", "role")))

    assert handle == ("unique", "Membership with this Handle already exists.", None)
    assert pair == ("unique_together", "Membership with this Team and Role already exists.", None)


def test_MSG4_a_numeric_param_is_a_json_number():
    entry = entries_from_form(Signup({"email": "a@b.co"}))[0]

    assert json.loads(json.dumps(entry))["params"]["limit_value"] == 8
    assert isinstance(entry["params"]["limit_value"], int)


# -- MSG-7 / MSG-10 / MSG-11: listing ahead of time ----------------------------------------------


def test_MSG7_the_provider_lists_djangos_own_sentences():
    listed = list(declared_templates([Signup]))

    assert not [item for item in listed if isinstance(item, (TemplateProblem, LabelAdvice))]
    assert {item["template"] for item in listed} >= {
        "This field is required.",
        "Enter a valid email address.",
        "Ensure this value has at least {limit_value} characters (it has {show_value}).",
    }


def test_MSG7_a_plural_message_lists_the_form_its_bound_selects():
    class One(forms.Form):
        pin = forms.CharField(label="pin", min_length=1)

    templates = {item["template"] for item in declared_templates([One])}

    assert "Ensure this value has at least {limit_value} character (it has {show_value})." in (
        templates
    )


def test_MSG7_an_undeclared_validator_is_a_problem_and_a_declared_one_is_listed():
    class Declared(forms.Form):
        name = forms.CharField(label="name", validators=[declared_no_reserved_names])

    loose = [item for item in loose_templates() if isinstance(item, TemplateProblem)]
    listed = list(declared_templates([Declared]))

    assert [problem.field for problem in loose] == ["name"]
    assert not [item for item in listed if isinstance(item, TemplateProblem)]
    assert "That name is reserved." in {item["template"] for item in listed}


def test_MSG7_a_model_forms_uniqueness_is_listed_with_djangos_labels():
    class MembershipForm(forms.ModelForm):
        class Meta:
            model = Membership
            fields = ["handle", "team", "role"]

    templates = {item["template"] for item in declared_templates([MembershipForm])}

    assert "Membership with this Handle already exists." in templates
    assert "Membership with this Team and Role already exists." in templates


def test_MSG10_an_undeclared_label_is_advice_naming_the_field():
    class AccountForm(forms.ModelForm):
        class Meta:
            model = Account
            fields = ["email", "cc_number"]

    advice = [item for item in declared_templates([AccountForm]) if isinstance(item, LabelAdvice)]

    assert [(item.field, item.label) for item in advice] == [("cc_number", "cc number")]


def test_MSG7_the_command_reports_problems_and_fails_only_under_strict(capsys):
    call_command("langsys_messages", "--provider", "tests.test_messages:clean_templates")
    assert "Enter a valid email address." in capsys.readouterr().out

    call_command("langsys_messages", "--provider", "tests.test_messages:loose_templates")
    out = capsys.readouterr().out
    assert "PROBLEM" in out
    assert "ADVICE" in out and "'code'" in out

    with pytest.raises(SystemExit) as failed:
        call_command(
            "langsys_messages", "--provider", "tests.test_messages:loose_templates", "--strict"
        )
    assert failed.value.code != 0


def test_MSG11_a_template_holding_a_django_label_placeholder_is_refused(capsys):
    call_command("langsys_messages", "--provider", "tests.test_messages:labelled_templates")
    out = capsys.readouterr().out

    assert "PROBLEM" in out and "%(field_label)s" in out
    assert not re.search(r"^%\(field_label\)s is reserved\.", out, re.M)


# -- MSG-5 rendering, and MSG-1's body ---------------------------------------------------------


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
                    "This field is required.": "Este campo es obligatorio.",
                    "Ensure this value has at least 8 characters (it has 7).": "NOT A LOOKUP KEY",
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

    assert rendered == "[Este campo es obligatorio.]"


def test_MSG5_the_filled_message_is_never_the_lookup_key(spanish):
    """The catalog holds a 'translation' keyed by the filled message; rendering must not use it."""
    rendered = render(
        "{% for entry in form|langsys_errors %}[{% t_message entry %}]{% endfor %}",
        form=Signup({"email": "a@x.com"}),  # valid, and one short of min_length
    )

    assert rendered == "[Ensure this value has at least 8 characters (it has 7).]"


def test_MSG1_a_failed_form_answers_with_djangos_body_and_the_entries_beside_it(spanish, settings):
    form = Signup({})
    response = error_response(form)
    body = json.loads(response.content)
    entries = body.pop("langsys_errors")

    assert response.status_code == 400
    assert body == Signup({}).errors.get_json_data()
    assert [(e["code"], e["field"], e["template"]) for e in entries] == [
        ("required", "email", "This field is required.")
    ]

    settings.LANGSYS = {"RESPONSE_KEY": "translatable"}
    assert "translatable" in json.loads(error_response(Signup({})).content)
