"""The tablet agent's HTTP interface, driven by the plugin's client (no database)."""

from __future__ import annotations

import hashlib
import json
import time

import pytest

from nl_plugin_tablet_kiosk.tablet import Tablet, TabletError
from tablet_fakes import FakeSystem, running_agent


@pytest.fixture
def tablet(tmp_path, monkeypatch):
    with running_agent(tmp_path, monkeypatch) as (agent, url, token):
        client = Tablet(url, token)
        yield agent, client, tmp_path
        client.close()


def _media(tmp_path, content=b"stimulus-video", ext="mp4"):
    name = f"{hashlib.sha256(content).hexdigest()}.{ext}"
    path = tmp_path / name
    path.write_bytes(content)
    return name, path


def test_every_request_needs_the_token(tablet):
    agent, client, _ = tablet
    wrong = Tablet(client.base_url, "x" * 20)
    with pytest.raises(TabletError) as e:
        wrong.status()
    assert e.value.code == "bad_token"
    status = client.status()
    assert status["device_id"] == "tablet-test" and status["capture"] is None and status["instance"]


def test_media_must_match_its_name(tablet):
    agent, client, tmp = tablet
    name, path = _media(tmp)
    bad = tmp / ("0" * 64 + ".mp4")
    bad.write_bytes(b"other")
    with pytest.raises(TabletError):
        client.put_media(bad.name, bad)
    client.put_media(name, path)
    assert [f["name"] for f in client.media()["files"]] == [name]
    with pytest.raises(TabletError) as e:
        client.command("show", {"mode": "content", "items": ["1" * 64 + ".mp4"]})
    assert e.value.code == "missing_media"
    assert client.command("show", {"mode": "content", "items": [name]}) == {"mode": "content", "items": [name]}
    with pytest.raises(TabletError) as e:   # on screen: kept
        client.delete_media(name)
    assert e.value.code == "in_use"


def test_capture_with_lease_files_and_restore(tablet):
    agent, client, tmp = tablet
    name, path = _media(tmp)
    client.put_media(name, path)
    FakeSystem.volume = 0.5
    r = client.command("capture_start", {"session_id": "run-1.p1", "duration_s": 3600, "lease_s": 30,
                                         "screen": {"mode": "content", "items": [name]}, "volume": 0.9,
                                         "meta": {"run_id": "run-1"}})
    assert r["session"] == "run-1.p1" and r["lease_until"] > time.time()
    assert FakeSystem.volume == 0.9
    assert client.status()["capture"]["session"] == "run-1.p1"
    with pytest.raises(TabletError):     # one capture at a time
        client.command("capture_start", {"session_id": "other", "duration_s": 60})
    client.command("capture_renew", {"session": "run-1.p1", "lease_s": 30})

    spool = client.spool()   # the finished segment is there, the one being written is not
    assert [f["name"] for f in spool[0]["files"]] == ["av_00000.mp4", "touch_00001.jsonl"]
    assert spool[0]["active"] and not spool[0]["complete"]

    # the plugin stops renewing: the tablet ends the capture and puts things back
    agent.capture["lease_until"] = time.time() - 1
    agent._watchdog()
    assert client.status()["capture"] is None and FakeSystem.volume == 0.5
    with pytest.raises(TabletError):
        client.command("capture_renew", {"session": "run-1.p1"})

    s = client.spool()[0]
    assert s["complete"] and {f["name"] for f in s["files"]} == {"av_00000.mp4", "av_00001.mp4", "touch_00001.jsonl", "session.json"}
    dest = tmp / "fetched"
    dest.mkdir()
    for f in s["files"]:
        assert client.fetch("run-1.p1", f["name"], dest / f["name"], f["size"]) == f["size"]
    meta = json.loads((dest / "session.json").read_text())
    assert meta["stop_reason"] == "lease_expired" and meta["request"] == {"run_id": "run-1"}
    assert meta["screen"]["requested"]["items"] == [name] and meta["screen"]["log"][0]["file"] == name
    for f in s["files"]:
        client.delete_spool("run-1.p1", f["name"])
    assert client.spool() == []

    names = [e["name"] for e in client.events(0)["events"]]
    assert names[-2:] == ["capture_started", "capture_finished"] or "capture_finished" in names
    last = client.events(0)["last_seq"]
    assert client.events(last)["events"] == []


def test_capture_stops_at_its_duration_and_on_command(tablet):
    agent, client, _ = tablet
    client.command("capture_start", {"session_id": "m-1", "duration_s": 60, "touch": False})
    agent.capture["until"] = time.time() - 1
    agent._watchdog()
    assert json.loads((agent.cfg.spool_dir / "m-1" / "session.json").read_text())["stop_reason"] == "duration"
    client.command("capture_start", {"session_id": "m-2", "duration_s": 60})
    assert client.command("capture_stop", {"session": "other"}) == {"stopped": None}
    assert client.command("capture_stop", {"session": "m-2", "reason": "user"})["stopped"] == "m-2"
    with pytest.raises(TabletError):    # session ids are never reused
        client.command("capture_start", {"session_id": "m-2", "duration_s": 60})


def test_recover_closes_a_capture_cut_by_a_crash(tmp_path, monkeypatch):
    with running_agent(tmp_path, monkeypatch) as (agent, _url, _token):
        d = agent.cfg.spool_dir / "cut"
        d.mkdir()
        (d / "av_00000.mp4").write_bytes(b"x")
        (d / ".av_00001.mp4").write_bytes(b"half")
        assert agent.spool.recover() == ["cut"]
        meta = json.loads((d / "session.json").read_text())
        assert meta["stop_reason"] == "interrupted" and [s["file"] for s in meta["segments"]] == ["av_00000.mp4"]
        assert not (d / ".av_00001.mp4").exists()


def test_event_log_survives_a_restart(tmp_path):
    from kiosk.events import EventLog

    log = EventLog(tmp_path, keep=3)
    for i in range(5):
        log.append("e", i=i)
    again = EventLog(tmp_path, keep=3)
    assert again.instance == log.instance and again.seq == 5
    assert [e["i"] for e in again.after(0)] == [2, 3, 4]
