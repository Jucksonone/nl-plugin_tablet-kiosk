"""Tests on a real database: `nl-plugin verify` runs them (`-m platform`) on its temporary platform.

They use the plugin's own role and the ids verify created (NL_VERIFY_PROJECT_ID, NL_VERIFY_EXPERIMENT_ID,
NL_VERIFY_RUN_ID). The tablet is the real agent (tablet/kiosk) with a fake player and camera, served on
127.0.0.1; the settings pointing at it exist only inside this test process, so the plugin's running
backend never drives it. Each test removes the rows it wrote.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from natural_land_plugin_sdk import Settings

from nl_plugin_tablet_kiosk import app as app_mod
from nl_plugin_tablet_kiosk import controller as controller_mod
from nl_plugin_tablet_kiosk import store
from nl_plugin_tablet_kiosk.app import forget_deleted, plugin
from nl_plugin_tablet_kiosk.controller import Controller, Refused
from nl_plugin_tablet_kiosk.model import ManualCapture, PlanDoc, SettingsDoc
from tablet_fakes import FakeSystem, running_agent

pytestmark = pytest.mark.platform

RUN = "verify-tablet-run"


@pytest.fixture
def env(monkeypatch, tmp_path):
    settings = Settings.from_env()
    monkeypatch.setattr(plugin, "_settings", settings)
    monkeypatch.setattr(app_mod.controller, "_data_dir", tmp_path / "data")
    yield TestClient(plugin.app), {"authorization": f"Bearer {settings.token}", "x-nl-user": "verify"}, tmp_path
    experiment = int(os.environ["NL_VERIFY_EXPERIMENT_ID"])
    with plugin.connect() as conn:
        conn.execute("DELETE FROM captures WHERE run_id IN (%s, 'verify-gone-run') OR requested_by = 'verify' OR session_id LIKE 'verify-%%'", [RUN])
        conn.execute("DELETE FROM runs WHERE run_id IN (%s, 'verify-gone-run')", [RUN])
        conn.execute("DELETE FROM events WHERE run_id = %s OR actor = 'verify' OR session_id LIKE 'verify-%%' OR session_id LIKE 'm-%%'", [RUN])
        conn.execute("DELETE FROM plan_versions WHERE experiment_id IN (%s, 999999)", [experiment])
        conn.execute("DELETE FROM media WHERE uploaded_by = 'verify'")
        conn.execute("DELETE FROM settings_versions WHERE created_by = 'verify'")


def _upload(http, auth, content: bytes, filename="clip.mp4") -> str:
    started = http.post("/media/uploads", json={"filename": filename, "size": len(content)}, headers=auth)
    assert started.status_code == 201, started.text
    up, step = started.json()["upload_id"], 7
    offset = 0
    while offset < len(content):
        r = http.put(f"/media/uploads/{up}", params={"offset": offset}, content=content[offset:offset + step],
                     headers={**auth, "content-type": "application/octet-stream"})
        assert r.status_code == 200, r.text
        offset = r.json()["received"]
    assert http.put(f"/media/uploads/{up}", params={"offset": 0}, content=b"x", headers=auth).json()["error"]["code"] == "wrong_offset"
    done = http.post(f"/media/uploads/{up}/finish", headers=auth).json()
    assert done["name"] == hashlib.sha256(content).hexdigest() + ".mp4"
    return done["name"]


def test_settings_keep_their_token(env):
    http, auth, _ = env
    first = http.get("/settings", headers=auth).json()
    assert http.get("/settings", headers=auth).json()["settings"]["token"] == first["settings"]["token"]
    doc = {**first["settings"], "enabled": False, "host": "192.168.1.9"}
    saved = http.put("/settings", json=doc, headers=auth).json()
    assert saved["version"] > first["version"]
    assert http.put("/settings", json={**doc, "host": "bad host!"}, headers=auth).status_code == 422


def test_media_and_plans(env):
    http, auth, _ = env
    experiment = int(os.environ["NL_VERIFY_EXPERIMENT_ID"])
    name = _upload(http, auth, b"a short stimulus video")
    media = http.get("/media", headers=auth).json()["media"]
    assert any(m["name"] == name and m["original_name"] == "clip.mp4" and m["kind"] == "video" for m in media)
    assert http.get(f"/media/{name}/file", headers=auth).content == b"a short stimulus video"

    plan = {"enabled": True, "screen": {"mode": "content", "items": [name]}, "volume": 0.4}
    assert http.get(f"/experiments/{experiment}/plan", headers=auth).json()["current"] is None
    saved = http.put(f"/experiments/{experiment}/plan", json={"doc": plan, "base_version": 0}, headers=auth).json()
    assert saved["version_no"] == 1
    stale = http.put(f"/experiments/{experiment}/plan", json={"doc": plan, "base_version": 0}, headers=auth).json()
    assert stale["error"]["code"] == "version_conflict"
    unknown = {**plan, "screen": {"mode": "content", "items": ["f" * 64 + ".mp4"]}}
    assert http.put(f"/experiments/{experiment}/plan", json={"doc": unknown}, headers=auth).json()["error"]["code"] == "unknown_media"
    current = http.get(f"/experiments/{experiment}/plan", headers=auth).json()["current"]
    assert current["doc"]["screen"]["items"] == [name] and current["created_by"] == "verify"
    assert http.delete(f"/media/{name}", headers=auth).json()["error"]["code"] == "in_use"
    assert http.get("/experiments/999999/plan", headers=auth).json()["error"]["code"] == "experiment_not_found"

    project = int(os.environ["NL_VERIFY_PROJECT_ID"])
    tool = http.post("/agent/tools/tablet_plan_of_experiment", json={"project_id": project, "args": {"experiment_id": experiment}},
                     headers=auth).json()
    assert tool["ok"] and tool["data"]["plan"]["screen"]["item_names"] == ["clip.mp4"]


class _Platform:
    def __init__(self):
        self.active = None

    def using(self, _conn):
        return self

    def active_run(self):
        return self.active


class _PluginWithRecord:
    """The real plugin (database, settings) with a recording the test decides."""

    def __init__(self, real):
        self._real, self.platform = real, _Platform()

    def __getattr__(self, name):
        return getattr(self._real, name)


def test_the_tablet_follows_a_record_and_files_come_home(env, monkeypatch):
    http, auth, tmp = env
    experiment = int(os.environ["NL_VERIFY_EXPERIMENT_ID"])
    with running_agent(tmp, monkeypatch) as (agent, url, token):
        port = int(url.rsplit(":", 1)[1])
        local = SettingsDoc(enabled=True, host="127.0.0.1", port=port, token=token)
        monkeypatch.setattr(controller_mod.store, "settings", lambda conn: (local, 1))
        fake = _PluginWithRecord(plugin)
        c = Controller(fake, data_dir=tmp / "data")
        c.holding = True
        FakeSystem.volume = 0.5
        with plugin.connect() as conn:
            conn.autocommit = True
            store.save_plan(conn, experiment, PlanDoc(enabled=True, screen={"mode": "white", "items": []}, volume=0.8), "verify", None)

            c.tick(conn)          # no record: nothing to do
            assert c.online and agent.capture is None and c.last_decision == "idle"

            fake.platform.active = {"run_id": RUN, "experiment_id": experiment, "state": "running",
                                    "started_at": datetime.now(timezone.utc)}
            c.tick(conn)
            assert agent.capture["id"] == f"{RUN}.p1" and agent.player.mode == "white" and FakeSystem.volume == 0.8
            row = store.capture(conn, f"{RUN}.p1")
            assert row["kind"] == "run" and row["run_id"] == RUN and row["experiment_id"] == experiment
            assert store.run_binding(conn, RUN)["plan"]["volume"] == 0.8

            c._status_at = float("-inf")
            c.tick(conn)          # same part, renewed
            assert agent.capture["id"] == f"{RUN}.p1" and c.last_decision == "renew_run"
            assert c.fetch_once() == 2    # the finished segment and the touch file, while it records
            assert sorted(store.stored_files(conn, f"{RUN}.p1")) == ["av_00000.mp4", "touch_00001.jsonl"]

            with pytest.raises(Refused) as e:   # the record's capture is not stopped by hand
                c.stop_capture(conn, "verify")
            assert e.value.code == "record_capture"

            fake.platform.active = None   # the record ended
            c._status_at = float("-inf")
            c.tick(conn)
            assert agent.capture is None and FakeSystem.volume == 0.5 and agent.player.mode == "content"
            c.fetch_once()
            done = store.capture(conn, f"{RUN}.p1")
            assert done["complete"] and done["stop_reason"] == "record_ended"
            assert done["summary"]["touch_frames"] == 2 and done["summary"]["segments"] == 2
            assert done["summary"]["touches"] == 1
            t = http.get(f"/captures/{RUN}.p1/touches", headers=auth).json()   # the plugin's data dir is this test's
            assert t["count"] == 1 and t["contacts"][0]["x0"] == 0.5 and t["screen"][0]["mode"] == "white"
            assert http.get(f"/captures/{RUN}.p1/touch.csv", headers=auth).text.startswith("t_unix")
            assert (tmp / "data" / "captures" / f"{RUN}.p1" / "av_00001.mp4").is_file()
            assert agent.spool.sessions() == []
            c._events_at = float("-inf")
            c._status_at = float("-inf")
            c.tick(conn)
            names = {e["name"] for e in store.events(conn, run_id=RUN)}
            assert {"capture_requested", "capture_started", "capture_stop_requested", "capture_finished", "capture_stored"} <= names

            # a manual capture runs to its own duration, renewed, and can be stopped
            started = c.start_manual(conn, ManualCapture(duration_s=60, touch=False), "verify")
            assert agent.capture["id"] == started["session_id"]
            c._status_at = float("-inf")
            c.tick(conn)
            assert agent.capture is not None and c.last_decision == "renew_manual"
            with pytest.raises(Refused):
                c.start_manual(conn, ManualCapture(duration_s=60), "verify")
            assert c.stop_capture(conn, "verify")["stopped"] == started["session_id"]
            assert agent.capture is None
            c.fetch_once()
            assert store.capture(conn, started["session_id"])["complete"]
        c.tablet.close()

    project = int(os.environ["NL_VERIFY_PROJECT_ID"])
    tool = http.post("/agent/tools/tablet_captures_of_run", json={"project_id": project, "args": {"run_id": os.environ["NL_VERIFY_RUN_ID"]}},
                     headers=auth).json()
    assert tool["ok"] or tool["error"]["code"] == "no_data"


def test_cleanup_forgets_deleted_records_and_experiments(env):
    http, auth, tmp = env
    experiment = int(os.environ["NL_VERIFY_EXPERIMENT_ID"])
    run = os.environ["NL_VERIFY_RUN_ID"]
    with plugin.connect() as conn:
        store.add_capture(conn, "verify-gone.p1", "run", run_id="verify-gone-run", by="verify")
        store.add_capture(conn, "verify-kept.p1", "run", run_id=run, by="verify")
        store.save_plan(conn, 999999, PlanDoc(), "verify", None)
        store.save_plan(conn, experiment, PlanDoc(), "verify", None)
    folder = tmp / "data" / "captures" / "verify-gone.p1"
    folder.mkdir(parents=True)
    (folder / "av_00000.mp4").write_bytes(b"x")
    forget_deleted()
    with plugin.connect() as conn:
        assert store.capture(conn, "verify-gone.p1") is None and store.capture(conn, "verify-kept.p1") is not None
        assert store.plan(conn, 999999) is None and store.plan(conn, experiment) is not None
    assert not folder.exists()
