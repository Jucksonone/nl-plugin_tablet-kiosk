"""Drives the tablet "by state" (AGENTS.md §4.6) and moves files to and from it.

Every second the controller decides which capture the tablet should be running:

* a recording whose experiment plan is enabled: the record's capture
  (session ``<run_id>.p<n>``; a new part when the tablet ended the previous one
  while the record still runs);
* otherwise a manual capture someone started (session ``m-…``), until its duration;
* otherwise none.

and compares it with what the tablet reports. A capture started by the plugin
has a lease (renewed every 5 s, valid 30 s): if the plugin dies, the tablet
stops the capture by itself and restores the screen, volume and brightness.

Two more threads, both only while this process holds the single-instance lock:
the pusher copies stimulus media to the tablet before it is needed, the fetcher
moves finished capture files from the tablet into ``$NL_PLUGIN_DATA_DIR/captures``.
"""

from __future__ import annotations

import json
import logging
import queue
import re
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import store
from .model import CaptureDoc, ManualCapture, PlanDoc, SettingsDoc
from .tablet import Tablet, TabletError

logger = logging.getLogger(__name__)

STATUS_EVERY_S = 2.0
EVENTS_EVERY_S = 3.0
SAMPLE_EVERY_S = 15.0
MEDIA_LIST_EVERY_S = 30.0
PRELOAD_EVERY_S = 60.0
SETTINGS_EVERY_S = 5.0
OFFLINE_AFTER = 3             # failed status polls in a row
LEASE_S = 30.0
RENEW_EVERY_S = 5.0
RUN_CAPTURE_S = 24 * 3600     # a record's capture part; the next part follows if the record runs longer
START_RETRY_S = 30.0
NEXT_PART_AFTER_S = 10.0      # after the tablet ended a part on its own
STOP_RETRY_S = 5.0
FETCH_EVERY_S = 5.0
SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


