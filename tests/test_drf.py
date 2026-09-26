"""Server messages from Django REST framework serializers (spec MSG family).

Each entry carries DRF's own code and DRF's own sentence as its template, its values read from the
field and the input where DRF reads them, never parsed from DRF's rendered text (MSG-2, MSG-3,
MSG-9). DRF's error response is left as it is, with the entries beside it (MSG-1). Every template a
serializer can emit is listable ahead of time (MSG-7).
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from django.core.management import call_command
from django.core.validators import MinValueValidator
from django.urls import path
from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.messages import TemplateProblem
from rest_framework import generics, serializers
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.test import APIClient

from langsys_django.client import reset_client, set_client
from langsys_django.drf import declared_templates, entries_from_serializer
from langsys_django.locale import ContextVarLocaleSource
from langsys_django.messages import LabelAdvice, declares
from tests.test_messages import Account, Membership

pytestmark = [
    pytest.mark.urls("tests.test_drf"),
    pytest.mark.httpx_mock(assert_all_responses_were_requested=False),
]

TRANS = re.compile(r"https://api\.test/api/translations")
NOT_AN_OBJECT = "Invalid data. Expected a dictionary, but got {datatype}."


def no_reserved_names(value: str) -> None:
    if "admin" in value:
        raise serializers.ValidationError("That name is reserved.")


@declares("The fields {field_names} clash.")
def no_clashing_fields(value: str) -> None:
    no_reserved_names(value)


def clashing_templates():
    class Clashing(serializers.Serializer):
        name = serializers.CharField(label="name", validators=[no_clashing_fields])

    return declared_templates([Clashing])


class SignupSerializer(serializers.Serializer):
    email = serializers.EmailField(label="email address", min_length=8)


class Address(serializers.Serializer):
    city = serializers.CharField(label="city")


class Item(serializers.Serializer):
    name = serializers.CharField(label="item name", max_length=3)


class Order(serializers.Serializer):
    address = Address(label="address")
    items = Item(many=True, label="items")
    tags = serializers.ListField(child=serializers.CharField(max_length=5), label="tags")


class AccountSerializer(serializers.ModelSerializer):
    class Meta:
        model = Account
        fields = ["email", "cc_number"]


class MembershipSerializer(serializers.ModelSerializer):
    class Meta:
        model = Membership
        fields = ["handle", "team", "role"]


class SignupView(generics.GenericAPIView):
    serializer_class = SignupSerializer
    authentication_classes: list = []
    permission_classes: list = []
    renderer_classes = [JSONRenderer]

    def post(self, request):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return Response({"ok": True})


urlpatterns = [path("signup/", SignupView.as_view())]


def entry_view(entries):
    return [(e.get("code"), e["template"], e.get("params"), e.get("field")) for e in entries]


def errors_of(serializer_class, data):
    serializer = serializer_class(data=data)
    assert not serializer.is_valid()
    return entries_from_serializer(serializer)


# -- MSG-9 / MSG-2 / MSG-3: DRF's code and sentence, values from the field ----------------------


def test_MSG9_two_failed_rules_on_one_field_become_two_entries():
    entries = errors_of(SignupSerializer, {"email": "a@b"})

    assert entry_view(entries) == [
        (
            "min_length",
            "Ensure this field has at least {min_length} characters.",
            {"min_length": 8},
            "email",
        ),
        ("invalid", "Enter a valid email address.", None, "email"),
    ]
    assert entries[0]["message"] == "Ensure this field has at least 8 characters."


def test_MSG9_a_validators_own_sentence_is_kept_as_written_with_drfs_code():
    class Named(serializers.Serializer):
        name = serializers.CharField(label="name", validators=[no_reserved_names])

    assert entry_view(errors_of(Named, {"name": "admin"})) == [
        ("invalid", "That name is reserved.", None, "name")
    ]


def test_MSG9_a_django_validator_on_a_drf_field_keeps_djangos_sentence():
    class Seats(serializers.Serializer):
        seats = serializers.IntegerField(label="seats", validators=[MinValueValidator(3)])

    assert entry_view(errors_of(Seats, {"seats": 1})) == [
        (
            "min_value",
            "Ensure this value is greater than or equal to {limit_value}.",
            {"limit_value": 3},
            "seats",
        )
    ]


def test_MSG2_an_apps_code_passes_through():
    class Taken(serializers.Serializer):
        email = serializers.EmailField(label="email address")

        def validate_email(self, value):
            raise serializers.ValidationError(
                "The email address has already been taken.", code="already_taken"
            )

    assert entry_view(errors_of(Taken, {"email": "ada@example.com"})) == [
        ("already_taken", "The email address has already been taken.", None, "email")
    ]


def test_MSG1_fields_are_dotted_paths_through_nested_serializers_and_lists():
    data = {
        "address": {},
        "items": [{"name": "ok"}, {"name": "toolong"}],
        "tags": ["a", "waytoolong"],
    }

    too_long = "Ensure this field has no more than {max_length} characters."
    assert entry_view(errors_of(Order, data)) == [
        ("required", "This field is required.", None, "address.city"),
        ("max_length", too_long, {"max_length": 3}, "items.1.name"),
        ("max_length", too_long, {"max_length": 5}, "tags.1"),
    ]


def test_MSG3_a_value_that_is_not_an_object_names_its_type_in_a_marker():
    nested = errors_of(Order, {"address": "Paris", "items": [], "tags": []})
    body = errors_of(SignupSerializer, ["not", "an", "object"])

    assert entry_view(nested)[0] == ("invalid", NOT_AN_OBJECT, {"datatype": "str"}, "address")
    assert entry_view(body) == [("invalid", NOT_AN_OBJECT, {"datatype": "list"}, None)]


def test_MSG2_size_rules_keep_drfs_codes_and_bounds():
    class Sized(serializers.Serializer):
        seats = serializers.IntegerField(label="seats", min_value=10)
        price = serializers.DecimalField(label="price", max_digits=3, decimal_places=1)
        guests = serializers.ListField(label="guests", min_length=2)

    entries = errors_of(Sized, {"seats": 5, "price": "123.4", "guests": ["ada"]})

    assert entry_view(entries) == [
        (
            "min_value",
            "Ensure this value is greater than or equal to {min_value}.",
            {"min_value": 10},
            "seats",
        ),
        (
            "max_digits",
            "Ensure that there are no more than {max_digits} digits in total.",
            {"max_digits": 3},
            "price",
        ),
        (
            "min_length",
            "Ensure this field has at least {min_length} elements.",
            {"min_length": 2},
            "guests",
        ),
    ]


def test_MSG3_a_choice_names_the_rejected_input_in_a_marker():
    class Kind(serializers.Serializer):
        kind = serializers.ChoiceField(label="kind", choices=["a", "b"])

    assert entry_view(errors_of(Kind, {"kind": "zz"})) == [
        ("invalid_choice", '"{input}" is not a valid choice.', {"input": "zz"}, "kind")
    ]


# -- DRF's error response, with the entries beside it (MSG-1, MSG-8) ---------------------------


@pytest.fixture()
def api(settings, httpx_mock):
    settings.REST_FRAMEWORK = {"EXCEPTION_HANDLER": "langsys_django.drf.exception_handler"}
    httpx_mock.add_response(
        url=re.compile(r"https://api\.test/api/authorize-project/"),
        json={
            "status": True,
            "data": {
                "id": "proj-1",
                "title": "T",
                "base_locale": "en-us",
                "target_locales": [],
                "default_locales": {},
                "key_type": "write",
                "write_enabled": True,
                "langsys_settings": {"translatable_items": {"batch_limit": 200}},
            },
        },
        is_reusable=True,
    )
    httpx_mock.add_response(
        url=TRANS,
        json={"status": True, "words": 0, "untranslatedWords": 0, "data": {}},
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
    registered: list[str] = []

    def accept(request):
        registered.extend(
            item["phrase"] for item in json.loads(request.content)["translatable_items"]
        )
        return httpx.Response(200, json={"status": True})

    httpx_mock.add_callback(
        accept, url=re.compile(r"https://api\.test/api/translatable-items"), is_reusable=True
    )
    client = APIClient()
    client.registered = registered
    yield client
    reset_client()


def test_MSG1_a_failed_request_keeps_drfs_body_with_the_entries_beside_it(api):
    response = api.post("/signup/", {"email": ""}, format="json")
    body = json.loads(response.content)
    key = "langsys_errors"

    assert response.status_code == 400
    assert set(body) == {"email", key}
    assert body.pop("email") == ["This field may not be blank."]
    assert [(e["code"], e["field"], e["template"]) for e in body[key]] == [
        ("blank", "email", "This field may not be blank.")
    ]
    # MSG-8: a template the catalog did not list is registered after the response.
    assert api.registered == ["This field may not be blank."]


def test_MSG1_the_key_the_entries_sit_under_is_configurable(api, settings):
    settings.LANGSYS = {"RESPONSE_KEY": "translatable"}

    body = json.loads(api.post("/signup/", {"email": ""}, format="json").content)

    assert set(body) == {"email", "translatable"}


def test_MSG1_a_response_that_is_not_a_failed_validation_is_drfs_own(api):
    response = api.post("/signup/", data="{not json", content_type="application/json")

    assert response.status_code == 400
    assert set(json.loads(response.content)) == {"detail"}


# -- MSG-7 / MSG-10: listing ahead of time -----------------------------------------------------


def test_MSG7_the_provider_lists_drfs_own_sentences():
    listed = list(declared_templates([SignupSerializer, Order]))
    templates = {item["template"] for item in listed if isinstance(item, dict)}

    assert not [item for item in listed if isinstance(item, (TemplateProblem, LabelAdvice))]
    assert templates >= {
        "This field is required.",
        "Enter a valid email address.",
        "Ensure this field has at least {min_length} characters.",
        "Ensure this field has no more than {max_length} characters.",
        NOT_AN_OBJECT,
    }
    assert "Ensure this value is less than or equal to {max_value}." not in templates


def test_MSG7_a_model_serializers_uniqueness_is_listed_with_drfs_field_names():
    templates = {
        item["template"]
        for item in declared_templates([MembershipSerializer])
        if isinstance(item, dict)
    }

    assert "membership with this handle already exists." in templates, "as DRF fills it"
    assert "The fields team, role must make a unique set." in templates


def test_MSG7_an_undeclared_validator_is_a_problem_and_an_unlabelled_field_advice():
    class Loose(serializers.Serializer):
        code = serializers.CharField()
        name = serializers.CharField(label="name", validators=[no_reserved_names])

        def validate_name(self, value):
            return value

    listed = list(declared_templates([Loose]))
    problems = [item for item in listed if isinstance(item, TemplateProblem)]
    advice = [item for item in listed if isinstance(item, LabelAdvice)]

    assert sorted(problem.field for problem in problems) == ["name", "name"]
    assert [item.field for item in advice] == ["code"]


def test_MSG10_a_model_serializer_declares_with_verbose_name():
    advice = [
        item for item in declared_templates([AccountSerializer]) if isinstance(item, LabelAdvice)
    ]

    assert [(item.field, item.label) for item in advice] == [("cc_number", "Cc number")]


def test_MSG11_a_template_holding_drfs_field_names_placeholder_is_refused(capsys):
    call_command("langsys_messages", "--provider", "tests.test_drf:clashing_templates")
    out = capsys.readouterr().out

    assert "PROBLEM" in out and "{field_names}" in out
    assert not re.search(r"^The fields \{field_names\} clash\.", out, re.M)
