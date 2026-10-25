"""Camera + microphone capture with a simple auto-exposure / auto-white-balance loop.

One GStreamer pipeline, clocked by the system realtime clock so segment start
times line up with touch timestamps:

  v4l2src (atomisp) ─ tee ─ small RGB copy ─ appsink ──► AE/AWB controller thread
                           └ GL white-balance shader ─ vaapih264enc ─┐
  pipewiresrc (echo-cancelled mic) ─ opusenc ────────────────────────┤ splitmuxsink (.mp4, N s)
  pulsesrc (speaker monitor = what was played) ─ opusenc ────────────┤   audio track 2
  pulsesrc (raw mic, before echo cancellation) ─ opusenc ────────────┘   audio track 3

The controller sets sensor exposure/gain over the V4L2 subdev and updates the
shader's per-channel gains (grey-world AWB).
"""

import json
import logging
import queue
import subprocess
import threading
import time
from pathlib import Path

log = logging.getLogger(__name__)

WB_SHADER = """
#ifdef GL_ES
precision mediump float;
#endif
varying vec2 v_texcoord;
uniform sampler2D tex;
uniform float rg;
uniform float gg;
uniform float bg;
void main () {
  vec4 c = texture2D(tex, v_texcoord);
  gl_FragColor = vec4(clamp(c.r * rg, 0.0, 1.0), clamp(c.g * gg, 0.0, 1.0),
                      clamp(c.b * bg, 0.0, 1.0), 1.0);
}
"""

STATS_W, STATS_H = 80, 60
STATS_EVERY_N_FRAMES = 15        # ~2 Hz at 30 fps

_gst = None


def gst():
    global _gst
    if _gst is None:
        import gi
        gi.require_version("Gst", "1.0")
        from gi.repository import Gst
        Gst.init(None)
        _gst = Gst
    return _gst


def find_sensor_subdev() -> str:
    for dev in sorted(Path("/dev").glob("v4l-subdev*")):
        out = subprocess.run(["v4l2-ctl", "-d", str(dev), "-l"],
                             capture_output=True, text=True).stdout
        if "exposure" in out and "analogue_gain" in out:
            return str(dev)
    raise RuntimeError("no V4L2 subdev with exposure/analogue_gain controls")


class ExposureControl:
    """Proportional AE on the sensor plus grey-world AWB gains for the shader."""

    def __init__(self, cfg, subdev: str, set_wb):
        self.cfg = cfg
        self.subdev = subdev
        self.set_wb = set_wb
        self.exposure, self.gain = self._read()
        self.wb = [1.0, 1.0, 1.0]
        self.settle = 0
        self.samples: queue.Queue[bytes] = queue.Queue(maxsize=1)
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, name="ae-awb", daemon=True)
        self.last = {}

    def _read(self) -> tuple[int, int]:
        out = subprocess.run(["v4l2-ctl", "-d", self.subdev, "-C", "exposure,analogue_gain"],
                             capture_output=True, text=True).stdout
        vals = dict(line.split(":", 1) for line in out.splitlines() if ":" in line)
        return int(vals.get("exposure", 600)), int(vals.get("analogue_gain", 128))

    def _write(self) -> None:
        subprocess.run(["v4l2-ctl", "-d", self.subdev, "-c",
                        f"exposure={self.exposure},analogue_gain={self.gain}"],
                       capture_output=True)

    def offer(self, rgb: bytes) -> None:
        try:
            self.samples.put_nowait(rgb)
        except queue.Full:
            pass

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stop_event.set()
        self.thread.join(timeout=2)

    def _run(self) -> None:
        while not self.stop_event.is_set():
            try:
                rgb = self.samples.get(timeout=0.5)
            except queue.Empty:
                continue
            n = len(rgb) // 3
            r, g, b = sum(rgb[0::3]) / n, sum(rgb[1::3]) / n, sum(rgb[2::3]) / n
            self._awb(r, g, b)
            self._ae(0.299 * r + 0.587 * g + 0.114 * b)
            self.last = {"luma": round(0.299 * r + 0.587 * g + 0.114 * b, 1),
                         "exposure": self.exposure, "gain": self.gain,
                         "wb": [round(x, 3) for x in self.wb]}

    def _awb(self, r, g, b) -> None:
        if min(r, g, b) < 4:      # too dark to judge colour
            return
        target = [min(max(g / r, 0.5), 3.0), 1.0, min(max(g / b, 0.5), 3.0)]
        self.wb = [0.7 * old + 0.3 * new for old, new in zip(self.wb, target)]
        self.set_wb(*self.wb)

    def _ae(self, luma: float) -> None:
        if self.settle > 0:          # sensor applies new values a few frames late
            self.settle -= 1
            return
        if abs(luma - self.cfg.ae_target) <= self.cfg.ae_deadband:
            return
        ratio = min(max(self.cfg.ae_target / max(luma, 1.0), 0.6), 1.6)
        total = self.exposure * self.gain * ratio
        # Prefer longer exposure (less noise); only raise gain once exposure is maxed.
        exposure = int(min(max(total / self.cfg.gain_min, self.cfg.exposure_min), self.cfg.exposure_max))
        gain = int(min(max(total / exposure, self.cfg.gain_min), self.cfg.gain_max))
        if (exposure, gain) != (self.exposure, self.gain):
            self.exposure, self.gain = exposure, gain
            self._write()
            self.settle = 2


