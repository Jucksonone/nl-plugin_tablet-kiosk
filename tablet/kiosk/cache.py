"""Download playlist media into a local, content-addressed cache."""

import hashlib
import logging
import os
import threading
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)


class MediaCache:
    def __init__(self, root: Path, max_bytes: int, server_url: str = ""):
        self.root = root
        self.server_url = server_url.rstrip("/")   # base for relative manifest URLs
        self.max_bytes = max_bytes
        self.lock = threading.Lock()
        self.pending = 0

    def path_for(self, item: dict) -> Path:
        return self.root / item["name"]

    def is_ready(self, item: dict) -> bool:
        p = self.path_for(item)
        return p.is_file() and p.stat().st_size == item["size"]

    def ensure(self, items: list[dict]) -> list[Path]:
        """Download every missing item; returns local paths in playlist order.

        Raises on the first item that cannot be fetched or fails verification.
        """
        missing = [it for it in items if not self.is_ready(it)]
        with self.lock:
            self.pending = len(missing)
        for it in missing:
            self._download(it)
            with self.lock:
                self.pending -= 1
        paths = [self.path_for(it) for it in items]
        now = time.time()
        for p in paths:
            os.utime(p, (now, now))   # mtime doubles as "last used" for eviction
        self.evict(keep={p.name for p in paths})
        return paths

    def _download(self, item: dict) -> None:
        dest = self.path_for(item)
        part = dest.with_name(dest.name + ".part")
        offset = part.stat().st_size if part.exists() else 0
        url = item["url"]
        if url.startswith("/"):
            url = self.server_url + url
        req = urllib.request.Request(url)
        if offset:
            req.add_header("Range", f"bytes={offset}-")
        log.info("downloading %s (%d bytes, resume at %d)", item["name"], item["size"], offset)
        with urllib.request.urlopen(req, timeout=30) as resp:
            if offset and resp.status != 206:
                offset = 0          # server ignored the Range header; start over
            with part.open("ab" if offset else "wb") as out:
                while chunk := resp.read(1 << 20):
                    out.write(chunk)
        digest = hashlib.sha256()
        with part.open("rb") as f:
            while chunk := f.read(1 << 20):
                digest.update(chunk)
        if digest.hexdigest() != item["sha256"]:
            part.unlink()
            raise ValueError(f"sha256 mismatch for {item['name']}")
        part.replace(dest)

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
