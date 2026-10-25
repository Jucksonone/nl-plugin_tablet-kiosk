import os
import socket
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_PATH = Path(os.environ.get("KIOSK_CONFIG", Path.home() / ".config/kiosk/config.toml"))
STATE_DIR = Path.home() / ".local/share/kiosk"


@dataclass
class Config:
    server_url: str = "http://127.0.0.1:8000"
    token: str = ""
    device_id: str = field(default_factory=socket.gethostname)
    status_interval: float = 15.0

    media_dir: Path = STATE_DIR / "media"
    spool_dir: Path = STATE_DIR / "spool"
    cache_max_bytes: int = 20 * 1024**3
    keep_uploaded: bool = False

    # player
    mpv_audio_device: str = "pipewire/echo-cancel-sink"
    mpv_extra_args: list = field(default_factory=list)

    # capture
    video_device: str = "/dev/video0"
    video_input: int = 0
    width: int = 640
    height: int = 480
    video_bitrate_kbps: int = 1000
    audio_source: str = "echo-cancel-source"
    audio_bitrate: int = 32000
    # Extra audio tracks for offline echo removal / analysis (PulseAudio device names):
    # what the speaker played, and the microphone before echo cancellation.
    record_reference: bool = True
    reference_device: str = "echo-cancel-sink.monitor"
    record_raw_mic: bool = True
    raw_mic_device: str = "alsa_input.platform-bytcr_rt5651.HiFi__hw_rt5651__source"
    segment_seconds: int = 10
    ae_target: float = 100.0       # mean raw luma, 0-255
    ae_deadband: float = 10.0
    exposure_min: int = 4
    exposure_max: int = 1286
    gain_min: int = 16
    gain_max: int = 250

    # touch: raw evdev axes -> 0..1 screen coords, same matrix as the libinput rule
    touch_device_name: str = "silead_ts"
    touch_matrix: list = field(default_factory=lambda: [2.068, 0, 0, 0, -2.767, 1])


def load() -> Config:
    cfg = Config()
    if CONFIG_PATH.is_file():
        with CONFIG_PATH.open("rb") as f:
            data = tomllib.load(f)
        for key, value in data.items():
            if not hasattr(cfg, key):
                raise ValueError(f"unknown config key: {key}")
            if isinstance(getattr(cfg, key), Path):
                value = Path(value).expanduser()
            setattr(cfg, key, value)
    cfg.server_url = cfg.server_url.rstrip("/")
    cfg.media_dir.mkdir(parents=True, exist_ok=True)
    cfg.spool_dir.mkdir(parents=True, exist_ok=True)
    return cfg
