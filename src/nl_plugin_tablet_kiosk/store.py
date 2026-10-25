"""SQL on this plugin's schema. Every function takes an open connection."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .model import PlanDoc, SettingsDoc


def _iso(v: datetime | None) -> str | None:
    return v.isoformat() if v else None


def utc(ts: float | None) -> datetime | None:
    return datetime.fromtimestamp(ts, timezone.utc) if ts is not None else None


# ---------------------------------------------------------------- settings


def settings(conn) -> tuple[SettingsDoc, int]:
    """The current settings; the first call saves defaults so the generated token stays the same."""
    row = conn.execute("SELECT id, doc FROM settings_versions ORDER BY id DESC LIMIT 1").fetchone()
    if row:
        return SettingsDoc.model_validate(row[1]), row[0]
    doc = SettingsDoc()
    return doc, save_settings(conn, doc, None)


def save_settings(conn, doc: SettingsDoc, by: str | None) -> int:
    return conn.execute("INSERT INTO settings_versions (doc, created_by) VALUES (%s, %s) RETURNING id",
                        [json.dumps(doc.model_dump()), by]).fetchone()[0]


# ---------------------------------------------------------------- plans


def plan(conn, experiment_id: int, before: datetime | None = None) -> dict | None:
    sql = "SELECT id, version_no, doc, created_by, created_at FROM plan_versions WHERE experiment_id = %s"
    params: list[Any] = [experiment_id]
    if before is not None:
        sql += " AND created_at <= %s"
        params.append(before)
    row = conn.execute(sql + " ORDER BY version_no DESC LIMIT 1", params).fetchone()
    return _plan_row(row) if row else None


def _plan_row(row) -> dict:
    return {"id": row[0], "version_no": row[1], "doc": PlanDoc.model_validate(row[2]).model_dump(),
            "created_by": row[3], "created_at": _iso(row[4])}


def plan_history(conn, experiment_id: int, limit: int = 50) -> list[dict]:
    rows = conn.execute("SELECT id, version_no, doc, created_by, created_at FROM plan_versions WHERE experiment_id = %s "
                        "ORDER BY version_no DESC LIMIT %s", [experiment_id, limit]).fetchall()
    return [_plan_row(r) for r in rows]


def save_plan(conn, experiment_id: int, doc: PlanDoc, by: str | None, base_version: int | None) -> dict:
    # Two saves at once: the second one hits UNIQUE (experiment_id, version_no) and fails.
    current = conn.execute("SELECT max(version_no) FROM plan_versions WHERE experiment_id = %s", [experiment_id]).fetchone()[0]
    if base_version is not None and (current or 0) != base_version:
        raise VersionConflict(current or 0)
    row = conn.execute("INSERT INTO plan_versions (experiment_id, version_no, doc, created_by) VALUES (%s, %s, %s, %s) "
                       "RETURNING id, version_no", [experiment_id, (current or 0) + 1, json.dumps(doc.model_dump()), by]).fetchone()
    return {"id": row[0], "version_no": row[1]}


class VersionConflict(Exception):
    def __init__(self, current: int):
        super().__init__(f"the plan was changed meanwhile (now version {current})")
        self.current = current


def plans_using_media(conn, name: str) -> list[int]:
    """Experiments whose newest plan shows this media."""
    rows = conn.execute(
        "SELECT DISTINCT ON (experiment_id) experiment_id, doc FROM plan_versions ORDER BY experiment_id, version_no DESC").fetchall()
    return [r[0] for r in rows if name in ((r[1].get("screen") or {}).get("items") or [])]


def media_of_enabled_plans(conn) -> set[str]:
    rows = conn.execute(
        "SELECT DISTINCT ON (experiment_id) doc FROM plan_versions ORDER BY experiment_id, version_no DESC").fetchall()
    out: set[str] = set()
    for (doc,) in rows:
        if doc.get("enabled") and doc.get("screen") and doc["screen"].get("mode") == "content":
            out |= set(doc["screen"].get("items") or [])
    return out


# ---------------------------------------------------------------- runs


def run_binding(conn, run_id: str) -> dict | None:
    row = conn.execute("SELECT run_id, experiment_id, plan_version_id, plan, inferred, seen_at FROM runs WHERE run_id = %s",
                       [run_id]).fetchone()
    if not row:
        return None
    return {"run_id": row[0], "experiment_id": row[1], "plan_version_id": row[2], "plan": row[3],
            "inferred": row[4], "seen_at": _iso(row[5])}


def bind_run(conn, run: dict, now: datetime, inferred_after_s: float = 10.0) -> dict:
    """Remember which plan a record uses; the first sight wins."""
    existing = run_binding(conn, run["run_id"])
    if existing:
        return existing
    started = run.get("started_at")
    inferred = bool(started and (now - started).total_seconds() > inferred_after_s)
    p = plan(conn, run["experiment_id"], before=started if inferred else None) if run.get("experiment_id") else None
    conn.execute("INSERT INTO runs (run_id, experiment_id, plan_version_id, plan, inferred) VALUES (%s, %s, %s, %s, %s) "
                 "ON CONFLICT (run_id) DO NOTHING",
                 [run["run_id"], run.get("experiment_id"), p["id"] if p else None,
                  json.dumps({**p["doc"], "version_no": p["version_no"]}) if p else None, inferred])
    return run_binding(conn, run["run_id"])


# ---------------------------------------------------------------- media


def media_list(conn) -> list[dict]:
    rows = conn.execute("SELECT name, original_name, kind, bytes, uploaded_by, uploaded_at FROM media ORDER BY uploaded_at DESC").fetchall()
    return [{"name": r[0], "original_name": r[1], "kind": r[2], "bytes": r[3], "uploaded_by": r[4], "uploaded_at": _iso(r[5])}
            for r in rows]


def media_names(conn) -> dict[str, str]:
    return {r[0]: r[1] for r in conn.execute("SELECT name, original_name FROM media").fetchall()}


def add_media(conn, name: str, original: str, kind: str, size: int, by: str | None) -> bool:
    row = conn.execute("INSERT INTO media (name, original_name, kind, bytes, uploaded_by) VALUES (%s, %s, %s, %s, %s) "
                       "ON CONFLICT (name) DO NOTHING RETURNING name", [name, original, kind, size, by]).fetchone()
    return row is not None


# ---------------------------------------------------------------- captures


CAPTURE_COLS = ("session_id, kind, run_id, experiment_id, part, plan_version_id, request, requested_by, requested_at, "
                "started_at, ended_at, stop_reason, complete, summary")


def _capture(row) -> dict:
    keys = [c.strip() for c in CAPTURE_COLS.split(",")]
    d = dict(zip(keys, row))
    for k in ("requested_at", "started_at", "ended_at"):
        d[k] = _iso(d[k])
    return d


def add_capture(conn, session_id: str, kind: str, *, run_id: str | None = None, experiment_id: int | None = None,
                part: int = 1, plan_version_id: int | None = None, request: dict | None = None, by: str | None = None) -> None:
    conn.execute("INSERT INTO captures (session_id, kind, run_id, experiment_id, part, plan_version_id, request, requested_by) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT (session_id) DO NOTHING",
                 [session_id, kind, run_id, experiment_id, part, plan_version_id, json.dumps(request or {}), by])


def capture(conn, session_id: str) -> dict | None:
    row = conn.execute(f"SELECT {CAPTURE_COLS}, meta FROM captures WHERE session_id = %s", [session_id]).fetchone()
    if not row:
        return None
    d = _capture(row[:-1])
    d["meta"] = row[-1]
    d["files"] = capture_files(conn, session_id)
    d["bytes"] = sum(f["bytes"] for f in d["files"])
    return d


def capture_files(conn, session_id: str) -> list[dict]:
    rows = conn.execute("SELECT name, bytes, stored_at, info FROM capture_files WHERE session_id = %s ORDER BY name",
                        [session_id]).fetchall()
    return [{"name": r[0], "bytes": r[1], "stored_at": _iso(r[2]), "info": r[3]} for r in rows]


def captures(conn, *, run_id: str | None = None, experiment_id: int | None = None, kind: str | None = None,
             limit: int = 100, offset: int = 0) -> tuple[list[dict], int]:
    where, params = [], []
    if run_id is not None:
        where.append("c.run_id = %s")
        params.append(run_id)
    if experiment_id is not None:
        where.append("c.experiment_id = %s")
        params.append(experiment_id)
    if kind is not None:
        where.append("c.kind = %s")
        params.append(kind)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    total = conn.execute(f"SELECT count(*) FROM captures c {clause}", params).fetchone()[0]
    cols = ", ".join(f"c.{c.strip()}" for c in CAPTURE_COLS.split(","))
    rows = conn.execute(
        f"SELECT {cols}, coalesce(sum(f.bytes), 0), count(f.name) FROM captures c "
        f"LEFT JOIN capture_files f ON f.session_id = c.session_id {clause} "
        f"GROUP BY c.session_id ORDER BY c.requested_at DESC LIMIT %s OFFSET %s", params + [limit, offset]).fetchall()
    out = []
    for r in rows:
        d = _capture(r[:-2])
        d["bytes"], d["file_count"] = int(r[-2]), int(r[-1])
        out.append(d)
    return out, total


def run_parts(conn, run_id: str) -> int:
    return conn.execute("SELECT coalesce(max(part), 0) FROM captures WHERE run_id = %s", [run_id]).fetchone()[0]


def stored_files(conn, session_id: str) -> dict[str, int]:
    return {r[0]: r[1] for r in conn.execute("SELECT name, bytes FROM capture_files WHERE session_id = %s", [session_id]).fetchall()}


def add_file(conn, session_id: str, name: str, size: int, info: dict | None = None) -> None:
    conn.execute("INSERT INTO capture_files (session_id, name, bytes, info) VALUES (%s, %s, %s, %s) "
                 "ON CONFLICT (session_id, name) DO UPDATE SET bytes = excluded.bytes, info = excluded.info, stored_at = now()",
                 [session_id, name, size, json.dumps(info or {})])


def finish_capture(conn, session_id: str, meta: dict, summary: dict) -> None:
    conn.execute("UPDATE captures SET started_at = coalesce(%s, started_at), ended_at = coalesce(%s, ended_at), "
                 "stop_reason = %s, meta = %s, summary = %s, complete = true WHERE session_id = %s",
                 [utc(meta.get("started_utc")), utc(meta.get("ended_utc")), meta.get("stop_reason"),
                  json.dumps(meta), json.dumps(summary), session_id])


def mark_started(conn, session_id: str, started: float | None) -> None:
    conn.execute("UPDATE captures SET started_at = coalesce(started_at, %s) WHERE session_id = %s", [utc(started), session_id])


# ---------------------------------------------------------------- events and samples


def add_event(conn, name: str, *, source: str = "plugin", at: datetime | None = None, session_id: str | None = None,
              run_id: str | None = None, actor: str | None = None, data: dict | None = None,
              tablet_instance: str | None = None, tablet_seq: int | None = None) -> None:
    conn.execute("INSERT INTO events (at, source, name, session_id, run_id, actor, data, tablet_instance, tablet_seq) "
                 "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                 [at or datetime.now(timezone.utc), source, name, session_id, run_id, actor, json.dumps(data or {}),
                  tablet_instance, tablet_seq])


def tablet_cursor(conn, instance: str) -> int:
    return conn.execute("SELECT coalesce(max(tablet_seq), 0) FROM events WHERE tablet_instance = %s", [instance]).fetchone()[0]


def events(conn, *, run_id: str | None = None, session_id: str | None = None, names: list[str] | None = None,
           limit: int = 200, before_id: int | None = None) -> list[dict]:
    where, params = [], []
    if run_id is not None:
        where.append("(run_id = %s OR session_id IN (SELECT session_id FROM captures WHERE run_id = %s))")
        params += [run_id, run_id]
    if session_id is not None:
        where.append("session_id = %s")
        params.append(session_id)
    if names:
        where.append("name = ANY(%s)")
        params.append(names)
    if before_id is not None:
        where.append("id < %s")
        params.append(before_id)
    clause = ("WHERE " + " AND ".join(where)) if where else ""
    rows = conn.execute(f"SELECT id, at, source, name, session_id, run_id, actor, data FROM events {clause} "
                        f"ORDER BY at DESC, id DESC LIMIT %s", params + [limit]).fetchall()
    return [{"id": r[0], "at": _iso(r[1]), "source": r[2], "name": r[3], "session_id": r[4], "run_id": r[5],
             "actor": r[6], "data": r[7]} for r in rows]


def add_sample(conn, at: datetime, data: dict) -> None:
    conn.execute("INSERT INTO samples (at, data) VALUES (%s, %s)", [at, json.dumps(data)])


def samples(conn, since: datetime, points: int) -> list[dict]:
    rows = conn.execute("SELECT at, data FROM samples WHERE at >= %s ORDER BY at", [since]).fetchall()
    step = max(1, len(rows) // max(points, 1))
    return [{"t": r[0].timestamp(), **r[1]} for r in rows[::step]]
