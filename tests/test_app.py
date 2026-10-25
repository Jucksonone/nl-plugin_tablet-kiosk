"""Backend routes without a database: token, validation and refusals that need no SQL.

The SQL runs in tests/test_platform.py, on the temporary platform of `nl-plugin verify`.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from natural_land_plugin_sdk import Settings

from nl_plugin_tablet_kiosk.app import plugin

TOKEN = "t" * 32
AUTH = {"authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def http(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin, "_settings", Settings(id="tablet_kiosk", token=TOKEN, port=1, data_dir=tmp_path))
    return TestClient(plugin.app)   # no `with`: the lifespan (controller threads) does not start


def test_routes_need_the_token(http):
    assert http.get("/status").status_code == 401
    body = http.get("/status", headers=AUTH).json()
    assert body["online"] is False and body["configured"] is False


def test_controls_are_validated_before_the_tablet_is_asked(http):
    r = http.post("/control", json={"cmd": "reboot", "params": {}}, headers=AUTH)
    assert r.status_code == 422
    r = http.post("/capture/start", json={"duration_s": 0}, headers=AUTH)
    assert r.status_code == 422


def test_uploads_only_take_media_types(http):
    r = http.post("/media/uploads", json={"filename": "notes.exe", "size": 10}, headers=AUTH)
    assert r.status_code == 400 and r.json()["error"]["code"] == "unsupported_type"


def test_capture_files_reject_odd_names(http):
    assert http.get("/captures/a/files/..%2Fsecret", headers=AUTH).status_code == 404
    assert http.get("/captures/..%2Fx/files/a.mp4", headers=AUTH).status_code == 404
