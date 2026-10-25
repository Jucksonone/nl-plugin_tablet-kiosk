"""Decisions and helpers of the controller that need no database."""

from __future__ import annotations

import io
import json
import zipfile

from nl_plugin_tablet_kiosk.app import stream_zip, touch_csv
from nl_plugin_tablet_kiosk.controller import (NEXT_PART_AFTER_S, desired_capture, file_info, run_session_id,
                                               sample_of, summarise)
from nl_plugin_tablet_kiosk.model import PlanDoc

RUN = {"run_id": "r-20261025", "experiment_id": 3}


def binding(**plan):
    return {"plan": {**PlanDoc(enabled=True).model_dump(), **plan, "version_no": 2}, "plan_version_id": 9}


def test_no_record_or_no_enabled_plan_means_no_record_capture():
    assert desired_capture(None, None, 0, False, 1e9) is None
    assert desired_capture(RUN, {"plan": None}, 0, False, 1e9) is None
    assert desired_capture(RUN, binding(enabled=False), 0, False, 1e9) is None


def test_parts_follow_what_the_tablet_runs():
    first = desired_capture(RUN, binding(), 0, False, 1e9)
    assert first["session_id"] == "r-20261025.p1" and first["part"] == 1 and first["plan_version_id"] == 9
    # part 1 running: keep it
    assert desired_capture(RUN, binding(), 1, True, 100)["part"] == 1
    # just started, the tablet's report is not in yet: do not open part 2
    assert desired_capture(RUN, binding(), 1, False, NEXT_PART_AFTER_S - 1)["part"] == 1
    # the tablet ended part 1 on its own while the record runs: part 2
    assert desired_capture(RUN, binding(), 1, False, NEXT_PART_AFTER_S + 1)["part"] == 2


def test_long_or_odd_run_ids_still_make_safe_session_ids():
    sid = run_session_id("x" * 200, 3)
    assert len(sid) <= 128 and sid.endswith(".p3") and sid.startswith("run-")
    assert run_session_id("abc", 1) == "abc.p1"


def test_plan_command_carries_the_lease_and_screen():
    doc = PlanDoc(enabled=True, video=False, screen={"mode": "white", "items": []}, volume=0.3)
    cmd = doc.command(session_id="s", duration_s=10, lease_s=30, meta={"run_id": "r"})
    assert cmd["lease_s"] == 30 and cmd["screen"]["mode"] == "white" and cmd["video"] is False and cmd["volume"] == 0.3
    assert doc.media() == []


def test_touch_info_and_summary(tmp_path):
    f = tmp_path / "touch_00001.jsonl"
    f.write_text("\n".join(json.dumps(x) for x in [
        {"t": 1.0, "touches": [{"id": 1, "x": 0.1, "y": 0.2}, {"id": 2, "x": 0.3, "y": 0.4}]},
        {"t": 1.1, "touches": []}]) + "\n")
    info = file_info(f)
    assert info == {"frames": 2, "touching": 1, "max_fingers": 2}
    s = summarise({"started_utc": 10, "ended_utc": 70, "segments": [{}, {}], "screen": {"log": [{}]}},
                  [{"name": "touch_00001.jsonl", "bytes": 5, "info": info}, {"name": "av_00000.mp4", "bytes": 100, "info": {}}])
    assert s["duration_s"] == 60 and s["segments"] == 2 and s["bytes"] == 105 and s["touch_frames_down"] == 1
    rows = touch_csv(tmp_path, 1.0).splitlines()
    assert rows[0].startswith("t_unix") and len(rows) == 4 and rows[-1].endswith(",,,")


def test_sample_keeps_only_numbers_for_charts():
    s = sample_of({"battery": {"capacity": "64", "status": "Charging"}, "temperature": 43.0, "load": [1.5, 1, 1],
                   "memory": {"total_mb": 3800, "available_mb": 2800, "agent_rss_mb": 80}, "capture": None})
    assert s["battery"] == 64.0 and s["mem_used_mb"] == 1000 and s["load1"] == 1.5 and s["capturing"] is False


def test_export_zip_streams_files(tmp_path):
    big = tmp_path / "av_00000.mp4"
    big.write_bytes(b"v" * (3 << 20))
    data = b"".join(stream_zip([("s/av_00000.mp4", big), ("s/README.txt", "说明".encode())]))
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        assert z.read("s/av_00000.mp4") == big.read_bytes()
        assert z.read("s/README.txt").decode() == "说明"
