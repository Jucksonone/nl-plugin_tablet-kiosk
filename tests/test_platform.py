"""Tests on a real database: `nl-plugin verify` runs them (`-m platform`) on its temporary platform.

They use the plugin's own role (NL_PLUGIN_DSN, …) and the ids of the test data verify created:
NL_VERIFY_PROJECT_ID, NL_VERIFY_EXPERIMENT_ID and NL_VERIFY_RUN_ID (a stopped record of that
experiment). Each test cleans up the rows it wrote.
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from natural_land_plugin_sdk import Settings

from nl_plugin_tablet_kiosk.app import plugin, remove_notes_of_deleted_experiments

pytestmark = pytest.mark.platform


@pytest.fixture
def client(monkeypatch):
    settings = Settings.from_env()
    monkeypatch.setattr(plugin, "_settings", settings)
    yield TestClient(plugin.app), {"authorization": f"Bearer {settings.token}"}
    with plugin.connect() as conn:
        conn.execute("DELETE FROM notes WHERE text LIKE 'verify-test %'")


def test_notes_are_written_read_and_seen_by_the_agent(client):
    http, auth = client
    experiment = int(os.environ["NL_VERIFY_EXPERIMENT_ID"])
    project = int(os.environ["NL_VERIFY_PROJECT_ID"])
    created = http.post(f"/experiments/{experiment}/notes", json={"text": "verify-test water"}, headers={**auth, "x-nl-user": "verify"})
    assert created.status_code == 201
    notes = http.get(f"/experiments/{experiment}/notes", headers=auth).json()["notes"]
    assert notes[0]["text"] == "verify-test water" and notes[0]["author"] == "verify"
    tool = http.post("/agent/tools/notes_of_experiment", json={"project_id": project, "args": {"experiment_id": experiment}}, headers=auth).json()
    assert tool["ok"] and tool["data"]["notes"][0]["text"] == "verify-test water"
    assert http.get("/experiments/999999/notes", headers=auth).json()["error"]["code"] == "experiment_not_found"


def test_cleanup_keeps_notes_of_existing_experiments(client):
    http, auth = client
    experiment = int(os.environ["NL_VERIFY_EXPERIMENT_ID"])
    http.post(f"/experiments/{experiment}/notes", json={"text": "verify-test kept"}, headers=auth)
    with plugin.connect() as conn:  # a note of an experiment the platform does not have
        conn.execute("INSERT INTO notes (experiment_id, text) VALUES (999999, 'verify-test orphan')")
    remove_notes_of_deleted_experiments()
    with plugin.connect() as conn:
        texts = {row[0] for row in conn.execute("SELECT text FROM notes WHERE text LIKE 'verify-test %'").fetchall()}
    assert texts == {"verify-test kept"}
