"""Record raw multitouch frames from evdev as JSON lines.

Each line is one SYN_REPORT frame:
  {"t": <unix time, kernel timestamp>, "touches": [{"id": 17, "x": 0.41, "y": 0.73}, ...]}
x/y are 0..1 screen coordinates (origin top-left). An empty list means all
fingers lifted. Files rotate every `segment_seconds` so they upload alongside
the video segments.
"""

import json
import logging
import select
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)


def find_device(name: str):
    import evdev
    for path in evdev.list_devices():
        dev = evdev.InputDevice(path)
        if dev.name == name:
            return dev
        dev.close()
    raise RuntimeError(f"touch device {name!r} not found")


class TouchRecorder:
    def __init__(self, cfg, session_dir: Path, on_file_done):
        self.cfg = cfg
        self.dir = session_dir
        self.on_file_done = on_file_done
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="touch", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=5)

    def _run(self) -> None:
        import evdev
        from evdev import ecodes as e

        dev = find_device(self.cfg.touch_device_name)
        xmax = dev.absinfo(e.ABS_MT_POSITION_X).max or 4095
        ymax = dev.absinfo(e.ABS_MT_POSITION_Y).max or 4095
        m = self.cfg.touch_matrix

        def to_screen(x, y):
            nx, ny = x / xmax, y / ymax
            return (min(max(m[0] * nx + m[1] * ny + m[2], 0.0), 1.0),
                    min(max(m[3] * nx + m[4] * ny + m[5], 0.0), 1.0))

        slots: dict[int, dict] = {}
        slot = 0
        index = 0
        out = None
        out_name = ""
        file_start = 0.0

        def rotate(now):
            # Write to a hidden file and rename when closed, so the uploader
            # never picks up a half-written file.
            nonlocal out, out_name, index, file_start
            if out:
                out.close()
                final = self.dir / out_name
                (self.dir / f".{out_name}").replace(final)
                self.on_file_done(final)
                out = None
            if now is None:
                return
            index += 1
            out_name = f"touch_{index:05d}.jsonl"
            out = (self.dir / f".{out_name}").open("w")
            file_start = now

        rotate(time.time())
        try:
            while not self.stop_event.is_set():
                now = time.time()
                if now - file_start >= self.cfg.segment_seconds:
                    rotate(now)
                r, _, _ = select.select([dev.fd], [], [], 0.2)
                if not r:
                    continue
                for ev in dev.read():
                    if ev.type == e.EV_ABS:
                        if ev.code == e.ABS_MT_SLOT:
                            slot = ev.value
                        elif ev.code == e.ABS_MT_TRACKING_ID:
                            if ev.value < 0:
                                slots.pop(slot, None)
                            else:
                                slots[slot] = {"id": ev.value, "rx": 0, "ry": 0}
                        elif ev.code == e.ABS_MT_POSITION_X and slot in slots:
                            slots[slot]["rx"] = ev.value
                        elif ev.code == e.ABS_MT_POSITION_Y and slot in slots:
                            slots[slot]["ry"] = ev.value
                    elif ev.type == e.EV_SYN and ev.code == e.SYN_REPORT:
                        touches = []
                        for s in slots.values():
                            x, y = to_screen(s["rx"], s["ry"])
                            touches.append({"id": s["id"], "x": round(x, 4), "y": round(y, 4)})
                        out.write(json.dumps({"t": round(ev.timestamp(), 6), "touches": touches}) + "\n")
        except Exception:
            log.exception("touch recorder failed")
        finally:
            rotate(None)
            dev.close()
