"""Background upload queue; finished files survive restarts in the spool dir."""

import logging
import queue
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


class Uploader:
    def __init__(self, cfg, emit):
        self.cfg = cfg
        self.emit = emit
        self.q: queue.Queue[Path] = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="uploader", daemon=True)

    def start(self) -> None:
        # Anything left in the spool from a previous run is complete (capture
        # renames files only once they are closed), so queue it for upload.
        for session in sorted(self.cfg.spool_dir.iterdir()):
            if session.is_dir():
                for f in sorted(session.iterdir(), key=lambda p: (p.name == "session.json", p.name)):
                    if f.is_file() and not f.name.startswith("."):
                        self.q.put(f)
        self.thread.start()

    def enqueue(self, path: Path) -> None:
        self.q.put(path)

    def pending(self) -> int:
        return self.q.qsize()

    def _run(self) -> None:
        while True:
            path = self.q.get()
            delay = 2.0
            while not self._upload(path):
                time.sleep(delay)
                delay = min(delay * 2, 60.0)

    def _upload(self, path: Path) -> bool:
        if not path.exists():
            return True
        session = path.parent.name
        url = "/".join([self.cfg.server_url, "api/uploads",
                        urllib.parse.quote(self.cfg.device_id), session, path.name])
        try:
            with path.open("rb") as f:
                req = urllib.request.Request(url, data=f, method="PUT", headers={
                    "Content-Length": str(path.stat().st_size),
                    "Content-Type": "application/octet-stream",
                    "Authorization": f"Bearer {self.cfg.token}",
                })
                with urllib.request.urlopen(req, timeout=60) as resp:
                    resp.read()
        except (OSError, urllib.error.URLError) as e:
            log.warning("upload of %s failed: %s", path, e)
            return False
        log.info("uploaded %s/%s", session, path.name)
        if not self.cfg.keep_uploaded:
            path.unlink()
            if path.name == "session.json":   # written last, after capture has stopped
                try:
                    path.parent.rmdir()       # fails harmlessly if segments are still queued
                except OSError:
                    pass
        self.emit("uploaded", session=session, file=path.name)
        return True
