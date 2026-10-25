"""Backend tests without a database: the routes and the Agent tool with a fake platform and connection.

The SQL itself runs in tests/test_platform.py, on the temporary platform of `nl-plugin verify`.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from natural_land_plugin_sdk import Settings

from nl_plugin_tablet_kiosk.app import plugin

TOKEN = "t" * 32
AUTH = {"authorization": f"Bearer {TOKEN}"}


class FakeConn:
    def __init__(self, rows):
        self.rows, self.sql = rows, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        return self

    def fetchall(self):
        return self.rows

    def fetchone(self):
        return (7,)


class FakePlatform:
    def experiment(self, experiment_id):
        return {"id": experiment_id, "project_id": 1, "name": "e"} if experiment_id == 1 else None


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(plugin, "_settings", Settings(id="tablet_kiosk", token=TOKEN, port=1))
    monkeypatch.setattr(type(plugin), "platform", property(lambda self: FakePlatform()))
    conn = FakeConn([("hello", "alice", datetime(2026, 10, 22, tzinfo=timezone.utc))])
    monkeypatch.setattr(plugin, "connect", lambda: conn)
    yield TestClient(plugin.app), conn


def test_routes_need_the_token_and_check_the_experiment(client):
    http, _ = client
    assert http.get("/experiments/1/notes").status_code == 401
    assert http.get("/experiments/2/notes", headers=AUTH).status_code == 404


def test_adding_a_note_records_the_user(client):
    http, conn = client
    response = http.post("/experiments/1/notes", json={"text": "fed at 9"}, headers={**AUTH, "x-nl-user": "alice"})
    assert response.status_code == 201 and response.json() == {"id": 7}
    assert conn.sql[-1][1] == [1, "fed at 9", "alice"]


def test_the_agent_tool_stays_in_the_project(client):
    http, _ = client
    call = lambda project, args: http.post("/agent/tools/notes_of_experiment", json={"project_id": project, "args": args}, headers=AUTH).json()
    assert call(1, {"experiment_id": 1})["data"]["notes"][0]["text"] == "hello"
    assert call(2, {"experiment_id": 1})["error"]["code"] == "not_found"
    assert call(1, {"experiment_id": 0})["error"]["code"] == "invalid_arguments"
