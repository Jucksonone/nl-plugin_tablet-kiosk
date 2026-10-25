"""Device agent: the plugin's HTTP interface in, the player / capture / touch on the tablet.

The Natural Land plugin connects to the tablet (server.py). A capture the plugin
starts carries a lease: the plugin renews it every few seconds, and when it
stops renewing (plugin stopped, host down, network gone) the tablet stops the
capture by itself and puts back what the capture changed.
"""

import logging
import os
import secrets
import subprocess
import threading
import time

from . import system
from .cache import MediaCache
from .config import STATE_DIR, Config
from .events import EventLog
from .player import Player
from .spool import SAFE_NAME, Spool

log = logging.getLogger(__name__)

VERSION = "0.2.0"
MAX_DURATION_S = 24 * 3600
ENSURE_PLAYER_EVERY_S = 5.0


class Agent:
    def __init__(self, cfg: Config, player=None, state_dir=STATE_DIR):
        self.cfg = cfg
        self.events = EventLog(state_dir, cfg.event_keep)
        self.cache = MediaCache(cfg.media_dir, cfg.cache_max_bytes)
        self.spool = Spool(cfg.spool_dir)
        self.player = player or Player(cfg, state_dir)
        self.capture_lock = threading.Lock()
        self.capture = None            # dict with session objects while running
        self.stop_event = threading.Event()

    # ------------------------------------------------------------ outbound

    def emit(self, name: str, **data) -> None:
        """Thread-safe: record an event for the plugin."""
        self.events.append(name, **data)

    def active_session(self) -> str | None:
        with self.capture_lock:
            return self.capture["id"] if self.capture else None

    def media_in_use(self) -> set[str]:
        names = {p.name for p in self.player.playlist}
        with self.capture_lock:
            if self.capture and self.capture["screen"]:
                names |= set(_names(self.capture["screen"].get("items")))
        return names

    def status(self) -> dict:
        with self.capture_lock:
            cap = None
            if self.capture:
                s = self.capture
                cap = {"session": s["id"], "started": s["started"], "until": s["until"],
                       "lease_until": s["lease_until"], "video": s["flags"]["video"],
                       "audio": s["flags"]["audio"], "touch": s["flags"]["touch"],
                       "exposure": s["av"].ae.last if s["av"] and s["av"].ae else None}
        return {
            "device_id": self.cfg.device_id,
            "version": VERSION,
            "time": time.time(),
            "instance": self.events.instance,
            "last_seq": self.events.seq,
            "uptime": system.uptime(),
            "volume": system.get_volume(),
            "brightness": self.player.brightness(),
            "player": self.player.status(),
            "cache_bytes": self.cache.usage(),
            "capture": cap,
            "spool": self.spool.usage(),
            "battery": system.battery(),
            "temperature": system.soc_temperature(),
            "memory": {**system.memory(),
                       "agent_rss_mb": system.process_rss_mb(os.getpid()),
                       "mpv_rss_mb": system.process_rss_mb(self.player.proc.pid if getattr(self.player, "proc", None) else None)},
            "load": system.load_average(),
        }

    # ------------------------------------------------------------ commands

    def handle(self, cmd: str, params: dict):
        """Runs in an HTTP worker thread; returns the result or raises."""
        if cmd == "ping":
            return {"pong": time.time()}
        if cmd == "get_status":
            return self.status()
        if cmd == "set_volume":
            value = system.set_volume(params["value"])
            self.emit("volume_set", value=value)
            return {"volume": value}
        if cmd == "set_brightness":
            value = self.player.set_brightness(params["value"])
            self.emit("brightness_set", value=value)
            return {"brightness": value}
        if cmd == "set_screen":
            self.player.set_mode(params["mode"])
            self.emit("screen_set", mode=self.player.mode)
            return {"mode": self.player.mode}
        if cmd == "player":
            self.player.control(params["action"])
            return self.player.status()
        if cmd == "show":
            result = self._show(params)
            self.emit("shown", **result)
            return result
        if cmd == "capture_start":
            return self._capture_start(params)
        if cmd == "capture_renew":
            return self._capture_renew(params)
        if cmd == "capture_stop":
            return self._capture_stop(reason=params.get("reason") or "command", session=params.get("session"))
        raise ValueError(f"unknown command: {cmd}")

    def _show(self, screen: dict) -> dict:
        """Put a mode (and optionally media already pushed to the tablet) on screen."""
        mode = screen.get("mode", "content")
        names = _names(screen.get("items"))
        if names:
            paths = self.cache.ensure(names)
            self.player.show(mode, paths, screen.get("loop", True),
                             float(screen.get("image_duration", 10.0)))
        else:
            self.player.show(mode)
        return {"mode": self.player.mode, "items": names}

    def _capture_start(self, params: dict) -> dict:
        from .capture import CaptureSession
        from .touch import TouchRecorder

        duration = float(params.get("duration_s", 60))
        if not 1 <= duration <= MAX_DURATION_S:
            raise ValueError(f"duration_s must be between 1 and {MAX_DURATION_S}")
        lease = params.get("lease_s")
        if lease is not None and not 5 <= float(lease) <= 600:
            raise ValueError("lease_s must be between 5 and 600")
        video = bool(params.get("video", True))
        audio = bool(params.get("audio", True))
        touch = bool(params.get("touch", True))
        sid = params.get("session_id") or time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        if not SAFE_NAME.match(sid):
            raise ValueError(f"bad session id: {sid}")
        if (self.cfg.spool_dir / sid).exists():
            raise ValueError(f"session {sid} already exists on the tablet")
        screen = params.get("screen")              # None = leave the screen as it is
        volume = params.get("volume")              # None = leave as is, else 0..1
        brightness = params.get("brightness")
        restore = bool(params.get("restore", True))
        if screen and _names(screen.get("items")):
            self.cache.ensure(_names(screen.get("items")))   # fail before touching anything

        with self.capture_lock:
            if self.capture:
                raise RuntimeError(f"capture already running: {self.capture['id']}")
            now = time.time()
            self.capture = cap = {
                "id": sid, "started": now, "until": now + duration,
                "lease_until": now + float(lease) if lease is not None else None,
                "flags": {"video": video, "audio": audio, "touch": touch},
                "av": None, "touch": None, "screen": screen, "meta": params.get("meta") or {},
                "screen_log": [], "playback": [], "restore": {}, "settings": {},
                "stop_sampling": threading.Event(), "stopping": False}
        screen_log = cap["screen_log"]

        def log_screen(event: dict) -> None:
            # mpv reports the new file shortly after we switch modes; keep one entry per change
            if not screen_log or (screen_log[-1]["mode"], screen_log[-1]["file"]) != (event["mode"], event["file"]):
                screen_log.append(event)
        cap["log_fn"] = log_screen

        try:
            if restore:
                if screen:
                    cap["restore"]["screen"] = self.player.snapshot()
                if volume is not None:
                    cap["restore"]["volume"] = system.get_volume()
                if brightness is not None:
                    cap["restore"]["brightness"] = self.player.brightness()
            if volume is not None:
                system.set_volume(volume)
            if brightness is not None:
                self.player.set_brightness(brightness)
            if screen:
                self._show(screen)    # content is on screen before recording starts
            cap["settings"] = {"volume": system.get_volume(), "brightness": self.player.brightness()}

            self.player.listeners.append(log_screen)
            mode = self.player.mode
            if mode != "content":
                first_file = None
            elif screen and _names(screen.get("items")):
                first_file = _names(screen["items"])[0]   # mpv may not have reported it yet
            else:
                first_file = self.player.display_name(self.player.current)
            log_screen({"t": time.time(), "mode": mode, "file": first_file})

            (self.cfg.spool_dir / sid).mkdir(parents=True, exist_ok=True)
            av = None
            if video or audio:
                av = CaptureSession(self.cfg, sid, video, audio, lambda _path: None, self.emit)
                av.start()
            tr = None
            if touch:
                tr = TouchRecorder(self.cfg, self.cfg.spool_dir / sid, lambda _path: None)
                tr.start()
        except Exception:
            with self.capture_lock:
                self.capture = None
            self._undo_capture_settings(cap)
            _remove_if_empty(self.cfg.spool_dir / sid)
            raise

        threading.Thread(target=self._sample_playback, args=(cap,), name="playback-sampler",
                         daemon=True).start()
        with self.capture_lock:
            now = time.time()
            cap.update(av=av, touch=tr, started=now, until=now + duration)
            if lease is not None:
                cap["lease_until"] = now + float(lease)
        self.emit("capture_started", session=sid, duration_s=duration, lease_s=lease,
                  video=video, audio=audio, touch=touch)
        return {"session": sid, "duration_s": duration, "until": cap["until"], "lease_until": cap["lease_until"]}

    def _capture_renew(self, params: dict) -> dict:
        lease = float(params.get("lease_s", 30))
        if not 5 <= lease <= 600:
            raise ValueError("lease_s must be between 5 and 600")
        with self.capture_lock:
            cap = self.capture
            if not cap or cap["id"] != params.get("session") or cap["stopping"]:
                raise RuntimeError(f"capture {params.get('session')} is not running")
            cap["lease_until"] = time.time() + lease
            return {"session": cap["id"], "lease_until": cap["lease_until"], "until": cap["until"]}

    def _sample_playback(self, cap: dict) -> None:
        """Once a second, pair wall-clock time with the stimulus video position.

        Together with the screen log this tells, for any capture timestamp, which
        frame of a looping video was on screen.
        """
        while not cap["stop_sampling"].wait(1.0 if cap["playback"] else 0.0):
            t0 = time.time()
            pos = self.player.position()
            t1 = time.time()
            if pos:
                cap["playback"].append({"t": round((t0 + t1) / 2, 4), **pos})

    def _undo_capture_settings(self, cap: dict) -> None:
        cap["stop_sampling"].set()
        if cap.get("log_fn") in self.player.listeners:
            self.player.listeners.remove(cap["log_fn"])
        undo = cap["restore"]
        try:
            if "screen" in undo:
                self.player.restore(undo["screen"])
            if undo.get("volume") is not None:
                system.set_volume(undo["volume"])
            if undo.get("brightness") is not None:
                self.player.set_brightness(undo["brightness"])
        except Exception:
            log.exception("restoring settings after capture failed")

    def _capture_stop(self, reason: str, session: str | None = None) -> dict:
        with self.capture_lock:
            cap = self.capture
            if not cap or (session and cap["id"] != session) or cap["stopping"]:
                return {"stopped": None}
            cap["stopping"] = True      # renewals are refused from here on
        from .capture import write_session_json

        try:
            cap["stop_sampling"].set()
            if cap["touch"]:
                cap["touch"].stop()
            meta = {"session": cap["id"], "device_id": self.cfg.device_id, "agent_version": VERSION,
                    "started_utc": cap["started"], "ended_utc": time.time(),
                    "video": None, "audio": None, "segments": [], "error": None}
            if cap["av"]:
                cap["av"].stop()
                meta.update(cap["av"].metadata())
            requested = cap["screen"]
            meta.update(
                stop_reason=reason, touch=bool(cap["touch"]), settings=cap["settings"], request=cap["meta"],
                screen={"requested": requested and {
                            "mode": requested.get("mode", "content"),
                            "items": _names(requested.get("items"))},
                        "log": cap["screen_log"],
                        "playback": cap["playback"],
                        "restored": sorted(cap["restore"])})
            self._undo_capture_settings(cap)
            write_session_json(self.cfg.spool_dir / cap["id"], meta)
        finally:
            with self.capture_lock:
                self.capture = None
        self.emit("capture_finished", session=cap["id"], reason=reason)
        return {"stopped": cap["id"], "reason": reason}

    # ------------------------------------------------------------ main loop

    def _watchdog(self) -> None:
        """Ends a capture at its duration or when its lease ran out."""
        with self.capture_lock:
            cap = self.capture
            if not cap or cap["stopping"]:
                return
            now = time.time()
            reason = ("lease_expired" if cap["lease_until"] is not None and now > cap["lease_until"]
                      else "duration" if now >= cap["until"] else None)
        if reason:
            self._capture_stop(reason=reason, session=cap["id"])

    def run(self) -> None:
        from .server import serve

        for sid in self.spool.recover():
            self.emit("capture_interrupted", session=sid)
        # Undo a screen-off left behind by a previous run (panel off, touch detached from X).
        system.screen_power(True, self.cfg.touch_device_name)
        self.player.start()
        httpd = serve(self, self.cfg.listen_host, self.cfg.listen_port)
        self.emit("agent_started", version=VERSION)
        next_ensure = 0.0
        try:
            while not self.stop_event.wait(0.5):
                try:
                    self._watchdog()
                except Exception:
                    log.exception("capture watchdog failed")
                if time.monotonic() >= next_ensure:
                    next_ensure = time.monotonic() + ENSURE_PLAYER_EVERY_S
                    try:
                        self.player.ensure_running()
                    except Exception:
                        log.exception("player check failed")
        finally:
            httpd.shutdown()
            self._capture_stop(reason="agent_stopped")


def _names(items) -> list[str]:
    """Media names from a list of names or of {"name": ...} dicts."""
    return [it["name"] if isinstance(it, dict) else str(it) for it in items or []]


def _remove_if_empty(folder) -> None:
    try:
        folder.rmdir()
    except OSError:
        pass


def wait_for_display(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if system.display_available():
            subprocess.run(["xset", "s", "off", "-dpms"], capture_output=True)
            return
        time.sleep(2)
    raise RuntimeError("X display not available")
