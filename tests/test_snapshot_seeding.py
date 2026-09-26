"""SNAP-2: the ``SNAPSHOT`` setting seeds the shared client when Django starts.

The seam is the core's ``load_snapshot``; the decision to seed at boot is this binding's. A seeded
first render needs no network call, and a snapshot the core refuses stops startup.
"""

from __future__ import annotations

import json

import pytest
from django.apps import apps
from langsys.snapshot import Snapshot, SnapshotError

from langsys_django.client import get_client, reset_client, t
from langsys_django.locale import reset_current_locale, set_current_locale

pytestmark = pytest.mark.httpx_mock(assert_all_responses_were_requested=False)


@pytest.fixture()
def snapshot(tmp_path):
    return Snapshot(
        {
            "project_id": "proj-1",
            "generated_at": "2026-09-26T00:00:00Z",
            "base_locale": "en-us",
            "locales": ["es-es"],
            "categories": ["UI"],
            "catalog": {"es-es": {"UI": {"Save": "Guardar"}}},
        }
    ).write_to(tmp_path / "catalog.snapshot.json")


@pytest.fixture()
def boot(settings):
    def start(path) -> None:
        settings.LANGSYS = {
            "API_KEY": "k",
            "PROJECT_ID": "proj-1",
            "API_URL": "https://api.test/api",
            "SNAPSHOT": str(path),
        }
        reset_client()
        apps.get_app_config("langsys_django").ready()

    yield start
    reset_client()


def test_SNAP2_a_seeded_first_render_needs_no_network(boot, snapshot, httpx_mock):
    boot(snapshot)
    token = set_current_locale("es-ES")
    try:
        assert t("Save", "UI") == "Guardar"
    finally:
        reset_current_locale(token)

    assert httpx_mock.get_requests() == []


def test_SNAP2_a_snapshot_edited_after_export_stops_startup(boot, snapshot):
    document = json.loads(snapshot.read_text(encoding="utf-8"))
    document["catalog"]["es-es"]["UI"]["Save"] = "Salvar"
    snapshot.write_text(json.dumps(document), encoding="utf-8")

    with pytest.raises(SnapshotError, match="checksum"):
        boot(snapshot)


def test_SNAP2_without_the_setting_nothing_is_seeded_or_built(settings):
    settings.LANGSYS = {"API_KEY": "k", "PROJECT_ID": "proj-1"}
    reset_client()
    apps.get_app_config("langsys_django").ready()

    from langsys_django import client

    assert client._client is None, "startup builds no client unless there is a snapshot to seed"
    assert get_client() is not None
    reset_client()
