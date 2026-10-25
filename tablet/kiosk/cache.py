"""Content-addressed media cache: the plugin pushes files here before showing them.

A file is named ``<sha256>.<ext>``; a pushed file is kept only if its content
matches the name.
"""

import hashlib
import logging
import os
import re
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

MEDIA_NAME = re.compile(r"^[0-9a-f]{64}\.[a-z0-9]{1,8}$")


class MissingMedia(Exception):
    def __init__(self, names: list[str]):
        super().__init__(f"media not on the tablet: {', '.join(names)}")
        self.names = names


class MediaCache:
    def __init__(self, root: Path, max_bytes: int):
        self.root = root
        self.max_bytes = max_bytes
        self.lock = threading.Lock()

    def path_for(self, name: str) -> Path:
        if not MEDIA_NAME.match(name):
            raise ValueError(f"bad media name: {name}")
        return self.root / name

    def files(self) -> list[dict]:
        return [{"name": p.name, "size": p.stat().st_size}
                for p in sorted(self.root.iterdir()) if p.is_file() and MEDIA_NAME.match(p.name)]

    def ensure(self, names: list[str]) -> list[Path]:
        """Local paths in playlist order; raises MissingMedia if any is not here."""
        paths = [self.path_for(n) for n in names]
        missing = [p.name for p in paths if not p.is_file()]
        if missing:
            raise MissingMedia(missing)
        now = time.time()
        for p in paths:
            os.utime(p, (now, now))   # mtime doubles as "last used" for eviction
        return paths

    def put(self, name: str, stream, size: int, keep: set[str] = frozenset()) -> int:
        """Store ``size`` bytes from ``stream`` as ``name`` after checking the hash."""
        dest = self.path_for(name)
        part = dest.with_name(f".{name}.part")
        digest = hashlib.sha256()
        left = size
        with part.open("wb") as out:
            while left > 0:
                chunk = stream.read(min(left, 1 << 20))
                if not chunk:
                    break
                digest.update(chunk)
                out.write(chunk)
                left -= len(chunk)
        if left or digest.hexdigest() != name.split(".", 1)[0]:
            part.unlink(missing_ok=True)
            raise ValueError(f"content of {name} does not match its name" if not left else f"{name}: body ended early")
        with self.lock:
            part.replace(dest)
            self.evict(keep=set(keep) | {name})
        return size

    def delete(self, name: str) -> bool:
        p = self.path_for(name)
        if p.is_file():
            p.unlink()
            return True
        return False

    def usage(self) -> int:
        return sum(p.stat().st_size for p in self.root.iterdir() if p.is_file())

    def evict(self, keep: set[str]) -> None:
        files = sorted((p for p in self.root.iterdir() if p.is_file() and p.name not in keep),
                       key=lambda p: p.stat().st_mtime)
        total = self.usage()
        for p in files:
            if total <= self.max_bytes:
                break
            total -= p.stat().st_size
            log.info("evicting %s", p.name)
            p.unlink()
