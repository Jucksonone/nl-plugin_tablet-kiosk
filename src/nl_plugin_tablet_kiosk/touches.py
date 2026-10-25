"""Touches of a capture: the raw multitouch frames (touch_*.jsonl) joined into contacts.

A *contact* is one finger from the frame it appears in to the first frame
without it (the kernel's tracking id stays the same while the finger is down).
Frames come every ~25 ms while a finger is down, so one tap is several frames
but one contact, and a quick tap lasts about one frame interval.
"""

from __future__ import annotations

import json
from pathlib import Path

MAX_CONTACTS = 5000
MAX_PATH_POINTS = 40


def frames(folder: Path):
    """Every frame of the capture in time order: (t, [(id, x, y), ...])."""
    for f in sorted(folder.glob("touch_*.jsonl")):
        with f.open() as fh:
            for line in fh:
                if not line.strip():
                    continue
                try:
                    ev = json.loads(line)
                    yield float(ev["t"]), [(int(p["id"]), float(p["x"]), float(p["y"])) for p in ev.get("touches") or []]
                except (ValueError, KeyError, TypeError):
                    continue


def _thin(path: list[list[float]], n: int) -> list[list[float]]:
    if len(path) <= n:
        return path
    step = (len(path) - 1) / (n - 1)
    return [path[round(i * step)] for i in range(n)]


def contacts(folder: Path, started: float | None = None) -> dict:
    """Contacts with times relative to ``started`` (the capture start; else the first frame)."""
    open_: dict[int, dict] = {}
    done: list[dict] = []
    n_frames = n_down = max_fingers = 0
    t0 = started
    last_t = None
    for t, touches in frames(folder):
        if t0 is None:
            t0 = t
        rel = round(t - t0, 3)
        n_frames += 1
        n_down += bool(touches)
        max_fingers = max(max_fingers, len(touches))
        last_t = rel
        present = set()
        for tid, x, y in touches:
            present.add(tid)
            c = open_.get(tid)
            if c is None:
                c = open_[tid] = {"id": tid, "t0": rel, "t1": rel, "path": [], "fingers": len(touches)}
            c["t1"] = rel
            c["fingers"] = max(c["fingers"], len(touches))
            c["path"].append([rel, round(x, 4), round(y, 4)])
        for tid in [k for k in open_ if k not in present]:
            c = open_.pop(tid)
            c["t1"] = rel                # lifted: the first frame without it
            done.append(c)
    done.extend(open_.values())          # still down when the files end
    done.sort(key=lambda c: (c["t0"], c["id"]))
    out = []
    for i, c in enumerate(done[:MAX_CONTACTS]):
        p = c["path"]
        dist = sum(((b[1] - a[1]) ** 2 + (b[2] - a[2]) ** 2) ** 0.5 for a, b in zip(p, p[1:]))
        out.append({"i": i + 1, "id": c["id"], "t0": c["t0"], "t1": c["t1"], "duration": round(c["t1"] - c["t0"], 3),
                    "frames": len(p), "x0": p[0][1], "y0": p[0][2], "x1": p[-1][1], "y1": p[-1][2],
                    "distance": round(dist, 4), "fingers": c["fingers"], "path": _thin(p, MAX_PATH_POINTS)})
    return {"started": t0, "last": last_t, "frames": n_frames, "frames_down": n_down, "max_fingers": max_fingers,
            "count": len(done), "truncated": len(done) > MAX_CONTACTS, "contacts": out}


def stats(folder: Path, started: float | None = None) -> dict:
    """The figures kept in a capture's summary."""
    c = contacts(folder, started)
    return {"touches": c["count"], "touch_seconds": round(sum(x["duration"] for x in c["contacts"]), 2)}
