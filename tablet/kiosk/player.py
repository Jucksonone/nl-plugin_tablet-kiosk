"""Full-screen mpv controlled over its JSON IPC socket, plus screen modes."""

import json
import logging
import os
import socket
import subprocess
import threading
import time
from pathlib import Path

from . import system

log = logging.getLogger(__name__)

SCREEN_MODES = ("content", "white", "black", "off")
COLOR_SOURCE = "av://lavfi:color=c={color}:s=160x100:r=5"   # 16:10 like the panel, so no bars
DEFAULT_BRIGHTNESS = 0.6       # used when we never saw the panel's brightness


class Player:
    def __init__(self, cfg, state_dir: Path):
        self.cfg = cfg
        self.sock_path = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp")) / "kiosk-mpv.sock"
        self.playlist_file = state_dir / "playlist.m3u"
        self.state_file = state_dir / "screen.json"     # survives agent restarts and reboots
        self.proc: subprocess.Popen | None = None
        self.lock = threading.Lock()                 # one IPC exchange at a time
        self.state_lock = threading.RLock()          # one screen change at a time
        self.mode = "content"
        self.playlist: list[Path] = []
        self.loop = True
        self.image_duration = 10.0
        self.current: str | None = None
        self.listeners: list = []          # fn(event: dict), called on mode/file changes
        self.watch_thread: threading.Thread | None = None
        # Panel brightness from before the screen was switched off: the driver
        # reports 0 while the panel is powered down, so we keep our own copy.
        self.saved_brightness: float | None = None
        self._load_state()

    def _load_state(self) -> None:
        try:
            st = json.loads(self.state_file.read_text())
        except (OSError, ValueError):
            return
        paths = [Path(p) for p in st.get("playlist", []) if Path(p).is_file()]
        self.mode = st.get("mode", "content") if st.get("mode") in SCREEN_MODES else "content"
        self.loop = st.get("loop", True)
        self.image_duration = st.get("image_duration", 10.0)
        self.saved_brightness = st.get("saved_brightness")
        self._set_list(paths)

    def _save_state(self) -> None:
        tmp = self.state_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"mode": self.mode, "playlist": [str(p) for p in self.playlist],
                                   "loop": self.loop, "image_duration": self.image_duration,
                                   "saved_brightness": self.saved_brightness}))
        tmp.replace(self.state_file)

    def _set_list(self, paths: list[Path]) -> None:
        self.playlist = paths
        self.playlist_file.write_text("".join(f"{p}\n" for p in paths))

    # ------------------------------------------------------------ process

    def start(self) -> None:
        if self.proc and self.proc.poll() is None:
            return
        self.sock_path.unlink(missing_ok=True)
        args = [
            "mpv", "--idle=yes", "--force-window=yes", "--fullscreen",
            "--no-terminal", "--no-osc", "--osd-level=0", "--no-input-default-bindings",
            "--input-vo-keyboard=no", "--window-dragging=no", "--cursor-autohide=always",
            "--hwdec=vaapi", "--keep-open=no",
            # Only ever draw inside X. Without this, mpv started while X is down
            # takes over the GPU directly and the next X server fails to start.
            "--vo=gpu", "--gpu-context=x11egl",
            # mpv keeps waking the display while playing, which would undo screen "off"
            "--stop-screensaver=no",
            f"--audio-device={self.cfg.mpv_audio_device}",
            f"--input-ipc-server={self.sock_path}",
            *self.cfg.mpv_extra_args,
        ]
        log.info("starting mpv")
        self.proc = subprocess.Popen(args)
        for _ in range(100):
            if self.sock_path.exists():
                break
            if self.proc.poll() is not None:
                raise RuntimeError(f"mpv failed to start (exit code {self.proc.returncode})")
            time.sleep(0.1)
        self.watch_thread = threading.Thread(target=self._watch, args=(self.proc,),
                                             name="mpv-watch", daemon=True)
        self.watch_thread.start()
        self._apply()
        if self.mode == "off":                # restored from the saved state
            system.screen_power(False, self.cfg.touch_device_name)
        else:
            self._restore_brightness()        # in case a previous run left the backlight at 0

    def _watch(self, proc: subprocess.Popen) -> None:
        """Follow mpv's current file over a second IPC connection."""
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.connect(str(self.sock_path))
                s.sendall(b'{"command": ["observe_property", 1, "path"]}\n')
                buf = b""
                while proc.poll() is None:
                    chunk = s.recv(65536)
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        msg = json.loads(line)
                        if msg.get("event") == "property-change" and msg.get("name") == "path":
                            self.current = msg.get("data")
                            if self.current:
                                self._notify()
        except (OSError, ValueError) as e:
            log.warning("mpv watcher stopped: %s", e)

    def _notify(self) -> None:
        event = {"t": time.time(), "mode": self.mode, "file": self.display_name(self.current)}
        for fn in list(self.listeners):
            try:
                fn(event)
            except Exception:
                log.exception("player listener failed")

    @staticmethod
    def display_name(path: str | None) -> str | None:
        if not path:
            return None
        if path.startswith("av://lavfi:color="):
            return None                     # white/black screens are described by the mode
        return os.path.basename(path)

    def ensure_running(self) -> None:
        if not self.proc or self.proc.poll() is not None:
            if not system.display_available():
                log.warning("mpv not running and X is not available; waiting")
                return
            log.warning("mpv not running, restarting")
            self.start()
        # Safety net in case something still woke the panel. Taken under the state
        # lock so it never sees a half-finished switch away from "off".
        with self.state_lock:
            if self.mode == "off":
                system.screen_power(False, self.cfg.touch_device_name)

    def stop(self) -> None:
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()
            self.proc.wait(timeout=5)

    def _ipc(self, *command) -> dict:
        with self.lock, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(str(self.sock_path))
            s.sendall(json.dumps({"command": list(command)}).encode() + b"\n")
            buf = b""
            while True:
                chunk = s.recv(65536)
                if not chunk:
                    raise ConnectionError("mpv closed the IPC socket")
                buf += chunk
                while b"\n" in buf:
                    line, buf = buf.split(b"\n", 1)
                    msg = json.loads(line)
                    if "event" not in msg:   # skip async events, return the reply
                        if msg.get("error") not in ("success", None):
                            raise RuntimeError(f"mpv {command[0]}: {msg['error']}")
                        return msg

    # ------------------------------------------------------------ state

    def set_playlist(self, paths: list[Path], loop: bool, image_duration: float) -> None:
        with self.state_lock:
            self._set_playlist(paths, loop, image_duration)

    def _set_playlist(self, paths: list[Path], loop: bool, image_duration: float) -> None:
        self.loop, self.image_duration = loop, image_duration
        self._set_list(paths)
        if self.mode == "content":
            self._apply()
        self._save_state()

    def snapshot(self) -> dict:
        return {"mode": self.mode, "playlist": list(self.playlist), "loop": self.loop,
                "image_duration": self.image_duration}

    def show(self, mode: str, playlist: list[Path] | None = None, loop: bool | None = None,
             image_duration: float | None = None) -> None:
        """Switch mode and (optionally) playlist in one step, without flashing old content."""
        with self.state_lock:
            self._show(mode, playlist, loop, image_duration)

    def _show(self, mode, playlist, loop, image_duration) -> None:
        if playlist is not None:
            self._set_list(playlist)
        if loop is not None:
            self.loop = loop
        if image_duration is not None:
            self.image_duration = image_duration
        self._set_mode(mode)

    def restore(self, snap: dict) -> None:
        self.show(snap["mode"], snap["playlist"], snap["loop"], snap["image_duration"])

    def set_mode(self, mode: str) -> None:
        with self.state_lock:
            self._set_mode(mode)

    def _set_mode(self, mode: str) -> None:
        if mode not in SCREEN_MODES:
            raise ValueError(f"mode must be one of {SCREEN_MODES}")
        if mode == "off":
            if self.mode != "off":
                self.saved_brightness = system.get_brightness() or self.saved_brightness
            system.screen_power(False, self.cfg.touch_device_name)
        elif self.mode == "off":
            system.screen_power(True, self.cfg.touch_device_name)
            self._restore_brightness(saved_wins=True)
        self.mode = mode
        self._apply()
        self._save_state()
        if mode != "content":
            self._notify()       # colour screens don't change "path" in a useful way

    def _apply(self) -> None:
        # A single item is shown "as is": a video loops forever, an image stays
        # on screen until something else is shown. Longer playlists rotate.
        single = len(self.playlist) == 1
        self._ipc("set", "image-display-duration", "inf" if single else str(self.image_duration))
        self._ipc("set_property", "loop-playlist", "inf" if self.loop else "no")
        if self.mode == "content" and self.playlist:
            self._ipc("set", "loop-file", "inf" if single else "no")
            self._ipc("loadlist", str(self.playlist_file), "replace")
        else:
            color = "white" if self.mode == "white" else "black"
            self._ipc("set_property", "loop-file", "inf")
            self._ipc("loadfile", COLOR_SOURCE.format(color=color), "replace")

    def control(self, action: str) -> None:
        commands = {
            "pause": ("set_property", "pause", True),
            "resume": ("set_property", "pause", False),
            "next": ("playlist-next", "force"),
            "prev": ("playlist-prev", "force"),
        }
        if action not in commands:
            raise ValueError(f"action must be one of {list(commands)}")
        self._ipc(*commands[action])

    def _restore_brightness(self, saved_wins: bool = False) -> None:
        """Bring the backlight back after the panel was off.

        saved_wins: apply the remembered value even if the driver kept a
        non-zero level (it may have been changed while the screen was off).
        """
        if saved_wins and self.saved_brightness is not None:
            system.set_brightness(self.saved_brightness)
        elif system.get_brightness() == 0:
            system.set_brightness(self.saved_brightness or DEFAULT_BRIGHTNESS)

    def brightness(self) -> float | None:
        """Brightness the panel has (or will have once switched back on)."""
        if self.mode == "off":
            return self.saved_brightness
        return system.get_brightness()

    def set_brightness(self, value: float) -> float:
        with self.state_lock:
            if self.mode == "off":            # applied when the screen comes back on
                self.saved_brightness = min(max(float(value), 0.0), 1.0)
                self._save_state()
                return self.saved_brightness
            return system.set_brightness(value)

    def position(self) -> dict | None:
        """Current file and playback position (seconds) while showing content."""
        if self.mode != "content" or not self.playlist:
            return None
        try:
            pos = self._ipc("get_property", "playback-time").get("data")
            path = self._ipc("get_property", "path").get("data")
        except (OSError, RuntimeError, ConnectionError):
            return None
        if pos is None or not path:
            return None
        return {"file": self.display_name(path), "pos": round(pos, 3)}

    def status(self) -> dict:
        out = {"mode": self.mode, "playlist_len": len(self.playlist),
               "running": bool(self.proc and self.proc.poll() is None)}
        if out["running"]:
            try:
                out["current"] = os.path.basename(self._ipc("get_property", "path").get("data") or "")
                out["paused"] = self._ipc("get_property", "pause").get("data")
            except (OSError, RuntimeError, ConnectionError):
                pass
        return out