class CaptureSession:
    def __init__(self, cfg, session_id: str, video: bool, audio: bool,
                 on_file_done, emit):
        if not (video or audio):
            raise ValueError("need at least one of video/audio")
        self.cfg = cfg
        self.id = session_id
        self.video = video
        self.audio = audio
        self.on_file_done = on_file_done
        self.emit = emit
        self.dir = cfg.spool_dir / session_id
        self.dir.mkdir(parents=True, exist_ok=True)
        self.segments: list[dict] = []
        self.started_utc: float | None = None
        self.ended_utc: float | None = None
        self.pipeline = None
        self.ae: ExposureControl | None = None
        self.frames = 0
        self.bus_thread = threading.Thread(target=self._bus_loop, name="capture-bus", daemon=True)
        self.done = threading.Event()
        self.error: str | None = None

    # ------------------------------------------------------------ pipeline

    def _describe(self) -> str:
        c = self.cfg
        # MP4 (H.264 + Opus) plays directly in browsers for the console's replay view.
        parts = [f"splitmuxsink name=mux muxer-factory=mp4mux "
                 f"max-size-time={c.segment_seconds * 1_000_000_000} "
                 f"location={self.dir}/.av_%05d.mp4"]
        if self.video:
            parts.append(
                f"v4l2src device={c.video_device} ! "
                f"video/x-raw,format=YV12,width={c.width},height={c.height} ! tee name=t "
                f"t. ! queue max-size-buffers=2 leaky=downstream ! videoscale ! "
                f"video/x-raw,width={STATS_W},height={STATS_H} ! videoconvert ! video/x-raw,format=RGB ! "
                f"appsink name=stats emit-signals=true max-buffers=1 drop=true sync=false "
                f"t. ! queue max-size-buffers=4 ! glupload ! glcolorconvert ! glshader name=wb ! "
                f"gldownload ! videoconvert ! video/x-raw,format=NV12 ! "
                f"vaapih264enc bitrate={c.video_bitrate_kbps} keyframe-period=30 ! h264parse ! "
                f"queue ! mux.video")
        if self.audio:
            mono = "audioconvert ! audioresample ! audio/x-raw,rate=48000,channels=1"
            parts.append(
                f"pipewiresrc target-object={c.audio_source} do-timestamp=true ! "
                f"{mono} ! opusenc bitrate={c.audio_bitrate} ! queue ! mux.audio_0")
            # pulsesrc, not pipewiresrc: it reliably records a sink monitor and
            # keeps delivering silence while nothing plays, so the muxer never stalls.
            for i, device in enumerate(self.extra_audio_devices(), start=1):
                parts.append(
                    f"pulsesrc device={device} do-timestamp=true ! "
                    f"{mono} ! opusenc bitrate={c.audio_bitrate} ! queue ! mux.audio_{i}")
        return " ".join(parts)

    def extra_audio_devices(self) -> list[str]:
        c = self.cfg
        return ([c.reference_device] if c.record_reference else []) + \
               ([c.raw_mic_device] if c.record_raw_mic else [])

    def start(self) -> None:
        Gst = gst()
        if self.video:
            subprocess.run(["v4l2-ctl", "-d", self.cfg.video_device, "-i", str(self.cfg.video_input)],
                           capture_output=True)
        self.pipeline = Gst.parse_launch(self._describe())
        clock = Gst.SystemClock.obtain()
        clock.set_property("clock-type", Gst.ClockType.REALTIME)
        self.pipeline.use_clock(clock)

        if self.video:
            wb = self.pipeline.get_by_name("wb")
            wb.set_property("fragment", WB_SHADER)
            self._set_wb_uniforms(wb, 1.0, 1.0, 1.0)
            self.ae = ExposureControl(self.cfg, find_sensor_subdev(),
                                      lambda r, g, b: self._set_wb_uniforms(wb, r, g, b))
            self.pipeline.get_by_name("stats").connect("new-sample", self._on_stats_sample)
            self.ae.start()

        self.bus_thread.start()
        if self.pipeline.set_state(Gst.State.PLAYING) == Gst.StateChangeReturn.FAILURE:
            self._teardown()
            raise RuntimeError("capture pipeline failed to start")
        self.started_utc = time.time()

    @staticmethod
    def _set_wb_uniforms(element, r: float, g: float, b: float) -> None:
        Gst = gst()
        element.set_property("uniforms", Gst.Structure.new_from_string(
            f"uniforms, rg=(float){r:.4f}, gg=(float){g:.4f}, bg=(float){b:.4f}"))

    def _on_stats_sample(self, sink):
        Gst = gst()
        sample = sink.emit("pull-sample")
        self.frames += 1
        if self.ae and self.frames % STATS_EVERY_N_FRAMES == 0:
            buf = sample.get_buffer()
            ok, info = buf.map(Gst.MapFlags.READ)
            if ok:
                try:
                    self.ae.offer(bytes(info.data))
                finally:
                    buf.unmap(info)
        return Gst.FlowReturn.OK

    def stop(self, timeout: float = 15.0) -> None:
        """Send EOS so the last segment is finalised, then wait for it."""
        Gst = gst()
        if self.pipeline and not self.done.is_set():
            self.pipeline.send_event(Gst.Event.new_eos())
            self.done.wait(timeout)
        self._teardown()

    def _teardown(self) -> None:
        Gst = gst()
        if self.ae:
            self.ae.stop()
        if self.pipeline:
            self.pipeline.set_state(Gst.State.NULL)
        self.done.set()
        if self.ended_utc is None:
            self.ended_utc = time.time()

    # ------------------------------------------------------------ bus

    def _bus_loop(self) -> None:
        Gst = gst()
        bus = self.pipeline.get_bus()
        mask = Gst.MessageType.ERROR | Gst.MessageType.EOS | Gst.MessageType.ELEMENT
        while not self.done.is_set():
            msg = bus.timed_pop_filtered(200 * Gst.MSECOND, mask)
            if msg is None:
                continue
            if msg.type == Gst.MessageType.ERROR:
                err, debug = msg.parse_error()
                self.error = f"{err.message} ({debug})"
                log.error("capture error: %s", self.error)
                self.emit("capture_error", session=self.id, error=self.error)
                self.done.set()
            elif msg.type == Gst.MessageType.EOS:
                self.done.set()
            elif msg.type == Gst.MessageType.ELEMENT:
                s = msg.get_structure()
                name = s.get_name() if s else ""
                if name == "splitmuxsink-fragment-opened":
                    rt = s.get_value("running-time")
                    self.segments.append({
                        "hidden": Path(s.get_value("location")).name,
                        "start_utc": (self.pipeline.get_base_time() + rt) / 1e9,
                    })
                elif name == "splitmuxsink-fragment-closed":
                    hidden = Path(s.get_value("location"))
                    final = hidden.with_name(hidden.name.lstrip("."))
                    hidden.replace(final)
                    for seg in self.segments:
                        if seg["hidden"] == hidden.name:
                            seg["file"] = final.name
                            seg["end_utc"] = (self.pipeline.get_base_time()
                                              + s.get_value("running-time")) / 1e9
                    self.on_file_done(final)

    # ------------------------------------------------------------ metadata

    def metadata(self) -> dict:
        return {
            "started_utc": self.started_utc,
            "ended_utc": self.ended_utc,
            "container": "mp4",
            "video": self.video and {"width": self.cfg.width, "height": self.cfg.height,
                                     "codec": "h264"},
            "audio": self.audio and {
                "codec": "opus", "rate": 48000, "channels": 1, "source": self.cfg.audio_source,
                "tracks": ["mic_echo_cancelled"]
                          + (["playback_reference"] if self.cfg.record_reference else [])
                          + (["mic_raw"] if self.cfg.record_raw_mic else [])},
            "segments": [{k: v for k, v in s.items() if k != "hidden"} for s in self.segments],
            "error": self.error,
        }


def write_session_json(session_dir: Path, meta: dict) -> Path:
    session_dir.mkdir(parents=True, exist_ok=True)
    path = session_dir / "session.json"
    tmp = session_dir / ".session.json"
    tmp.write_text(json.dumps(meta, indent=2))
    tmp.replace(path)
    return path
