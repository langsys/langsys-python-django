"""Server messages from Django REST framework serializers (spec MSG family).

Entries are built from the rule that failed and the failing field's own bounds, never from DRF's
rendered text (MSG-9); labels are the ones fields declare (MSG-10); a body or nested value that is
not an object, and a body that is not JSON, take the core's MSG-2 wording table; every template a
serializer can emit is listable ahead of time (MSG-7).
"""

from __future__ import annotations

import json
import re

import httpx
import pytest
from django.urls import path
from langsys import LangsysClient
from langsys.cache import MemoryCache
from langsys.messages import WORDINGS, TemplateProblem
from rest_framework import generics, serializers
from rest_framework.renderers import JSONRenderer
from rest_framework.response import Response
from rest_framework.test import APIClient

from langsys_django.client import reset_client, set_client
from langsys_django.drf import declared_templates, entries_from_serializer
from langsys_django.locale import ContextVarLocaleSource
from tests.test_messages import Account

pytestmark = [
    pytest.mark.urls("tests.test_drf"),
    pytest.mark.httpx_mock(assert_all_responses_were_requested=False),
]

TRANS = re.compile(r"https://api\.test/api/translations")


def no_reserved_names(value: str) -> None:
    if "admin" in value:
        raise serializers.ValidationError("That name is reserved.")


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
    return [(e["code"], e["template"], e.get("params"), e.get("field")) for e in entries]


def errors_of(serializer_class, data):
    serializer = serializer_class(data=data)
    assert not serializer.is_valid()
    return entries_from_serializer(serializer)


# -- MSG-9: the rule that failed, never DRF's text ------------------------------------------------


def test_MSG9_two_failed_rules_on_one_field_become_two_entries():
    entries = errors_of(SignupSerializer, {"email": "a@b"})

    assert entry_view(entries) == [
        ("too_short", "The email address must be at least {min} characters.", {"min": 8}, "email"),
        ("invalid_format", "The email address must be a valid email address.", None, "email"),
    ]
    assert entries[0]["message"] == "The email address must be at least 8 characters."


def test_MSG9_a_validators_own_sentence_is_kept_as_written():
    class Named(serializers.Serializer):
        name = serializers.CharField(label="name", validators=[no_reserved_names])

    assert entry_view(errors_of(Named, {"name": "admin"})) == [
        ("invalid", "That name is reserved.", None, "name")
    ]


def test_MSG9_a_declared_code_keeps_its_template():
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

    assert entry_view(errors_of(Order, data)) == [
        ("required", "The city is required.", None, "address.city"),
        (
            "too_long",
            "The item name must not be longer than {max} characters.",
            {"max": 3},
            "items.1.name",
        ),
        ("too_long", "The tags must not be longer than {max} characters.", {"max": 5}, "tags.1"),
    ]


def test_MSG2_a_nested_value_and_a_body_that_are_not_objects_take_the_wording_table():
    nested = errors_of(Order, {"address": "Paris", "items": [], "tags": []})
    body = errors_of(SignupSerializer, ["not", "an", "object"])

    code, template = WORDINGS["object_type"]
    assert entry_view(nested)[0] == (
        code,
        template.replace(":attribute", "address"),
        None,
        "address",
    )
    assert entry_view(body) == [(*WORDINGS["body_not_object"], None, None)]


def test_MSG2_a_size_rule_takes_its_code_from_the_field_type():
    class Sized(serializers.Serializer):
        seats = serializers.IntegerField(label="seats", min_value=10)
        price = serializers.DecimalField(label="price", max_digits=3, decimal_places=1)
        guests = serializers.ListField(label="guests", min_length=2)

    entries = errors_of(Sized, {"seats": 5, "price": "123.4", "guests": ["ada"]})

    assert entry_view(entries) == [
        ("too_small", "The seats must be at least {min}.", {"min": 10}, "seats"),
        ("too_large", "The price must not have more than {max} digits.", {"max": 3}, "price"),
        ("too_few", "The guests must have at least {min} items.", {"min": 2}, "guests"),
    ]


# -- MSG-10: the label the field declares ------------------------------------------------------


def test_MSG10_a_model_serializer_labels_with_verbose_name_and_an_undeclared_field_by_its_key():
    entries = errors_of(AccountSerializer, {})

    assert [e["template"] for e in entries] == [
        "The email address is required.",
        "The cc_number is required.",
    ], "a label guessed from the key reads 'Cc number'"


# -- the exception handler ---------------------------------------------------------------------


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


def test_MSG1_a_failed_request_answers_with_the_default_envelope(api):
    response = api.post("/signup/", {"email": ""}, format="json")
    body = json.loads(response.content)

    assert response.status_code == 400
    assert body["error"]["code"] == "validation_failed"
    assert [(e["code"], e["field"]) for e in body["error"]["errors"]] == [("required", "email")]
    # MSG-8: templates the catalog did not list are registered after the response.
    assert set(api.registered) == {
        "The request failed validation.",
        "The email address is required.",
    }


def test_MSG2_a_body_that_is_not_json_takes_the_wording_table(api):
    response = api.post("/signup/", data="{not json", content_type="application/json")
    body = json.loads(response.content)

    assert response.status_code == 400
    assert [(e["code"], e["template"]) for e in body["error"]["errors"]] == [
        WORDINGS["body_not_json"]
    ]


# -- MSG-7: every template listable ahead of time ----------------------------------------------


def test_MSG7_the_provider_lists_each_template_with_its_label_written_in():
    listed = list(declared_templates([SignupSerializer, Order]))
    templates = {item["template"] for item in listed if not isinstance(item, TemplateProblem)}

    assert not [item for item in listed if isinstance(item, TemplateProblem)]
    assert templates >= {
        "The email address is required.",
        "The email address must be a valid email address.",
        "The email address must be at least {min} characters.",
        "The city is required.",
        "The item name must not be longer than {max} characters.",
        WORDINGS["object_type"][1].replace(":attribute", "address"),
        WORDINGS["body_not_json"][1],
    }


def test_MSG7_an_unlabelled_field_and_an_undeclared_validator_are_problems():
    class Loose(serializers.Serializer):
        code = serializers.CharField()
        name = serializers.CharField(label="name", validators=[no_reserved_names])

        def validate_name(self, value):
            return value

    problems = [item for item in declared_templates([Loose]) if isinstance(item, TemplateProblem)]

    assert sorted(problem.field for problem in problems) == ["code", "name", "name"]
