"""The plugin backend: routes for the UI, Agent tools, cleanup and the controller's lifecycle."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import re
import secrets
import shutil
import time
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Literal

from fastapi import Depends, Query, Request
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from natural_land_plugin_sdk import Caller, Plugin, PluginError, ToolError

from . import store
from .controller import SESSION_ID, Controller, Refused
from .model import MEDIA_NAME, ManualCapture, PlanDoc, SettingsDoc, media_kind

logger = logging.getLogger(__name__)

plugin = Plugin("tablet_kiosk", migrations=Path(__file__).parent / "migrations")
controller = Controller(plugin)

MAX_MEDIA_BYTES = 4 * 1024**3
UPLOAD_CHUNK_BYTES = 768 * 1024          # under the console proxy's 1 MiB body limit
SAFE_FILE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
EVENT_KEEP_DAYS = 180
SAMPLE_KEEP_DAYS = 31


@plugin.on_startup
def start_controller() -> None:
    controller.start()


@plugin.on_shutdown
def stop_controller() -> None:
    controller.stop()


def refused(e: Refused) -> PluginError:
    return PluginError(409, e.code, e.message)


def user_of(caller: Caller | None) -> str | None:
    return caller.user if caller else None


# ---------------------------------------------------------------- status and settings


@plugin.app.get("/status")
def status() -> dict:
    return controller.status()


@plugin.app.get("/settings")
def get_settings() -> dict:
    with plugin.connect() as conn:
        doc, version = store.settings(conn)
    return {"settings": doc.model_dump(), "version": version}


@plugin.app.put("/settings")
def put_settings(doc: SettingsDoc, caller: Caller | None = Depends(plugin.caller)) -> dict:
    with plugin.connect() as conn:
        version = store.save_settings(conn, doc, user_of(caller))
        store.add_event(conn, "settings_saved", actor=user_of(caller),
                        data={"enabled": doc.enabled, "host": doc.host, "port": doc.port, "version": version})
    controller.wake()
    return {"version": version}


@plugin.app.post("/settings/new-token")
def new_token() -> dict:
    """A fresh token for the settings form (saved only with the settings)."""
    return {"token": secrets.token_urlsafe(24)}


# ---------------------------------------------------------------- media library


def _media_dir() -> Path:
    return controller.media_dir


def _upload_dir() -> Path:
    p = controller.data_dir / "uploads"
    p.mkdir(parents=True, exist_ok=True)
    return p


@plugin.app.get("/media")
def list_media() -> dict:
    with plugin.connect() as conn:
        items = store.media_list(conn)
        rows = conn.execute("SELECT DISTINCT ON (experiment_id) experiment_id, doc FROM plan_versions "
                            "ORDER BY experiment_id, version_no DESC").fetchall()
    used: dict[str, list[int]] = {}
    for exp, doc in rows:
        for name in (doc.get("screen") or {}).get("items") or []:
            used.setdefault(name, []).append(exp)
    on_tablet = controller.tablet_media
    for m in items:
        m["on_tablet"] = None if on_tablet is None else m["name"] in on_tablet
        m["used_by_experiments"] = used.get(m["name"], [])
    return {"media": items, "bytes": sum(m["bytes"] for m in items)}


class UploadStart(BaseModel):
    filename: str = Field(min_length=1, max_length=255)
    size: int = Field(ge=1, le=MAX_MEDIA_BYTES)


@plugin.app.post("/media/uploads", status_code=201)
def start_upload(body: UploadStart) -> dict:
    ext = Path(body.filename).suffix.lower().lstrip(".")
    if not media_kind(ext):
        raise PluginError(400, "unsupported_type", f"files of type .{ext} cannot be shown on the tablet")
    if shutil.disk_usage(controller.data_dir.parent if not controller.data_dir.exists() else controller.data_dir).free < body.size * 2:
        raise PluginError(507, "no_space", "not enough free space on the host for this file")
    upload_id = secrets.token_hex(12)
    d = _upload_dir()
    (d / f"{upload_id}.json").write_text(json.dumps({"filename": Path(body.filename).name, "size": body.size, "ext": ext,
                                                     "started": time.time()}))
    (d / f"{upload_id}.part").write_bytes(b"")
    return {"upload_id": upload_id, "chunk_bytes": UPLOAD_CHUNK_BYTES}


def _upload(upload_id: str) -> tuple[dict, Path]:
    if not re.fullmatch(r"[0-9a-f]{24}", upload_id):
        raise PluginError(404, "upload_not_found", "no such upload")
    d = _upload_dir()
    try:
        meta = json.loads((d / f"{upload_id}.json").read_text())
    except (OSError, ValueError):
        raise PluginError(404, "upload_not_found", "no such upload (it may have expired)") from None
    return meta, d / f"{upload_id}.part"


@plugin.app.put("/media/uploads/{upload_id}")
async def upload_chunk(upload_id: str, request: Request, offset: int = Query(ge=0)) -> dict:
    meta, part = _upload(upload_id)
    have = part.stat().st_size
    if offset != have:
        raise PluginError(409, "wrong_offset", f"expected offset {have}")
    data = await request.body()
    if have + len(data) > meta["size"]:
        raise PluginError(400, "too_long", "more bytes than announced")
    with part.open("ab") as f:
        f.write(data)
    return {"received": have + len(data)}


@plugin.app.post("/media/uploads/{upload_id}/finish")
def finish_upload(upload_id: str, caller: Caller | None = Depends(plugin.caller)) -> dict:
    meta, part = _upload(upload_id)
    if part.stat().st_size != meta["size"]:
        raise PluginError(409, "incomplete", f"received {part.stat().st_size} of {meta['size']} bytes")
    digest = hashlib.sha256()
    with part.open("rb") as f:
        while chunk := f.read(1 << 20):
            digest.update(chunk)
    name = f"{digest.hexdigest()}.{meta['ext']}"
    dest = _media_dir() / name
    if dest.exists():
        part.unlink()
    else:
        part.replace(dest)
    (part.with_suffix(".json")).unlink(missing_ok=True)
    with plugin.connect() as conn:
        added = store.add_media(conn, name, meta["filename"], media_kind(meta["ext"]), meta["size"], user_of(caller))
        if added:
            store.add_event(conn, "media_added", actor=user_of(caller), data={"name": name, "original_name": meta["filename"],
                                                                              "bytes": meta["size"]})
    return {"name": name, "added": added}


@plugin.app.delete("/media/uploads/{upload_id}")
def cancel_upload(upload_id: str) -> dict:
    _meta, part = _upload(upload_id)
    part.unlink(missing_ok=True)
    part.with_suffix(".json").unlink(missing_ok=True)
    return {"cancelled": upload_id}


def _media_or_404(conn, name: str) -> dict:
    if not MEDIA_NAME.match(name):
        raise PluginError(404, "media_not_found", "no such media")
    for m in store.media_list(conn):
        if m["name"] == name:
            return m
    raise PluginError(404, "media_not_found", "no such media")


@plugin.app.get("/media/{name}/file")
def media_file(name: str) -> FileResponse:
    with plugin.connect() as conn:
        m = _media_or_404(conn, name)
    path = _media_dir() / name
    if not path.is_file():
        raise PluginError(404, "media_missing", "the file is missing on the host")
    return FileResponse(path, filename=m["original_name"], content_disposition_type="inline")


@plugin.app.post("/media/{name}/push")
def push_media(name: str) -> dict:
    with plugin.connect() as conn:
        _media_or_404(conn, name)
    controller.request_push([name])
    return {"queued": name}


@plugin.app.delete("/media/{name}")
def delete_media(name: str, caller: Caller | None = Depends(plugin.caller)) -> dict:
    with plugin.connect() as conn:
        m = _media_or_404(conn, name)
        users = store.plans_using_media(conn, name)
        if users:
            raise PluginError(409, "in_use", f"the current plan of experiment(s) {', '.join(map(str, users))} shows this media")
        conn.execute("DELETE FROM media WHERE name = %s", [name])
        store.add_event(conn, "media_deleted", actor=user_of(caller), data={"name": name, "original_name": m["original_name"]})
    (_media_dir() / name).unlink(missing_ok=True)
    if controller.online and controller.tablet is not None:
        try:
            controller.tablet.delete_media(name)
            if controller.tablet_media is not None:
                controller.tablet_media.discard(name)
        except Exception as e:  # noqa: BLE001 - on screen right now: the tablet's cache drops it later
            logger.info("tablet kept %s: %s", name, e)
    return {"deleted": name}


# ---------------------------------------------------------------- experiment plans


def _experiment_or_404(experiment_id: int) -> dict:
    exp = plugin.platform.experiment(experiment_id)
    if exp is None:
        raise PluginError(404, "experiment_not_found", "no such experiment")
    return exp


@plugin.app.get("/experiments/{experiment_id}/plan")
def get_plan(experiment_id: int) -> dict:
    _experiment_or_404(experiment_id)
    with plugin.connect() as conn:
        history = store.plan_history(conn, experiment_id)
    return {"current": history[0] if history else None, "history": history, "default": PlanDoc().model_dump()}


class PlanIn(BaseModel):
    doc: PlanDoc
    base_version: int | None = Field(default=None, ge=0)


@plugin.app.put("/experiments/{experiment_id}/plan")
def put_plan(experiment_id: int, body: PlanIn, caller: Caller | None = Depends(plugin.caller)) -> dict:
    _experiment_or_404(experiment_id)
    doc = body.doc
    if doc.enabled and not (doc.video or doc.audio or doc.touch):
        raise PluginError(400, "nothing_to_record", "choose at least one of video, audio and touch")
    with plugin.connect() as conn:
        known = store.media_names(conn)
        unknown = [n for n in doc.media() if n not in known]
        if unknown:
            raise PluginError(400, "unknown_media", f"not in the media library: {', '.join(unknown)}")
        try:
            saved = store.save_plan(conn, experiment_id, doc, user_of(caller), body.base_version)
        except store.VersionConflict as e:
            raise PluginError(409, "version_conflict", str(e)) from None
        store.add_event(conn, "plan_saved", actor=user_of(caller),
                        data={"experiment_id": experiment_id, "version_no": saved["version_no"], "enabled": doc.enabled})
    controller.request_push(doc.media() if doc.enabled else [])
    return saved


# ---------------------------------------------------------------- live control


class ControlIn(BaseModel):
    cmd: Literal["set_volume", "set_brightness", "set_screen", "player", "show"]
    params: dict[str, Any] = Field(default_factory=dict)


@plugin.app.post("/control")
def control(body: ControlIn, caller: Caller | None = Depends(plugin.caller)) -> dict:
    params = dict(body.params)
    if body.cmd == "show":
        from .model import ScreenDoc

        params = ScreenDoc.model_validate(params).command()
    with plugin.connect() as conn:
        try:
            result = controller.control(conn, body.cmd, params, user_of(caller))
        except Refused as e:
            raise refused(e) from None
    return {"result": result}


@plugin.app.post("/capture/start")
def capture_start(doc: ManualCapture, caller: Caller | None = Depends(plugin.caller)) -> dict:
    if not (doc.video or doc.audio or doc.touch):
        raise PluginError(400, "nothing_to_record", "choose at least one of video, audio and touch")
    with plugin.connect() as conn:
        unknown = [n for n in doc.media() if n not in store.media_names(conn)]
        if unknown:
            raise PluginError(400, "unknown_media", f"not in the media library: {', '.join(unknown)}")
        try:
            return controller.start_manual(conn, doc, user_of(caller))
        except Refused as e:
            raise refused(e) from None


@plugin.app.post("/capture/stop")
def capture_stop(caller: Caller | None = Depends(plugin.caller)) -> dict:
    with plugin.connect() as conn:
        try:
            return controller.stop_capture(conn, user_of(caller))
        except Refused as e:
            raise refused(e) from None


# ---------------------------------------------------------------- captures


def _capture_or_404(conn, session_id: str) -> dict:
    c = store.capture(conn, session_id) if SESSION_ID.match(session_id) else None
    if c is None:
        raise PluginError(404, "capture_not_found", "no such capture")
    return c


@plugin.app.get("/captures")
def list_captures(run_id: str | None = None, experiment_id: int | None = None,
                  kind: Literal["run", "manual", "other"] | None = None,
                  limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)) -> dict:
    with plugin.connect() as conn:
        rows, total = store.captures(conn, run_id=run_id, experiment_id=experiment_id, kind=kind, limit=limit, offset=offset)
    running = ((controller.report or {}).get("capture") or {}).get("session")
    for r in rows:
        r["capturing"] = r["session_id"] == running
    return {"captures": rows, "total": total}


@plugin.app.get("/captures/{session_id}")
def get_capture(session_id: str) -> dict:
    with plugin.connect() as conn:
        c = _capture_or_404(conn, session_id)
        c["media_names"] = store.media_names(conn)
    c["capturing"] = ((controller.report or {}).get("capture") or {}).get("session") == session_id
    return c


@plugin.app.delete("/captures/{session_id}")
def delete_capture(session_id: str, caller: Caller | None = Depends(plugin.caller)) -> dict:
    if caller is None:
        raise PluginError(403, "user_required", "deleting a capture needs a signed-in user")
    if ((controller.report or {}).get("capture") or {}).get("session") == session_id:
        raise PluginError(409, "capturing", "the tablet is still recording this capture")
    with plugin.connect() as conn:
        c = _capture_or_404(conn, session_id)
        conn.execute("DELETE FROM captures WHERE session_id = %s", [session_id])
        store.add_event(conn, "capture_deleted", actor=caller.user, session_id=session_id, run_id=c["run_id"],
                        data={"bytes": c["bytes"], "files": len(c["files"])})
    shutil.rmtree(controller.capture_dir(session_id), ignore_errors=True)
    return {"deleted": session_id}


@plugin.app.get("/captures/{session_id}/files/{name}")
def capture_file(session_id: str, name: str) -> FileResponse:
    if not SAFE_FILE.match(name) or not SESSION_ID.match(session_id):
        raise PluginError(404, "file_not_found", "no such file")
    path = controller.capture_dir(session_id) / name
    if not path.is_file():
        raise PluginError(404, "file_not_found", "no such file")
    media = {".mp4": "video/mp4", ".jsonl": "application/x-ndjson", ".json": "application/json"}.get(path.suffix, "application/octet-stream")
    return FileResponse(path, media_type=media, filename=name, content_disposition_type="inline")


EXPORT_README = """采集记录导出 · {session}

