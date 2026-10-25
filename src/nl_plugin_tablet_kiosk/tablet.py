"""HTTP client for the tablet agent (tablet/kiosk/server.py)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

CONNECT_TIMEOUT_S = 3.0
TIMEOUT_S = 10.0
COMMAND_TIMEOUT_S = 30.0   # capture_start waits for the camera pipeline
TRANSFER_TIMEOUT_S = 60.0  # between two chunks of a file


class TabletError(Exception):
    """The tablet answered but refused (``code`` from the agent) or could not be reached (``unreachable``)."""

    def __init__(self, code: str, message: str, data: dict | None = None):
        super().__init__(message)
        self.code, self.message, self.data = code, message, data or {}


class Tablet:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, headers={"Authorization": f"Bearer {token}"},
                                   timeout=httpx.Timeout(TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
                                   transport=transport, trust_env=False)

    def close(self) -> None:
        self.client.close()

    def _json(self, method: str, path: str, *, timeout: float | None = None, **kw) -> Any:
        try:
            r = self.client.request(method, path, timeout=timeout or httpx.USE_CLIENT_DEFAULT, **kw)
        except httpx.HTTPError as e:
            raise TabletError("unreachable", f"{type(e).__name__}: {e}") from e
        try:
            body = r.json()
        except ValueError:
            raise TabletError("bad_reply", f"HTTP {r.status_code}: not JSON") from None
        if r.status_code >= 400 or (isinstance(body, dict) and body.get("ok") is False):
            raise TabletError(body.get("code") or f"http_{r.status_code}", body.get("error") or f"HTTP {r.status_code}", body)
        return body

    def status(self) -> dict:
        return self._json("GET", "/status")

    def command(self, cmd: str, params: dict | None = None, *, timeout: float = COMMAND_TIMEOUT_S) -> Any:
        return self._json("POST", "/command", json={"cmd": cmd, "params": params or {}}, timeout=timeout)["result"]

    def events(self, after: int, limit: int = 500) -> dict:
        return self._json("GET", "/events", params={"after": after, "limit": limit})

    def media(self) -> dict:
        return self._json("GET", "/media")

    def put_media(self, name: str, path: Path) -> dict:
        size = path.stat().st_size

        def body():
            with path.open("rb") as f:
                while chunk := f.read(1 << 20):
                    yield chunk

        return self._json("PUT", f"/media/{name}", content=body(), headers={"Content-Length": str(size)},
                          timeout=TRANSFER_TIMEOUT_S)

    def delete_media(self, name: str) -> dict:
        return self._json("DELETE", f"/media/{name}")

    def spool(self) -> list[dict]:
        return self._json("GET", "/spool")["sessions"]

    def fetch(self, session: str, name: str, dest: Path, expected_size: int | None = None) -> int:
        """Download one spool file to ``dest`` (written to a hidden temp file first); returns its size."""
        tmp = dest.with_name(f".{dest.name}.part")
        try:
            with self.client.stream("GET", f"/spool/{session}/{name}", timeout=TRANSFER_TIMEOUT_S) as r:
                if r.status_code != 200:
                    r.read()
                    raise TabletError(f"http_{r.status_code}", r.text[:200])
                size = 0
                with tmp.open("wb") as out:
                    for chunk in r.iter_bytes(1 << 20):
                        out.write(chunk)
                        size += len(chunk)
                    out.flush()
                    os.fsync(out.fileno())
        except httpx.HTTPError as e:
            tmp.unlink(missing_ok=True)
            raise TabletError("unreachable", f"{type(e).__name__}: {e}") from e
        if expected_size is not None and size != expected_size:
            tmp.unlink(missing_ok=True)
            raise TabletError("short_read", f"{session}/{name}: got {size} of {expected_size} bytes")
        tmp.replace(dest)
        return size

    def delete_spool(self, session: str, name: str) -> dict:
        return self._json("DELETE", f"/spool/{session}/{name}")