class Refused(Exception):
    """A request the controller cannot do now (route → 409 with this code)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def run_session_id(run_id: str, part: int) -> str:
    sid = f"{run_id}.p{part}"
    if len(sid) > 128 or not SESSION_ID.match(sid):
        import hashlib

        sid = f"run-{hashlib.sha1(run_id.encode()).hexdigest()[:16]}.p{part}"
    return sid


def desired_capture(active: dict | None, binding: dict | None, last_part: int, last_session_running: bool,
                    since_last_start_s: float) -> dict | None:
    """Which record capture the tablet should run (pure; manual captures are handled separately).

    ``last_part``: the highest part already started for this record (0: none);
    ``last_session_running``: the tablet is running that part now.
    """
    if not active or not binding or not binding.get("plan"):
        return None
    plan = PlanDoc.model_validate(binding["plan"])
    if not plan.enabled:
        return None
    if last_part == 0:
        part = 1
    elif last_session_running or since_last_start_s < NEXT_PART_AFTER_S:
        part = last_part
    else:
        part = last_part + 1
    return {"session_id": run_session_id(active["run_id"], part), "kind": "run", "run_id": active["run_id"],
            "experiment_id": active.get("experiment_id"), "part": part, "plan_version_id": binding.get("plan_version_id"),
            "doc": plan}


class Controller:
    def __init__(self, plugin, *, tablet_factory: Callable[[str, str], Any] = Tablet,
                 clock: Callable[[], float] = time.time, mono: Callable[[], float] = time.monotonic,
                 data_dir: Path | None = None) -> None:
        self.plugin = plugin
        self.tablet_factory = tablet_factory
        self.clock, self.mono = clock, mono
        self._data_dir = data_dir
        self._lock = threading.RLock()
        self._wake = threading.Event()
        self._stopping = threading.Event()
        self._threads: list[threading.Thread] = []
        self._db_lock = None
        self.holding = False
        self.db_error: str | None = None
        # inputs
        self.settings = SettingsDoc()
        self.settings_version: int | None = None
        self._settings_at = float("-inf")
        self.active: dict | None = None
        self.binding: dict | None = None
        # tablet
        self.tablet = None
        self._tablet_key: tuple | None = None
        self.online = False
        self.fails = 0
        self.error: str | None = None
        self.report: dict | None = None
        self.report_at: float | None = None       # wall clock of the last good status
        self._status_at = float("-inf")
        self._events_at = float("-inf")
        self._sample_at = float("-inf")
        self._media_at = float("-inf")
        self._preload_at = float("-inf")
        self.tablet_media: set[str] | None = None
        self._cursor: tuple[str, int] | None = None   # (instance, last seq stored)
        # decisions
        self.want: dict | None = None
        self.waiting_media: list[str] = []
        self._renewed: dict[str, float] = {}
        self._retry_at = float("-inf")
        self._stop_at = float("-inf")
        self._last_start: dict[str, float] = {}      # run_id -> mono of the last part started
        self._parts: dict[str, int] = {}             # run_id -> highest part started
        self._session_runs: dict[str, str | None] = {}
        self.last_decision: str | None = None
        # transfers
        self._push_q: queue.Queue[str] = queue.Queue()
        self._push_queued: set[str] = set()
        self.pushing: dict | None = None
        self.push_error: str | None = None
        self.fetching: dict | None = None
        self.fetch_error: str | None = None
        self.low_disk = False

    # ------------------------------------------------------------ paths

    @property
    def data_dir(self) -> Path:
        d = self._data_dir or self.plugin.settings.data_dir or Path("data")
        return Path(d)

    @property
    def media_dir(self) -> Path:
        p = self.data_dir / "media"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def captures_dir(self) -> Path:
        p = self.data_dir / "captures"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def capture_dir(self, session_id: str) -> Path:
        if not SESSION_ID.match(session_id):
            raise ValueError(f"bad session id {session_id!r}")
        return self.captures_dir / session_id

    def disk(self) -> dict:
        u = shutil.disk_usage(self.data_dir if self.data_dir.exists() else Path("."))
        return {"free_bytes": u.free, "total_bytes": u.total, "min_free_bytes": int(self.settings.min_free_gb * 1e9)}

    # ------------------------------------------------------------ lifecycle

    def start(self) -> None:
        for target, name in ((self._run, "tablet-controller"), (self._pusher, "tablet-pusher"), (self._fetcher, "tablet-fetcher")):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)

    def stop(self) -> None:
        self._stopping.set()
        self._wake.set()
        self._push_q.put("")
        for t in self._threads:
            t.join(timeout=20)
        if self.tablet is not None:
            self.tablet.close()
        if self._db_lock is not None:
            try:
                self._db_lock.release()
            except Exception:
                pass

    def wake(self) -> None:
        self._settings_at = float("-inf")
        self._wake.set()

    @property
    def stopping(self) -> bool:
        return self._stopping.is_set() or self.plugin.stopping

    def _sleep(self, seconds: float) -> None:
        if not self._stopping.is_set():
            self._wake.wait(seconds)
        self._wake.clear()

    def _run(self) -> None:
        last_lock_check = float("-inf")
        while not self.stopping:
            if self.mono() - last_lock_check >= 10 or not self.holding:
                last_lock_check = self.mono()
                self._hold_lock()
            if not self.holding:
                self._sleep(10.0)
                continue
            try:
                with self.plugin.connect() as conn:
                    conn.autocommit = True
                    while not self.stopping and self.holding:
                        self.tick(conn)
                        self.db_error = None
                        self._sleep(1.0)
                        if self.mono() - last_lock_check >= 10:
                            last_lock_check = self.mono()
                            self._hold_lock()
            except Exception as exc:
                logger.warning("controller tick failed: %s", exc, exc_info=True)
                self.db_error = str(exc)[:300]
                # Without the database the plugin does not renew: the tablet stops the capture when the lease ends.
            self._sleep(2.0)

    def _hold_lock(self) -> None:
        try:
            if self._db_lock is not None and self._db_lock.alive():
                self.holding = True
                return
            if self._db_lock is not None:
                try:
                    self._db_lock.release()
                except Exception:
                    pass
            self._db_lock = self.plugin.try_lock("tablet")
        except Exception as exc:
            logger.warning("tablet lock: %s", exc)
            self._db_lock = None
        self.holding = self._db_lock is not None

    # ------------------------------------------------------------ tick

    def tick(self, conn) -> None:
        mono = self.mono()
        if mono - self._settings_at >= SETTINGS_EVERY_S:
            self._settings_at = mono
            self.settings, self.settings_version = store.settings(conn)
        self._ensure_tablet()
        self.active = self.plugin.platform.using(conn).active_run()
        self.binding = store.bind_run(conn, self.active, now_utc()) if self.active else None
        if self.tablet is None:
            self.online, self.report, self.want = False, None, None
            return
        if mono - self._status_at >= STATUS_EVERY_S:
            self._status_at = mono
            self._poll_status(conn)
        if not self.online:
            return
        if mono - self._events_at >= EVENTS_EVERY_S:
            self._events_at = mono
            self._pull_events(conn)
        if mono - self._media_at >= MEDIA_LIST_EVERY_S:
            self._media_at = mono
            self._refresh_media()
        if mono - self._preload_at >= PRELOAD_EVERY_S:
            self._preload_at = mono
            self.request_push(store.media_of_enabled_plans(conn))
        if mono - self._sample_at >= SAMPLE_EVERY_S and self.report:
            self._sample_at = mono
            store.add_sample(conn, now_utc(), sample_of(self.report))
        self._drive(conn)

    def _ensure_tablet(self) -> None:
        key = (self.settings.base_url, self.settings.token)
        if key == self._tablet_key:
            return
        if self.tablet is not None:
            self.tablet.close()
        self._tablet_key = key
        self.tablet = self.tablet_factory(key[0], key[1]) if key[0] else None
        self.online, self.fails, self.report, self.tablet_media, self._cursor = False, 0, None, None, None
        self._status_at = self._media_at = float("-inf")

    def _poll_status(self, conn) -> None:
        try:
            report = self.tablet.status()
        except TabletError as e:
            self.fails += 1
            self.error = e.message[:300]
            if self.online and self.fails >= OFFLINE_AFTER:
                self.online = False
                store.add_event(conn, "tablet_offline", data={"error": self.error})
            return
        self.fails, self.error = 0, None
        self.report, self.report_at = report, self.clock()
        if not self.online:
            self.online = True
            self._media_at = float("-inf")
            store.add_event(conn, "tablet_online", data={"version": report.get("version"), "device_id": report.get("device_id")})

    def _pull_events(self, conn) -> None:
        instance = (self.report or {}).get("instance")
        if not instance:
            return
        if self._cursor is None or self._cursor[0] != instance:
            self._cursor = (instance, store.tablet_cursor(conn, instance))
        try:
            got = self.tablet.events(self._cursor[1])
        except TabletError as e:
            self.error = e.message[:300]
            return
        for ev in got["events"]:
            sid = ev.get("session")
            store.add_event(conn, ev["name"], source="tablet", at=store.utc(ev["t"]), session_id=sid,
                            run_id=self._run_of(conn, sid), data={k: v for k, v in ev.items() if k not in ("seq", "t", "name")},
                            tablet_instance=instance, tablet_seq=ev["seq"])
            self._cursor = (instance, ev["seq"])
            if ev["name"] == "capture_started" and sid:
                store.mark_started(conn, sid, ev["t"])
            if ev["name"] == "media_stored":
                self._media_at = float("-inf")

    def _run_of(self, conn, session_id: str | None) -> str | None:
        if not session_id:
            return None
        if session_id not in self._session_runs:
            c = conn.execute("SELECT run_id FROM captures WHERE session_id = %s", [session_id]).fetchone()
            if c is None:
                return None
            self._session_runs[session_id] = c[0]
        return self._session_runs[session_id]

    def _refresh_media(self) -> None:
        try:
            self.tablet_media = {f["name"] for f in self.tablet.media()["files"]}
        except TabletError as e:
            self.error = e.message[:300]

    # ------------------------------------------------------------ driving the capture

    def _tablet_capture(self) -> dict | None:
        return (self.report or {}).get("capture")

    def _drive(self, conn) -> None:
        mono = self.mono()
        cap = self._tablet_capture()
        running = cap["session"] if cap else None
        active = self.active
        want = None
        if active:
            rid = active["run_id"]
            if rid not in self._parts:
                self._parts[rid] = store.run_parts(conn, rid)
            last = self._parts[rid]
            want = desired_capture(active, self.binding, last, running == run_session_id(rid, last) if last else False,
                                   mono - self._last_start.get(rid, float("-inf")))
        self.want = want
        with self._lock:
            if running:
                if want and running == want["session_id"]:
                    self._renew(conn, running)
                    self.last_decision = "renew_run"
                elif want is None and running.startswith("m-") and not cap.get("stopping"):
                    self._renew(conn, running)          # a manual capture runs to its own duration
                    self.last_decision = "renew_manual"
                else:
                    self.last_decision = "stop"
                    if mono >= self._stop_at:
                        self._stop_at = mono + STOP_RETRY_S
                        reason = "superseded" if want else ("record_ended" if not running.startswith("m-") else "stopped")
                        self._send_stop(conn, running, reason, actor=None)
                return
            if want is None:
                self.last_decision = "idle"
                self.waiting_media = []
                return
            if mono < self._retry_at:
                self.last_decision = "retry_later"
                return
            missing = self.missing_media(want["doc"].media())
            if missing:
                self.waiting_media = missing
                self.request_push(missing)
                self.last_decision = "waiting_media"
                return
            self.waiting_media = []
            self._start(conn, want, actor=None)

    def _start(self, conn, want: dict, actor: str | None) -> None:
        doc: CaptureDoc = want["doc"]
        sid = want["session_id"]
        meta = {"kind": want["kind"], "run_id": want.get("run_id"), "experiment_id": want.get("experiment_id"),
                "part": want.get("part"), "plan_version_id": want.get("plan_version_id")}
        params = doc.command(session_id=sid, duration_s=want.get("duration_s") or RUN_CAPTURE_S, lease_s=LEASE_S, meta=meta)
        try:
            result = self.tablet.command("capture_start", params)
        except TabletError as e:
            self._retry_at = self.mono() + START_RETRY_S
            self.last_decision = "start_failed"
            store.add_event(conn, "capture_start_failed", session_id=sid, run_id=want.get("run_id"), actor=actor,
                            data={"error": e.message, "code": e.code})
            if want["kind"] == "manual":
                raise Refused(e.code, e.message) from e
            return
        store.add_capture(conn, sid, want["kind"], run_id=want.get("run_id"), experiment_id=want.get("experiment_id"),
                          part=want.get("part") or 1, plan_version_id=want.get("plan_version_id"),
                          request=params, by=actor)
        self._session_runs[sid] = want.get("run_id")
        store.add_event(conn, "capture_requested", session_id=sid, run_id=want.get("run_id"), actor=actor,
                        data={"kind": want["kind"], "part": want.get("part"), "duration_s": params["duration_s"]})
        now = self.clock()
        self._renewed[sid] = self.mono()
        if want.get("run_id"):
            self._parts[want["run_id"]] = max(self._parts.get(want["run_id"], 0), want["part"])
            self._last_start[want["run_id"]] = self.mono()
        # The next status poll is up to 2 s away: until then, take the tablet's answer as its state.
        if self.report is not None:
            self.report = {**self.report, "capture": {"session": sid, "started": now, "until": result.get("until"),
                                                      "lease_until": result.get("lease_until"), "video": doc.video,
                                                      "audio": doc.audio, "touch": doc.touch}}
        self.last_decision = "started"

    def _renew(self, conn, sid: str) -> None:
        if self.mono() - self._renewed.get(sid, float("-inf")) < RENEW_EVERY_S:
            return
        try:
            self.tablet.command("capture_renew", {"session": sid, "lease_s": LEASE_S}, timeout=10)
            self._renewed[sid] = self.mono()
        except TabletError as e:
            self.error = f"renew {sid}: {e.message}"[:300]
            self._status_at = float("-inf")   # see right away whether it ended

    def _send_stop(self, conn, sid: str, reason: str, actor: str | None) -> None:
        try:
            self.tablet.command("capture_stop", {"session": sid, "reason": reason}, timeout=40)
        except TabletError as e:
            store.add_event(conn, "capture_stop_failed", session_id=sid, actor=actor, data={"error": e.message})
            return
        store.add_event(conn, "capture_stop_requested", session_id=sid, run_id=self._run_of(conn, sid), actor=actor,
                        data={"reason": reason})
        if self.report is not None and self._tablet_capture() and self._tablet_capture()["session"] == sid:
            self.report = {**self.report, "capture": None}
        self._renewed.pop(sid, None)
        self._status_at = float("-inf")

    # ------------------------------------------------------------ requests from routes

    def _require_tablet(self) -> None:
        if not self.holding:
            raise Refused("not_controlling", "another plugin process controls the tablet")
        if self.tablet is None:
            raise Refused("not_configured", "the tablet's address is not set or the tablet is switched off in the settings")
        if not self.online:
            raise Refused("tablet_offline", f"the tablet does not answer ({self.error or 'no reply yet'})")

    def missing_media(self, names: list[str]) -> list[str]:
        if self.tablet_media is None:
            self._refresh_media()
        have = self.tablet_media or set()
        return [n for n in dict.fromkeys(names) if n not in have]

    def start_manual(self, conn, doc: ManualCapture, by: str | None) -> dict:
        self._require_tablet()
        with self._lock:
            if self.want is not None:
                raise Refused("record_capture", "the tablet is capturing for the running record")
            if self._tablet_capture():
                raise Refused("capture_running", f"the tablet is already capturing ({self._tablet_capture()['session']})")
            missing = self.missing_media(doc.media())
            if missing:
                self.request_push(missing)
                raise Refused("media_pending", "the media are being copied to the tablet; start again when they are there")
            sid = "m-" + datetime.now().strftime("%Y%m%d-%H%M%S")
            active = self.active
            want = {"session_id": sid, "kind": "manual", "run_id": active["run_id"] if active else None,
                    "experiment_id": active.get("experiment_id") if active else None, "part": 1, "doc": doc,
                    "duration_s": doc.duration_s}
            self._start(conn, want, actor=by)
            return {"session_id": sid}

    def stop_capture(self, conn, by: str | None) -> dict:
        self._require_tablet()
        with self._lock:
            cap = self._tablet_capture()
            if not cap:
                return {"stopped": None}
            if self.want and cap["session"] == self.want["session_id"]:
                raise Refused("record_capture", "this capture follows the running record; it stops when the record stops "
                                                "(or switch the experiment's plan off)")
            self._send_stop(conn, cap["session"], "user", actor=by)
            return {"stopped": cap["session"]}

    def control(self, conn, cmd: str, params: dict, by: str | None) -> Any:
        self._require_tablet()
        if cmd == "show":
            missing = self.missing_media(params.get("items") or [])
            if missing:
                self.request_push(missing)
                raise Refused("media_pending", "the media are being copied to the tablet; try again when they are there")
        try:
            result = self.tablet.command(cmd, params)
        except TabletError as e:
            raise Refused(e.code, e.message) from e
        store.add_event(conn, "control", actor=by, run_id=(self.active or {}).get("run_id"), data={"cmd": cmd, "params": params})
        self._status_at = float("-inf")
        self._wake.set()
        return result

    def request_push(self, names) -> None:
        for n in names:
            if n and n not in self._push_queued and (self.tablet_media is None or n not in self.tablet_media):
                self._push_queued.add(n)
                self._push_q.put(n)

    # ------------------------------------------------------------ pusher

    def _pusher(self) -> None:
        while not self.stopping:
            try:
                name = self._push_q.get(timeout=1.0)
            except queue.Empty:
                continue
            if not name:
                continue
            try:
                self._push(name)
            except Exception as exc:          # noqa: BLE001 - next file
                logger.exception("pushing %s failed", name)
                self.push_error = f"{name}: {exc}"[:300]
            finally:
                self._push_queued.discard(name)
                self.pushing = None

    def _push(self, name: str) -> None:
        tablet = self.tablet
        if tablet is None or not self.online or not self.holding:
            return
        if self.tablet_media is not None and name in self.tablet_media:
            return
        path = self.media_dir / name
        if not path.is_file():
            self.push_error = f"{name}: not in the media library"
            return
        self.pushing = {"name": name, "bytes": path.stat().st_size, "started": self.clock()}
        try:
            tablet.put_media(name, path)
        except TabletError as e:
            self.push_error = f"{name}: {e.message}"[:300]
            return
        self.push_error = None
        if self.tablet_media is not None:
            self.tablet_media.add(name)
        self._wake.set()

    # ------------------------------------------------------------ fetcher

    def _fetcher(self) -> None:
        while not self.stopping:
            if self.holding and self.online and self.tablet is not None:
                try:
                    self.fetch_once()
                except Exception as exc:      # noqa: BLE001 - try again next round
                    logger.warning("fetching capture files failed: %s", exc, exc_info=not isinstance(exc, TabletError))
                    self.fetch_error = (exc.message if isinstance(exc, TabletError) else str(exc))[:300]
                finally:
                    self.fetching = None
            self._stopping.wait(FETCH_EVERY_S)

    def fetch_once(self) -> int:
        """Move every finished file from the tablet; returns how many were stored."""
        tablet = self.tablet
        sessions = tablet.spool()
        stored = 0
        for s in sessions:
            sid = s["session"]
            if not SESSION_ID.match(sid):
                continue
            with self.plugin.connect() as conn:
                if store.capture(conn, sid) is None:
                    store.add_capture(conn, sid, "manual" if sid.startswith("m-") else "other")
                have = store.stored_files(conn, sid)
            folder = self.capture_dir(sid)
            folder.mkdir(parents=True, exist_ok=True)
            files = [f for f in s["files"] if f["name"] != "session.json"]
            for f in files:
                if self.stopping:
                    return stored
                name = f["name"]
                if have.get(name) != f["size"]:
                    if not self._room_for(f["size"]):
                        return stored
                    self.fetching = {"session": sid, "name": name, "bytes": f["size"]}
                    tablet.fetch(sid, name, folder / name, expected_size=f["size"])
                    with self.plugin.connect() as conn:
                        store.add_file(conn, sid, name, f["size"], file_info(folder / name))
                    stored += 1
                tablet.delete_spool(sid, name)
            if s["complete"] and any(f["name"] == "session.json" for f in s["files"]):
                tablet.fetch(sid, "session.json", folder / "session.json")
                meta = json.loads((folder / "session.json").read_text())
                with self.plugin.connect() as conn:
                    store.add_file(conn, sid, "session.json", (folder / "session.json").stat().st_size)
                    files_now = store.capture_files(conn, sid)
                    store.finish_capture(conn, sid, meta, summarise(meta, files_now))
                    store.add_event(conn, "capture_stored", session_id=sid, run_id=(meta.get("request") or {}).get("run_id"),
                                    data={"files": len(files_now), "bytes": sum(x["bytes"] for x in files_now),
                                          "stop_reason": meta.get("stop_reason")})
                tablet.delete_spool(sid, "session.json")
                stored += 1
        self.fetch_error = None
        return stored

    def _room_for(self, size: int) -> bool:
        d = self.disk()
        low = d["free_bytes"] - size < d["min_free_bytes"]
        if low != self.low_disk:
            self.low_disk = low
            try:
                with self.plugin.connect() as conn:
                    store.add_event(conn, "low_disk" if low else "disk_ok", data={"free_bytes": d["free_bytes"]})
            except Exception:
                pass
        return not low

    # ------------------------------------------------------------ status (no database)

    def status(self) -> dict:
        rep = self.report or {}
        offset = None
        if rep.get("time") and self.report_at:
            offset = round(rep["time"] - self.report_at, 3)
        want = self.want
        return {
            "holding": self.holding,
            "configured": self.tablet is not None,
            "enabled": self.settings.enabled,
            "address": self.settings.base_url,
            "online": self.online,
            "error": self.error,
            "db_error": self.db_error,
            "report_age_s": round(self.clock() - self.report_at, 1) if self.report_at else None,
            "clock_offset_s": offset,
            "tablet": rep or None,
            "active_run": {"run_id": self.active["run_id"], "experiment_id": self.active.get("experiment_id")} if self.active else None,
            "plan": ({"version_no": (self.binding.get("plan") or {}).get("version_no"),
                      "enabled": bool((self.binding.get("plan") or {}).get("enabled")),
                      "inferred": self.binding.get("inferred")} if self.binding else None),
            "want": {k: v for k, v in want.items() if k != "doc"} if want else None,
            "decision": self.last_decision,
            "waiting_media": self.waiting_media,
            "pushing": self.pushing,
            "push_queue": len(self._push_queued),
            "push_error": self.push_error,
            "fetching": self.fetching,
            "fetch_error": self.fetch_error,
            "low_disk": self.low_disk,
            "disk": self.disk(),
        }


def sample_of(report: dict) -> dict:
    """The few numbers kept every 15 s for the health charts."""
    mem = report.get("memory") or {}
    bat = report.get("battery") or {}
    total, avail = mem.get("total_mb"), mem.get("available_mb")
    cap = bat.get("capacity")
    return {
        "battery": float(cap) if isinstance(cap, (int, float, str)) and str(cap).replace(".", "", 1).isdigit() else None,
        "charging": bat.get("status"),
        "temperature": report.get("temperature"),
        "mem_used_mb": (total - avail) if isinstance(total, (int, float)) and isinstance(avail, (int, float)) else None,
        "agent_rss_mb": mem.get("agent_rss_mb"),
        "mpv_rss_mb": mem.get("mpv_rss_mb"),
        "load1": (report.get("load") or [None])[0],
        "spool_bytes": (report.get("spool") or {}).get("bytes"),
        "capturing": bool(report.get("capture")),
    }


def file_info(path: Path) -> dict:
    """Touch files: how many frames and how many with a finger down."""
    if not path.name.startswith("touch_") or path.suffix != ".jsonl":
        return {}
    frames = touching = max_fingers = 0
    with path.open() as f:
        for line in f:
            if not line.strip():
                continue
            try:
                n = len(json.loads(line).get("touches") or [])
            except ValueError:
                continue
            frames += 1
            touching += n > 0
            max_fingers = max(max_fingers, n)
    return {"frames": frames, "touching": touching, "max_fingers": max_fingers}


def summarise(meta: dict, files: list[dict]) -> dict:
    seg = meta.get("segments") or []
    touch = [f["info"] for f in files if f["name"].startswith("touch_")]
    started, ended = meta.get("started_utc"), meta.get("ended_utc")
    return {
        "duration_s": round(ended - started, 1) if started and ended else None,
        "segments": len(seg),
        "bytes": sum(f["bytes"] for f in files),
        "touch_frames": sum(t.get("frames", 0) for t in touch),
        "touch_frames_down": sum(t.get("touching", 0) for t in touch),
        "max_fingers": max([t.get("max_fingers", 0) for t in touch] or [0]),
        "screen_changes": len((meta.get("screen") or {}).get("log") or []),
        "error": meta.get("error"),
    }
