"""Finished capture files wait in the spool until the plugin has fetched them.

Capture writes hidden files (``.name``) and renames them once closed, so every
visible file is complete. A session is complete once its ``session.json`` is
there (written last). The plugin deletes each file after storing it.
"""

import json
import logging
import re
import shutil
from pathlib import Path

log = logging.getLogger(__name__)

SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def safe(name: str) -> str:
    if not SAFE_NAME.match(name):
        raise ValueError(f"bad name: {name}")
    return name


class Spool:
    def __init__(self, root: Path):
        self.root = root

    def sessions(self, active: str | None = None) -> list[dict]:
        out = []
        for s in sorted(self.root.iterdir()):
            if not s.is_dir() or not SAFE_NAME.match(s.name):
                continue
            files = [{"name": f.name, "size": f.stat().st_size}
                     for f in sorted(s.iterdir()) if f.is_file() and SAFE_NAME.match(f.name)]
            out.append({"session": s.name, "files": files, "active": s.name == active,
                        "complete": (s / "session.json").is_file()})
        return out

    def path(self, session: str, name: str) -> Path:
        return self.root / safe(session) / safe(name)

    def delete(self, session: str, name: str, active: str | None = None) -> bool:
        p = self.path(session, name)
        if not p.is_file():
            return False
        p.unlink()
        folder = p.parent
        if session != active and not any(folder.iterdir()):   # nothing left, not even a file being written
            try:
                folder.rmdir()
            except OSError:
                pass
        return True

    def usage(self) -> dict:
        files = [f for f in self.root.rglob("*") if f.is_file()]
        return {"files": len(files), "bytes": sum(f.stat().st_size for f in files),
                "free_bytes": shutil.disk_usage(self.root).free}

    def recover(self, active: str | None = None) -> list[str]:
        """Close sessions a crash or power loss left without session.json.

        The hidden, half-written segment is dropped (an mp4 without its index
        cannot be played); the visible segments are kept and described.
        """
        closed = []
        for s in sorted(self.root.iterdir()):
            if not s.is_dir() or s.name == active or (s / "session.json").exists():
                continue
            for f in s.iterdir():
                if f.name.startswith(".") and f.is_file():
                    f.unlink()
            segments = sorted(f.name for f in s.iterdir() if f.name.startswith("av_"))
            meta = {"session": s.name, "stop_reason": "interrupted", "started_utc": None, "ended_utc": None,
                    "video": None, "audio": None, "error": "the tablet agent stopped during this capture",
                    "segments": [{"file": n, "start_utc": None, "end_utc": None} for n in segments],
                    "touch": any(f.name.startswith("touch_") for f in s.iterdir()), "container": "mp4"}
            (s / ".session.json").write_text(json.dumps(meta, indent=2))
            (s / ".session.json").replace(s / "session.json")
            closed.append(s.name)
            log.warning("closed interrupted capture %s", s.name)
        return closed
