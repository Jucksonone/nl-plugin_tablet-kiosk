"""Device agent: one WebSocket to the server, commands in, acks/status/events out."""

import asyncio
import json
import logging
import os
import secrets
import subprocess
import threading
import time
import urllib.parse

import websockets

from . import system
from .cache import MediaCache
from .config import STATE_DIR, Config
from .player import Player
from .uploader import Uploader

log = logging.getLogger(__name__)

VERSION = "0.1.0"


class Agent:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.loop: asyncio.AbstractEventLoop | None = None
        self.outbox: asyncio.Queue | None = None
        self.status_now: asyncio.Event | None = None   # set to push a status report early
        self.cache = MediaCache(cfg.media_dir, cfg.cache_max_bytes, cfg.server_url)
        self.player = Player(cfg, STATE_DIR)
        self.uploader = Uploader(cfg, self.emit)
        self.capture_lock = threading.Lock()
        self.capture = None            # dict with session objects while running
        self.playlist_job: threading.Thread | None = None

    # ------------------------------------------------------------ outbound

    def emit(self, name: str, **data) -> None:
        """Thread-safe: queue an event for the server."""
        msg = {"type": "event", "name": name, "t": time.time(), **data}
        log.info("event %s %s", name, data)
        if self.loop and self.outbox:
            self.loop.call_soon_threadsafe(self._queue_out, msg)
            if name.startswith("capture_"):
                self.push_status()

    def push_status(self) -> None:
        """Thread-safe: send a status report now instead of at the next interval."""
        if self.loop and self.status_now:
            self.loop.call_soon_threadsafe(self.status_now.set)

    def _queue_out(self, msg: dict) -> None:
        if self.outbox.full():
            self.outbox.get_nowait()        # drop the oldest while offline
        self.outbox.put_nowait(msg)

    def status(self) -> dict:
        with self.capture_lock:
            cap = None
            if self.capture:
                s = self.capture
                cap = {"session": s["id"], "started": s["started"], "until": s["until"],
                       "exposure": s["av"].ae.last if s["av"] and s["av"].ae else None}
        return {
            "version": VERSION,
            "uptime": system.uptime(),
            "volume": system.get_volume(),
            "brightness": self.player.brightness(),
            "player": self.player.status(),
            "cache_bytes": self.cache.usage(),
            "cache_pending": self.cache.pending,
            "capture": cap,
            "upload_pending": self.uploader.pending(),
            "battery": system.battery(),
            "temperature": system.soc_temperature(),
            "memory": {**system.memory(),
                       "agent_rss_mb": system.process_rss_mb(os.getpid()),
                       "mpv_rss_mb": system.process_rss_mb(self.player.proc.pid if self.player.proc else None)},
            "load": system.load_average(),
        }

    # ------------------------------------------------------------ commands

    def handle(self, cmd: str, params: dict):
        """Runs in a worker thread; returns the ack result or raises."""
        if cmd == "ping":
            return {"pong": time.time()}
        if cmd == "get_status":
            return self.status()
        if cmd == "set_volume":
            return {"volume": system.set_volume(params["value"])}
        if cmd == "set_brightness":
            return {"brightness": self.player.set_brightness(params["value"])}
        if cmd == "set_screen":
            self.player.set_mode(params["mode"])
            return {"mode": self.player.mode}
        if cmd == "player":
            self.player.control(params["action"])
            return self.player.status()
        if cmd == "set_playlist":
            return self._set_playlist(params)
        if cmd == "show":
            return self._show(params)
        if cmd == "capture_start":
            return self._capture_start(params)
        if cmd == "capture_stop":
            return self._capture_stop(reason="command")
        raise ValueError(f"unknown command: {cmd}")

    def _set_playlist(self, params: dict) -> dict:
        items = params.get("items", [])
        missing = sum(not self.cache.is_ready(it) for it in items)

        def job():
            try:
                paths = self.cache.ensure(items)
                self.player.set_playlist(paths, params.get("loop", True),
                                         float(params.get("image_duration", 10.0)))
                self.emit("playlist_ready", items=len(paths))
            except Exception as e:
                log.exception("playlist failed")
                self.emit("playlist_failed", error=str(e))

        self.playlist_job = threading.Thread(target=job, name="playlist", daemon=True)
        self.playlist_job.start()
        return {"accepted": True, "items": len(items), "to_download": missing}

    def _show(self, screen: dict) -> dict:
        """Put a mode (and optionally media) on screen; downloads first, so it can be slow."""
        mode = screen.get("mode", "content")
        items = screen.get("items")
        if items:
            paths = self.cache.ensure(items)
            self.player.show(mode, paths, screen.get("loop", True),
                             float(screen.get("image_duration", 10.0)))
        else:
            self.player.show(mode)
        return {"mode": self.player.mode, "items": [p["name"] for p in items or []]}

    def _capture_start(self, params: dict) -> dict:
        from .capture import CaptureSession
        from .touch import TouchRecorder

        duration = float(params.get("duration_s", 60))
        if not 1 <= duration <= 6 * 3600:
            raise ValueError("duration_s must be between 1 and 21600")
        video = bool(params.get("video", True))
        audio = bool(params.get("audio", True))
        touch = bool(params.get("touch", True))
        sid = params.get("session_id") or time.strftime("%Y%m%d-%H%M%S-") + secrets.token_hex(2)
        screen = params.get("screen")              # None = leave the screen as it is
        volume = params.get("volume")              # None = leave as is, else 0..1
        brightness = params.get("brightness")
        restore = bool(params.get("restore", True))

        with self.capture_lock:
            if self.capture:
                raise RuntimeError(f"capture already running: {self.capture['id']}")
            self.capture = cap = {
                "id": sid, "started": time.time(), "until": time.time() + duration,
                "av": None, "touch": None, "timer": None, "screen": screen,
                "screen_log": [], "playback": [], "restore": {}, "settings": {},
                "stop_sampling": threading.Event()}
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
            elif screen and screen.get("items"):
                first_file = screen["items"][0]["name"]   # mpv may not have reported it yet
            else:
                first_file = self.player.display_name(self.player.current)
            log_screen({"t": time.time(), "mode": mode, "file": first_file})

            av = None
            if video or audio:
                av = CaptureSession(self.cfg, sid, video, audio, self.uploader.enqueue, self.emit)
                av.start()
            tr = None
            if touch:
                (self.cfg.spool_dir / sid).mkdir(parents=True, exist_ok=True)
                tr = TouchRecorder(self.cfg, self.cfg.spool_dir / sid, self.uploader.enqueue)
                tr.start()
        except Exception:
            with self.capture_lock:
                self.capture = None
            self._undo_capture_settings(cap)
            raise

        threading.Thread(target=self._sample_playback, args=(cap,), name="playback-sampler",
                         daemon=True).start()
        timer = threading.Timer(duration, self._capture_stop, kwargs={"reason": "duration"})
        timer.daemon = True
        with self.capture_lock:
            cap.update(av=av, touch=tr, timer=timer, started=time.time(), until=time.time() + duration)
        timer.start()
        self.emit("capture_started", session=sid, duration_s=duration,
                  video=video, audio=audio, touch=touch)
        return {"session": sid, "duration_s": duration}

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
        if cap["log_fn"] in self.player.listeners:
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

    def _capture_stop(self, reason: str) -> dict:
        with self.capture_lock:
            cap, self.capture = self.capture, None
        if not cap:
            return {"stopped": None}
        from .capture import write_session_json

        cap["timer"].cancel()
        cap["stop_sampling"].set()
        if cap["touch"]:
            cap["touch"].stop()
        meta = {"session": cap["id"], "device_id": self.cfg.device_id,
                "started_utc": cap["started"], "ended_utc": time.time(),
                "video": None, "audio": None, "segments": [], "error": None}
        if cap["av"]:
            cap["av"].stop()
            meta.update(cap["av"].metadata())
        requested = cap["screen"]
        meta.update(
            stop_reason=reason, touch=bool(cap["touch"]), settings=cap["settings"],
            screen={"requested": requested and {
                        "mode": requested.get("mode", "content"),
                        "items": [it["name"] for it in requested.get("items") or []]},
                    "log": cap["screen_log"],
                    "playback": cap["playback"],
                    "restored": sorted(cap["restore"])})
        self._undo_capture_settings(cap)
        self.uploader.enqueue(write_session_json(self.cfg.spool_dir / cap["id"], meta))
        self.emit("capture_finished", session=cap["id"], reason=reason)
        return {"stopped": cap["id"], "reason": reason}

    # ------------------------------------------------------------ main loop

    async def run(self) -> None:
        self.loop = asyncio.get_running_loop()
        self.outbox = asyncio.Queue(maxsize=200)
        self.status_now = asyncio.Event()
        self.uploader.start()
        # Undo a screen-off left behind by a previous run (panel off, touch detached from X).
        await self.loop.run_in_executor(None, system.screen_power, True, self.cfg.touch_device_name)
        await self.loop.run_in_executor(None, self.player.start)

        ws_url = self.cfg.server_url.replace("http", "ws", 1)
        url = (f"{ws_url}/ws/device/{urllib.parse.quote(self.cfg.device_id)}"
               f"?token={urllib.parse.quote(self.cfg.token)}")
        delay = 1.0
        while True:
            try:
                async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                    log.info("connected to %s", self.cfg.server_url)
                    delay = 1.0
                    await ws.send(json.dumps({"type": "hello", "data": {
                        "device_id": self.cfg.device_id, "version": VERSION}}))
                    await self._session(ws)
            except (OSError, websockets.exceptions.WebSocketException) as e:
                log.warning("server connection lost: %s", e)
            await asyncio.sleep(delay)
            delay = min(delay * 2, 30.0)

    async def _session(self, ws) -> None:
        async def sender():
            while True:
                await ws.send(json.dumps(await self.outbox.get()))

        async def status_loop():
            while True:
                await self.loop.run_in_executor(None, self.player.ensure_running)
                self.status_now.clear()
                data = await self.loop.run_in_executor(None, self.status)
                await ws.send(json.dumps({"type": "status", "data": data}))
                try:
                    await asyncio.wait_for(self.status_now.wait(), self.cfg.status_interval)
                except asyncio.TimeoutError:
                    pass

        async def receiver():
            async for raw in ws:
                msg = json.loads(raw)
                if msg.get("type") == "command":
                    asyncio.create_task(self._run_command(ws, msg))

        tasks = [asyncio.create_task(t()) for t in (sender, status_loop, receiver)]
        try:
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            for t in done:
                t.result()
        finally:
            for t in tasks:
                t.cancel()

    async def _run_command(self, ws, msg: dict) -> None:
        cid, cmd, params = msg.get("id"), msg.get("cmd"), msg.get("params") or {}
        log.info("command %s %s", cmd, params if cmd != "set_playlist" else "...")
        try:
            result = await self.loop.run_in_executor(None, self.handle, cmd, params)
            ack = {"type": "ack", "id": cid, "ok": True, "result": result}
        except Exception as e:
            log.exception("command %s failed", cmd)
            ack = {"type": "ack", "id": cid, "ok": False, "error": f"{type(e).__name__}: {e}"}
        await ws.send(json.dumps(ack))
        if cmd not in ("ping", "get_status"):
            self.push_status()


def wait_for_display(timeout: float = 120) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if system.display_available():
            subprocess.run(["xset", "s", "off", "-dpms"], capture_output=True)
            return
        time.sleep(2)
    raise RuntimeError("X display not available")
