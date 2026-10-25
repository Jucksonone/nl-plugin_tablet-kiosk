"""Volume, backlight and basic health readings."""

import re
import subprocess
import time
from pathlib import Path

BACKLIGHT_ROOT = Path("/sys/class/backlight")
POWER_ROOT = Path("/sys/class/power_supply")


def _run(*args: str) -> str:
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=5).stdout


def get_volume() -> float | None:
    try:
        out = _run("wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@")
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"Volume:\s*([\d.]+)", out)
    return float(m.group(1)) if m else None


def set_volume(value: float) -> float:
    value = min(max(float(value), 0.0), 1.0)
    _run("wpctl", "set-mute", "@DEFAULT_AUDIO_SINK@", "0")
    _run("wpctl", "set-volume", "@DEFAULT_AUDIO_SINK@", f"{value:.3f}")
    return value


def _backlight() -> Path | None:
    devices = sorted(BACKLIGHT_ROOT.glob("*")) if BACKLIGHT_ROOT.is_dir() else []
    # Prefer the firmware/raw interfaces that actually drive the panel.
    for preferred in ("intel_backlight",):
        for d in devices:
            if d.name == preferred:
                return d
    return devices[0] if devices else None


def get_brightness() -> float | None:
    d = _backlight()
    if not d:
        return None
    return int((d / "brightness").read_text()) / int((d / "max_brightness").read_text())


def set_brightness(value: float) -> float:
    d = _backlight()
    if not d:
        raise RuntimeError("no backlight device in /sys/class/backlight")
    value = min(max(float(value), 0.0), 1.0)
    raw = round(value * int((d / "max_brightness").read_text()))
    (d / "brightness").write_text(str(raw))
    return value


def display_available() -> bool:
    try:
        return subprocess.run(["xset", "q"], capture_output=True, timeout=5).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def x_input_devices() -> list[str]:
    """IDs of the physical X input devices (touch, mouse, keyboards, buttons)."""
    try:
        out = subprocess.run(["xinput", "list", "--short"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    ids = []
    for line in out.splitlines():
        m = re.search(r"id=(\d+)\s+\[(slave|floating slave)", line)
        if m and "XTEST" not in line:
            ids.append(m.group(1))
    return ids


def screen_power(on: bool, touch_device: str) -> None:
    """Really power the panel down (DPMS), not just dim the backlight.

    X wakes a DPMS-off screen on any input event, so while the panel is off every
    input device (touch, mouse, keyboard, buttons) is detached from X. Touch
    capture reads evdev directly and keeps working.
    """
    devices = x_input_devices() or [touch_device]
    if on:
        for dev in devices:
            subprocess.run(["xinput", "enable", dev], capture_output=True)
        subprocess.run(["xset", "dpms", "force", "on"], capture_output=True)
        subprocess.run(["xset", "-dpms"], capture_output=True)   # no idle blanking
    else:
        for dev in devices:
            subprocess.run(["xinput", "disable", dev], capture_output=True)
        subprocess.run(["xset", "+dpms", "dpms", "0", "0", "0"], capture_output=True)
        subprocess.run(["xset", "dpms", "force", "off"], capture_output=True)


def battery() -> dict:
    out = {}
    for d in POWER_ROOT.glob("*") if POWER_ROOT.is_dir() else []:
        kind = (d / "type").read_text().strip() if (d / "type").exists() else ""
        if kind == "Battery":
            for key in ("capacity", "status"):
                f = d / key
                if f.exists():
                    out[key] = f.read_text().strip()
        elif (d / "online").exists():
            out["ac_online"] = (d / "online").read_text().strip() == "1"
    return out


def soc_temperature() -> float | None:
    temps = []
    for z in Path("/sys/class/thermal").glob("thermal_zone*"):
        try:
            temps.append(int((z / "temp").read_text()) / 1000)
        except (OSError, ValueError):
            pass
    return max(temps) if temps else None


def memory() -> dict:
    """System memory from /proc/meminfo, in MB."""
    info = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            key, value = line.split(":", 1)
            info[key] = int(value.split()[0]) / 1024          # kB -> MB
    except (OSError, ValueError):
        return {}
    return {"total_mb": round(info.get("MemTotal", 0)),
            "available_mb": round(info.get("MemAvailable", 0)),
            "swap_used_mb": round(info.get("SwapTotal", 0) - info.get("SwapFree", 0))}


def process_rss_mb(pid: int | None) -> float | None:
    """Resident memory of one process, in MB."""
    if not pid:
        return None
    try:
        for line in Path(f"/proc/{pid}/status").read_text().splitlines():
            if line.startswith("VmRSS:"):
                return round(int(line.split()[1]) / 1024, 1)
    except (OSError, ValueError):
        pass
    return None


def load_average() -> list[float] | None:
    try:
        return [float(x) for x in Path("/proc/loadavg").read_text().split()[:3]]
    except (OSError, ValueError):
        return None


def uptime() -> float:
    try:
        return float(Path("/proc/uptime").read_text().split()[0])
    except OSError:
        return time.monotonic()
