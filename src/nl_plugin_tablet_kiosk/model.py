"""Documents the plugin saves in versions: the tablet connection and an experiment's plan."""

from __future__ import annotations

import re
import secrets
from typing import Literal

from pydantic import BaseModel, Field, field_validator

MEDIA_NAME = re.compile(r"^[0-9a-f]{64}\.[a-z0-9]{1,8}$")
SCREEN_MODES = ("content", "white", "black", "off")
VIDEO_EXT = {"mp4", "mkv", "webm", "mov", "avi", "m4v"}
IMAGE_EXT = {"jpg", "jpeg", "png", "webp", "bmp", "gif"}
AUDIO_EXT = {"mp3", "wav", "ogg", "flac", "m4a", "opus"}


def media_kind(ext: str) -> str | None:
    ext = ext.lower()
    return "video" if ext in VIDEO_EXT else "image" if ext in IMAGE_EXT else "audio" if ext in AUDIO_EXT else None


class SettingsDoc(BaseModel):
    """How to reach the tablet. ``enabled`` False: the plugin does not talk to it at all."""

    enabled: bool = False
    host: str = Field(default="", max_length=253)
    port: int = Field(default=8770, ge=1, le=65535)
    token: str = Field(default_factory=lambda: secrets.token_urlsafe(24), min_length=16, max_length=128)
    # Stop fetching capture files from the tablet when the host has less free space than this.
    min_free_gb: float = Field(default=20.0, ge=1, le=10000)

    @field_validator("host")
    @classmethod
    def _host(cls, v: str) -> str:
        v = v.strip()
        if v and not re.fullmatch(r"[A-Za-z0-9.\-]+", v):
            raise ValueError("host must be an IP address or a host name")
        return v

    @property
    def base_url(self) -> str | None:
        return f"http://{self.host}:{self.port}" if self.enabled and self.host else None


class ScreenDoc(BaseModel):
    mode: Literal["content", "white", "black", "off"] = "content"
    items: list[str] = Field(default_factory=list, max_length=200)   # media names, in playlist order
    loop: bool = True
    image_duration: float = Field(default=10.0, ge=0.5, le=3600)

    @field_validator("items")
    @classmethod
    def _items(cls, v: list[str]) -> list[str]:
        for name in v:
            if not MEDIA_NAME.match(name):
                raise ValueError(f"not a media name: {name}")
        return v

    def command(self) -> dict:
        return {"mode": self.mode, "items": list(self.items) if self.mode == "content" else [],
                "loop": self.loop, "image_duration": self.image_duration}


class CaptureDoc(BaseModel):
    """What one capture records and what the tablet shows meanwhile (None = leave as it is)."""

    video: bool = True
    audio: bool = True
    touch: bool = True
    screen: ScreenDoc | None = None
    volume: float | None = Field(default=None, ge=0, le=1)
    brightness: float | None = Field(default=None, ge=0, le=1)
    restore: bool = True          # put screen, volume and brightness back afterwards

    def media(self) -> list[str]:
        return list(self.screen.items) if self.screen and self.screen.mode == "content" else []

    def command(self, *, session_id: str, duration_s: float, lease_s: float, meta: dict) -> dict:
        if not (self.video or self.audio or self.touch):
            raise ValueError("nothing to record")
        return {"session_id": session_id, "duration_s": duration_s, "lease_s": lease_s,
                "video": self.video, "audio": self.audio, "touch": self.touch,
                "screen": self.screen.command() if self.screen else None,
                "volume": self.volume, "brightness": self.brightness, "restore": self.restore, "meta": meta}


class PlanDoc(CaptureDoc):
    """An experiment's plan: capture for the whole of each record when enabled."""

    enabled: bool = False


class ManualCapture(CaptureDoc):
    duration_s: float = Field(default=60, ge=1, le=6 * 3600)
