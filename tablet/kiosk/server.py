"""The HTTP interface the Natural Land plugin uses (the plugin connects to the tablet).

Every request needs ``Authorization: Bearer <token>``.

  GET    /status                       health, player, capture (with lease), spool, events cursor
  POST   /command                      {"cmd", "params"} -> {"ok", "result" | "error"}
  GET    /events?after=N&limit=M       {"instance", "last_seq", "events": [...]}
  GET    /media                        {"files": [{name, size}], "bytes", "max_bytes"}
  PUT    /media/<sha256>.<ext>         store a file (Content-Length; checked against the name)
  DELETE /media/<name>
  GET    /spool                        finished capture files waiting for the plugin
  GET    /spool/<session>/<file>       one file
  DELETE /spool/<session>/<file>       the plugin stored it
"""

import hmac
import json
import logging
import shutil
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .cache import MissingMedia

log = logging.getLogger(__name__)

MAX_JSON_BYTES = 1 << 20


class Handler(BaseHTTPRequestHandler):
    agent = None              # set by serve()
    protocol_version = "HTTP/1.1"
    server_version = "kiosk-agent"

    def log_message(self, fmt, *args):
        log.debug("%s %s", self.address_string(), fmt % args)

    # ------------------------------------------------------------ helpers

    def _send(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _error(self, status: int, code: str, message: str) -> None:
        self._send(status, {"ok": False, "code": code, "error": message})

    def _authorised(self) -> bool:
        expected = f"Bearer {self.agent.cfg.token}".encode()
        got = self.headers.get("Authorization", "").encode()
        if hmac.compare_digest(got, expected):
            return True
        self._drain()
        self._error(401, "bad_token", "missing or wrong token")
        return False

    def _drain(self) -> None:
        n = int(self.headers.get("Content-Length") or 0)
        while n > 0:
            chunk = self.rfile.read(min(n, 1 << 20))
            if not chunk:
                break
            n -= len(chunk)

    def _json_body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > MAX_JSON_BYTES:
            raise ValueError("request body too large")
        return json.loads(self.rfile.read(n) or b"{}")

    def _route(self) -> tuple[list[str], dict]:
        url = urllib.parse.urlsplit(self.path)
        parts = [urllib.parse.unquote(p) for p in url.path.split("/") if p]
        query = {k: v[-1] for k, v in urllib.parse.parse_qs(url.query).items()}
        return parts, query

    def _guard(self, fn) -> None:
        try:
            fn()
        except FileNotFoundError as e:
            self._error(404, "not_found", str(e))
        except (ValueError, KeyError) as e:
            self._error(400, "bad_request", str(e))
        except Exception as e:      # noqa: BLE001 - report, keep serving
            log.exception("request %s %s failed", self.command, self.path)
            self._error(500, "agent_error", f"{type(e).__name__}: {e}")

    # ------------------------------------------------------------ methods

    def do_GET(self):
        if self._authorised():
            self._guard(self._get)

    def do_POST(self):
        if self._authorised():
            self._guard(self._post)

    def do_PUT(self):
        if self._authorised():
            self._guard(self._put)

    def do_DELETE(self):
        if self._authorised():
            self._guard(self._delete)

    def _get(self) -> None:
        a = self.agent
        parts, query = self._route()
        if parts == ["status"]:
            self._send(200, a.status())
        elif parts == ["events"]:
            after = int(query.get("after", 0))
            limit = min(max(int(query.get("limit", 500)), 1), 2000)
            self._send(200, {"instance": a.events.instance, "last_seq": a.events.seq,
                             "events": a.events.after(after, limit)})
        elif parts == ["media"]:
            self._send(200, {"files": a.cache.files(), "bytes": a.cache.usage(), "max_bytes": a.cache.max_bytes})
        elif parts == ["spool"]:
            self._send(200, {"sessions": a.spool.sessions(active=a.active_session())})
        elif len(parts) == 3 and parts[0] == "spool":
            path = a.spool.path(parts[1], parts[2])
            if not path.is_file():
                raise FileNotFoundError(f"{parts[1]}/{parts[2]}")
            size = path.stat().st_size
            self.send_response(200)
            self.send_header("Content-Type", "application/octet-stream")
            self.send_header("Content-Length", str(size))
            self.end_headers()
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile, 1 << 20)
        else:
            self._error(404, "not_found", self.path)

    def _post(self) -> None:
        parts, _ = self._route()
        if parts != ["command"]:
            self._drain()
            return self._error(404, "not_found", self.path)
        body = self._json_body()
        cmd, params = body.get("cmd"), body.get("params") or {}
        try:
            result = self.agent.handle(cmd, params)
        except MissingMedia as e:
            return self._send(200, {"ok": False, "code": "missing_media", "error": str(e), "missing": e.names})
        except Exception as e:      # noqa: BLE001 - the plugin gets the reason
            log.warning("command %s failed: %s", cmd, e)
            return self._send(200, {"ok": False, "code": "command_failed", "error": f"{type(e).__name__}: {e}"})
        if cmd not in ("ping", "get_status", "capture_renew"):
            log.info("command %s done", cmd)
        self._send(200, {"ok": True, "result": result})

    def _put(self) -> None:
        parts, _ = self._route()
        if len(parts) != 2 or parts[0] != "media":
            self._drain()
            return self._error(404, "not_found", self.path)
        size = int(self.headers.get("Content-Length") or -1)
        if size < 0:
            return self._error(411, "length_required", "Content-Length is required")
        a = self.agent
        try:
            a.cache.put(parts[1], self.rfile, size, keep=a.media_in_use())
        except ValueError:
            self.close_connection = True      # the body may be partly unread
            raise
        a.events.append("media_stored", media=parts[1], size=size)
        self._send(200, {"ok": True, "name": parts[1], "size": size})

    def _delete(self) -> None:
        parts, _ = self._route()
        a = self.agent
        if len(parts) == 2 and parts[0] == "media":
            if parts[1] in a.media_in_use():
                return self._error(409, "in_use", "the media is on screen or in the playlist")
            self._send(200, {"ok": True, "deleted": a.cache.delete(parts[1])})
        elif len(parts) == 3 and parts[0] == "spool":
            self._send(200, {"ok": True, "deleted": a.spool.delete(parts[1], parts[2], active=a.active_session())})
        else:
            self._error(404, "not_found", self.path)


def serve(agent, host: str, port: int) -> ThreadingHTTPServer:
    handler = type("BoundHandler", (Handler,), {"agent": agent})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="http", daemon=True).start()
    log.info("listening on %s:%d", host, port)
    return httpd
