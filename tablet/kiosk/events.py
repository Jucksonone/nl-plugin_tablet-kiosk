"""Numbered event log kept on disk, so the plugin can fetch what it missed.

Each event gets the next sequence number. ``instance`` is created once per
state directory: when it changes (the tablet was reset), the plugin starts
reading from the beginning again.
"""

import json
import logging
import secrets
import threading
import time
from collections import deque
from pathlib import Path

log = logging.getLogger(__name__)


class EventLog:
    def __init__(self, state_dir: Path, keep: int = 5000):
        self.path = state_dir / "events.jsonl"
        self.keep = keep
        self.lock = threading.Lock()
        instance_file = state_dir / "instance"
        if not instance_file.is_file():
            instance_file.write_text(secrets.token_hex(8))
        self.instance = instance_file.read_text().strip()
        self.events: deque[dict] = deque(maxlen=keep)
        self.seq = 0
        if self.path.is_file():
            for line in self.path.read_text().splitlines():
                try:
                    ev = json.loads(line)
                except ValueError:
                    continue
                self.events.append(ev)
                self.seq = max(self.seq, int(ev.get("seq", 0)))
        self._rewrite()       # trim the file to what is kept

    def _rewrite(self) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(e) + "\n" for e in self.events))
        tmp.replace(self.path)

    def append(self, name: str, **data) -> dict:
        with self.lock:
            self.seq += 1
            ev = {"seq": self.seq, "t": time.time(), "name": name, **data}
            self.events.append(ev)
            try:
                with self.path.open("a") as f:
                    f.write(json.dumps(ev) + "\n")
                if self.seq % self.keep == 0:
                    self._rewrite()
            except OSError:
                log.exception("writing the event log failed")
        log.info("event %s %s", name, data)
        return ev

    def after(self, seq: int, limit: int = 500) -> list[dict]:
        with self.lock:
            return [e for e in self.events if e["seq"] > seq][:limit]
