"""The real tablet agent (tablet/kiosk) with a fake player, camera, microphone and touch screen.

``running_agent(tmp)`` serves it on 127.0.0.1 and yields (agent, base_url, token).
"""

from __future__ import annotations

import contextlib
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "tablet") not in sys.path:
    sys.path.insert(0, str(ROOT / "tablet"))

from kiosk import agent as agent_mod  # noqa: E402
from kiosk import capture as capture_mod  # noqa: E402
from kiosk import system as system_mod  # noqa: E402
from kiosk import touch as touch_mod  # noqa: E402
from kiosk.config import Config  # noqa: E402
from kiosk.server import serve  # noqa: E402

TOKEN = "tablet-token-0123456789"


class FakePlayer:
    def __init__(self):
        self.mode, self.playlist, self.loop, self.image_duration = "content", [], True, 10.0
        self.listeners, self.current, self.proc = [], None, None
        self._brightness = 0.6
        self.shown: list[tuple] = []

    def start(self):
        pass

    def ensure_running(self):
        pass

    def show(self, mode, playlist=None, loop=None, image_duration=None):
        if playlist is not None:
            self.playlist = list(playlist)
        self.mode = mode
        self.current = str(self.playlist[0]) if mode == "content" and self.playlist else None
        self.shown.append((mode, [p.name for p in self.playlist]))
        for fn in list(self.listeners):
            fn({"t": time.time(), "mode": mode, "file": Path(self.current).name if self.current else None})

    def set_mode(self, mode):
        self.show(mode)

    def snapshot(self):
        return {"mode": self.mode, "playlist": list(self.playlist), "loop": self.loop, "image_duration": self.image_duration}

    def restore(self, snap):
        self.show(snap["mode"], snap["playlist"], snap["loop"], snap["image_duration"])

    def brightness(self):
        return self._brightness

    def set_brightness(self, value):
        self._brightness = float(value)
        return self._brightness

    def control(self, action):
        pass

    def position(self):
        return {"file": Path(self.current).name, "pos": 1.0} if self.current else None

    def status(self):
        return {"mode": self.mode, "playlist_len": len(self.playlist), "running": True}

    @staticmethod
    def display_name(path):
        return Path(path).name if path else None


class FakeCapture:
    """Writes one finished segment at start and one more at stop, like splitmuxsink."""

    def __init__(self, cfg, session_id, video, audio, on_file_done, emit):
        self.dir = cfg.spool_dir / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ae = None
        self.segments = []
        self.started = None

    def _segment(self, i):
        (self.dir / f".av_{i:05d}.mp4").write_bytes(b"\0" * (1000 + i))
        (self.dir / f".av_{i:05d}.mp4").replace(self.dir / f"av_{i:05d}.mp4")
        self.segments.append({"file": f"av_{i:05d}.mp4", "start_utc": time.time(), "end_utc": time.time()})

    def start(self):
        self.started = time.time()
        self._segment(0)
        (self.dir / ".av_00001.mp4").write_bytes(b"partial")   # being written

    def stop(self):
        (self.dir / ".av_00001.mp4").unlink(missing_ok=True)
        self._segment(1)

    def metadata(self):
        return {"started_utc": self.started, "ended_utc": time.time(), "container": "mp4",
                "video": {"width": 640, "height": 480, "codec": "h264"},
                "audio": {"codec": "opus", "tracks": ["mic_echo_cancelled", "playback_reference", "mic_raw"]},
                "segments": self.segments, "error": None}


class FakeTouch:
    def __init__(self, cfg, session_dir, on_file_done):
        self.dir = session_dir

    def start(self):
        lines = [{"t": time.time(), "touches": [{"id": 1, "x": 0.5, "y": 0.5}]}, {"t": time.time() + 0.01, "touches": []}]
        (self.dir / "touch_00001.jsonl").write_text("".join(json.dumps(x) + "\n" for x in lines))

    def stop(self):
        pass


class FakeSystem:
    volume = 0.5


@contextlib.contextmanager
def running_agent(tmp: Path, monkeypatch):
    state = tmp / "tablet-state"
    state.mkdir(parents=True, exist_ok=True)
    cfg = Config(token=TOKEN, listen_host="127.0.0.1", listen_port=0, device_id="tablet-test",
                 media_dir=state / "media", spool_dir=state / "spool")
    cfg.media_dir.mkdir(exist_ok=True)
    cfg.spool_dir.mkdir(exist_ok=True)
    monkeypatch.setattr(capture_mod, "CaptureSession", FakeCapture)
    monkeypatch.setattr(touch_mod, "TouchRecorder", FakeTouch)
    monkeypatch.setattr(system_mod, "get_volume", lambda: FakeSystem.volume)
    monkeypatch.setattr(system_mod, "set_volume", lambda v: setattr(FakeSystem, "volume", float(v)) or float(v))
    monkeypatch.setattr(system_mod, "screen_power", lambda *a: None)
    agent = agent_mod.Agent(cfg, player=FakePlayer(), state_dir=state)
    httpd = serve(agent, "127.0.0.1", 0)
    try:
        yield agent, f"http://127.0.0.1:{httpd.server_address[1]}", TOKEN
    finally:
        httpd.shutdown()
        httpd.server_close()