文件说明
  session.json        采集元数据：开始/结束时间、音视频分段、屏幕显示记录、音量亮度等
  av_NNNNN.mp4        摄像头（H.264）+ 音轨（Opus，48 kHz 单声道），每段约 10 秒：
                        音轨 1 回声消除后的麦克风（播放器默认播这一条）
                        音轨 2 播放参考：喇叭当时实际播放的声音
                        音轨 3 原始麦克风（未经回声消除和降噪）
                      离线去回声请用"音轨 3 + 音轨 2"。提取某条音轨，例如：
                        ffmpeg -i av_00000.mp4 -map 0:a:1 reference.wav
  touch_NNNNN.jsonl   原始触摸帧，每行 {{"t": 时间, "touches": [{{"id","x","y"}}]}}
  touch.csv           触摸数据展开成表格：每行一个手指在一个时刻的位置
  stimulus/           采集期间屏幕上显示过的素材原文件

时间对齐
  所有时间都是平板系统时钟的 Unix 时间（秒，UTC）。插件状态页显示平板时钟和主机的差。
  · 音视频：session.json 里每段有 start_utc，段内某一帧/采样的时间 = start_utc + 它在文件里的播放时间
  · 触摸：t 是内核记录触摸事件的时间
  · 屏幕显示：screen.log 记录每次切换（时间、模式、素材）；screen.playback 每秒记录一次
    "时间 -> 素材视频播放到第几秒"，用来确定循环视频在任一时刻显示的是哪一帧
  · touch.csv 的 t_rel 是相对 started_utc 的秒数
  坐标 x、y 是 0~1 的屏幕比例坐标，原点在左上角。
{record}"""


def touch_csv(folder: Path, started: float | None) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["t_unix", "t_rel", "frame", "contact_id", "x", "y"])
    frame = 0
    for f in sorted(folder.glob("touch_*.jsonl")):
        for line in f.read_text().splitlines():
            if not line.strip():
                continue
            ev = json.loads(line)
            rel = round(ev["t"] - started, 6) if started else ""
            if not ev["touches"]:                      # every finger lifted
                w.writerow([ev["t"], rel, frame, "", "", ""])
            for p in ev["touches"]:
                w.writerow([ev["t"], rel, frame, p["id"], p["x"], p["y"]])
            frame += 1
    return out.getvalue()


class _Sink(io.RawIOBase):
    """A write-only, unseekable buffer: zipfile writes into it, the response drains it."""

    def __init__(self) -> None:
        self.buf = bytearray()

    def writable(self) -> bool:
        return True

    def write(self, b) -> int:
        self.buf += b
        return len(b)

    def take(self) -> bytes:
        out, self.buf = bytes(self.buf), bytearray()
        return out


def stream_zip(entries: list[tuple[str, Path | bytes]]) -> Iterator[bytes]:
    sink = _Sink()
    with zipfile.ZipFile(sink, "w", allowZip64=True) as z:
        for arcname, src in entries:
            packed = arcname.endswith((".mp4", ".jpg", ".jpeg", ".png", ".webm", ".mkv", ".mov", ".webp", ".gif"))
            info = zipfile.ZipInfo(arcname, date_time=time.localtime()[:6])
            info.compress_type = zipfile.ZIP_STORED if packed else zipfile.ZIP_DEFLATED
            with z.open(info, "w", force_zip64=True) as w:
                if isinstance(src, bytes):
                    w.write(src)
                else:
                    with src.open("rb") as f:
                        while chunk := f.read(1 << 20):
                            w.write(chunk)
                            if len(sink.buf) >= 1 << 20:
                                yield sink.take()
            yield sink.take()
    yield sink.take()


@plugin.app.get("/captures/{session_id}/export")
def export_capture(session_id: str) -> StreamingResponse:
    with plugin.connect() as conn:
        c = _capture_or_404(conn, session_id)
        names = store.media_names(conn)
    folder = controller.capture_dir(session_id)
    meta = c.get("meta") or {}
    screen = meta.get("screen") or {}
    stimuli = set((screen.get("requested") or {}).get("items") or [])
    stimuli |= {e["file"] for e in screen.get("log") or [] if e.get("file")}
    root = session_id
    entries: list[tuple[str, Path | bytes]] = [(f"{root}/{f['name']}", folder / f["name"]) for f in c["files"]
                                               if (folder / f["name"]).is_file()]
    if any(folder.glob("touch_*.jsonl")):
        entries.append((f"{root}/touch.csv", touch_csv(folder, meta.get("started_utc")).encode()))
    for name in sorted(stimuli):
        src = controller.media_dir / name
        if MEDIA_NAME.match(name) and src.is_file():
            entries.append((f"{root}/stimulus/{Path(names.get(name) or name).name}", src))
    record = ""
    if c["run_id"]:
        record = f"\n平台记录\n  记录 {c['run_id']}（实验 {c['experiment_id']}），第 {c['part']} 段采集\n"
    entries.append((f"{root}/README.txt", EXPORT_README.format(session=session_id, record=record).encode()))
    return StreamingResponse(stream_zip(entries), media_type="application/zip",
                             headers={"Content-Disposition": f'attachment; filename="tablet_{session_id}.zip"'})


@plugin.app.get("/runs/{run_id}")
def run_detail(run_id: str) -> dict:
    with plugin.connect() as conn:
        binding = store.run_binding(conn, run_id)
        rows, _ = store.captures(conn, run_id=run_id, limit=500)
        evs = store.events(conn, run_id=run_id, limit=500)
    return {"run_id": run_id, "binding": binding, "captures": rows, "events": evs}


# ---------------------------------------------------------------- events and samples


@plugin.app.get("/events")
def list_events(limit: int = Query(200, ge=1, le=2000), before_id: int | None = None, run_id: str | None = None,
                session_id: str | None = None, names: list[str] | None = Query(None)) -> dict:
    with plugin.connect() as conn:
        return {"events": store.events(conn, run_id=run_id, session_id=session_id, names=names, limit=limit, before_id=before_id)}


@plugin.app.get("/samples")
def list_samples(hours: float = Query(6, gt=0, le=24 * 31), points: int = Query(600, ge=10, le=5000)) -> dict:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    with plugin.connect() as conn:
        return {"samples": store.samples(conn, since, points)}


# ---------------------------------------------------------------- Agent tools (read-only)


@plugin.agent_tool(
    "tablet_status",
    label={"zh-CN": "查看平板状态", "en": "Checking the tablet"},
    description="The tablet kiosk now: online or not, what its screen shows, volume, brightness, battery, temperature, "
                "the capture running (if any) and whether it follows the record being recorded. Use for questions about "
                "the tablet right now.",
    parameters={"type": "object", "properties": {}, "additionalProperties": False},
    examples=({},),
)
def tool_status(ctx) -> dict:
    s = controller.status()
    rep = s.get("tablet") or {}
    return {"configured": s["configured"], "online": s["online"], "error": s["error"],
            "seconds_since_report": s["report_age_s"], "clock_offset_s": s["clock_offset_s"],
            "screen": (rep.get("player") or {}), "volume": rep.get("volume"), "brightness": rep.get("brightness"),
            "battery": rep.get("battery"), "temperature_c": rep.get("temperature"), "capture": rep.get("capture"),
            "record_capture_wanted": s["want"], "waiting_for_media": s["waiting_media"],
            "files_waiting_on_tablet": (rep.get("spool") or {}).get("files"), "host_low_disk": s["low_disk"]}


def _brief(c: dict) -> dict:
    s = c.get("summary") or {}
    return {"session_id": c["session_id"], "kind": c["kind"], "run_id": c["run_id"], "part": c["part"],
            "started_at": c["started_at"], "ended_at": c["ended_at"], "stop_reason": c["stop_reason"],
            "complete": c["complete"], "duration_s": s.get("duration_s"), "segments": s.get("segments"),
            "touch_frames": s.get("touch_frames"), "touch_frames_down": s.get("touch_frames_down"),
            "bytes": c.get("bytes"), "error": s.get("error")}


@plugin.agent_tool(
    "tablet_captures_of_run",
    label={"zh-CN": "读取记录的平板采集", "en": "Reading the tablet captures of a record"},
    description="The tablet's captures (camera video, microphone tracks, touches) made during one record, with times, "
                "why each ended, how many touch frames and whether every file reached the host (complete). "
                "Also the plan the record used.",
    parameters={"type": "object", "properties": {"run_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                "required": ["run_id"], "additionalProperties": False},
    examples=({"run_id": "$run_id"},),
)
def tool_captures_of_run(ctx, run_id: str) -> dict:
    ctx.require_run(run_id)
    with plugin.connect() as conn:
        binding = store.run_binding(conn, run_id)
        rows, _ = store.captures(conn, run_id=run_id, limit=100)
    if not rows and not binding:
        raise ToolError("no_data", "the tablet plugin has nothing for this record (no plan was enabled, or it never saw it)")
    plan = (binding or {}).get("plan")
    return {"plan": {"version_no": plan.get("version_no"), "enabled": plan.get("enabled"),
                     "screen": plan.get("screen"), "video": plan.get("video"), "audio": plan.get("audio"),
                     "touch": plan.get("touch"), "inferred": binding.get("inferred")} if plan else None,
            "captures": [_brief(c) for c in rows]}


@plugin.agent_tool(
    "tablet_capture_detail",
    label={"zh-CN": "读取一次平板采集", "en": "Reading one tablet capture"},
    description="One tablet capture in detail: its files, what the screen showed and when (screen log, with the "
                "media's original file names), volume and brightness, and the touch counts. Times are Unix seconds "
                "of the tablet's clock.",
    parameters={"type": "object", "properties": {"session_id": {"type": "string", "minLength": 1, "maxLength": 128}},
                "required": ["session_id"], "additionalProperties": False},
    examples=({"session_id": "nl-verify-run.p1"},),
)
def tool_capture_detail(ctx, session_id: str) -> dict:
    with plugin.connect() as conn:
        c = store.capture(conn, session_id) if SESSION_ID.match(session_id) else None
        names = store.media_names(conn)
    if c is None:
        raise ToolError("no_data", "no capture with this session id")
    if c["run_id"]:
        ctx.require_run(c["run_id"])
    elif c.get("experiment_id"):
        ctx.require_experiment(c["experiment_id"])
    meta = c.get("meta") or {}
    screen = meta.get("screen") or {}
    log = [{**e, "file_name": names.get(e.get("file") or "", e.get("file"))} for e in (screen.get("log") or [])[:500]]
    return {**_brief(c), "settings": meta.get("settings"), "screen_log": log,
            "requested_screen": screen.get("requested"), "files": [{"name": f["name"], "bytes": f["bytes"]} for f in c["files"][:400]],
            "audio_tracks": (meta.get("audio") or {}).get("tracks") if isinstance(meta.get("audio"), dict) else None,
            "video": meta.get("video")}


@plugin.agent_tool(
    "tablet_plan_of_experiment",
    label={"zh-CN": "读取实验的平板方案", "en": "Reading an experiment's tablet plan"},
    description="What the tablet does during each record of an experiment (current plan version): whether it records, "
                "what it shows, volume and brightness. Media are given with their original file names.",
    parameters={"type": "object", "properties": {"experiment_id": {"type": "integer", "minimum": 1}},
                "required": ["experiment_id"], "additionalProperties": False},
    examples=({"experiment_id": "$experiment_id"},),
)
def tool_plan(ctx, experiment_id: int) -> dict:
    ctx.require_experiment(experiment_id)
    with plugin.connect() as conn:
        p = store.plan(conn, experiment_id)
        names = store.media_names(conn)
    if p is None:
        raise ToolError("no_plan", "no tablet plan was saved for this experiment: the tablet does nothing during its records")
    doc = p["doc"]
    if doc.get("screen"):
        doc["screen"]["item_names"] = [names.get(n, n) for n in doc["screen"].get("items") or []]
    return {"version_no": p["version_no"], "saved_at": p["created_at"], "saved_by": p["created_by"], "plan": doc}


@plugin.agent_tool(
    "tablet_events",
    label={"zh-CN": "读取平板事件", "en": "Reading tablet events"},
    description="The tablet's event log, newest first: captures started / finished / stored, screen and sound changes, "
                "online / offline, failures. Give run_id for one record's events.",
    parameters={"type": "object", "properties": {"run_id": {"type": "string", "minLength": 1, "maxLength": 128},
                                                 "limit": {"type": "integer", "minimum": 1, "maximum": 300}},
                "additionalProperties": False},
    examples=({"run_id": "$run_id", "limit": 50}, {"limit": 20}),
)
def tool_events(ctx, run_id: str | None = None, limit: int = 100) -> dict:
    if run_id:
        ctx.require_run(run_id)
    with plugin.connect() as conn:
        rows = store.events(conn, run_id=run_id, limit=limit)
    if not rows:
        raise ToolError("no_data", "no tablet events" + (" for this record" if run_id else ""))
    return {"events": [{k: r[k] for k in ("at", "source", "name", "session_id", "run_id", "actor", "data")} for r in rows]}


# ---------------------------------------------------------------- cleanup


@plugin.periodic(3600, first_delay=120)
def forget_deleted() -> None:
    """AGENTS.md §4.4: drop data of experiments and records the platform deleted (only when sure)."""
    with plugin.connect() as conn:
        exps = [r[0] for r in conn.execute("SELECT DISTINCT experiment_id FROM plan_versions").fetchall()]
        runs = [r[0] for r in conn.execute("SELECT run_id FROM runs UNION SELECT DISTINCT run_id FROM captures WHERE run_id IS NOT NULL").fetchall()]
    gone_exps = set(exps) - plugin.platform.existing_experiments(exps) if exps else set()
    gone_runs = set(runs) - plugin.platform.existing_runs(runs) if runs else set()
    sessions: list[str] = []
    with plugin.connect() as conn:
        if gone_runs:
            sessions = [r[0] for r in conn.execute("SELECT session_id FROM captures WHERE run_id = ANY(%s)", [sorted(gone_runs)]).fetchall()]
            conn.execute("DELETE FROM captures WHERE run_id = ANY(%s)", [sorted(gone_runs)])
            conn.execute("DELETE FROM runs WHERE run_id = ANY(%s)", [sorted(gone_runs)])
            conn.execute("DELETE FROM events WHERE run_id = ANY(%s)", [sorted(gone_runs)])
        if gone_exps:
            conn.execute("UPDATE runs SET plan_version_id = NULL WHERE plan_version_id IN "
                         "(SELECT id FROM plan_versions WHERE experiment_id = ANY(%s))", [sorted(gone_exps)])
            conn.execute("DELETE FROM plan_versions WHERE experiment_id = ANY(%s)", [sorted(gone_exps)])
        conn.execute("DELETE FROM events WHERE at < now() - make_interval(days => %s) AND run_id IS NULL AND "
                     "(session_id IS NULL OR session_id NOT IN (SELECT session_id FROM captures))", [EVENT_KEEP_DAYS])
        conn.execute("DELETE FROM samples WHERE at < now() - make_interval(days => %s)", [SAMPLE_KEEP_DAYS])
    for sid in sessions:
        shutil.rmtree(controller.capture_dir(sid), ignore_errors=True)
    if gone_runs or gone_exps:
        logger.info("forgot %d deleted records (%d captures) and %d deleted experiments", len(gone_runs), len(sessions), len(gone_exps))
    # unfinished uploads older than a day
    for meta in _upload_dir().glob("*.json"):
        try:
            if time.time() - json.loads(meta.read_text())["started"] > 86400:
                meta.with_suffix(".part").unlink(missing_ok=True)
                meta.unlink(missing_ok=True)
        except (OSError, ValueError, KeyError):
            pass


def main() -> None:
    plugin.main()
