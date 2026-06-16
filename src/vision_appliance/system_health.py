from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any


def get_system_health() -> dict[str, Any]:
    temperature_c = _temperature_c()
    throttled = _vcgencmd("get_throttled")
    return {
        "temperature_c": temperature_c,
        "temperature_status": _temperature_status(temperature_c),
        "throttled": throttled,
        "memory": _memory_info(),
    }


def _temperature_c() -> float | None:
    thermal_path = Path("/sys/class/thermal/thermal_zone0/temp")
    if thermal_path.exists():
        try:
            return round(int(thermal_path.read_text(encoding="utf-8").strip()) / 1000, 1)
        except (OSError, ValueError):
            pass

    measured = _vcgencmd("measure_temp")
    if measured and "temp=" in measured:
        try:
            return round(float(measured.split("temp=", 1)[1].split("'")[0]), 1)
        except (IndexError, ValueError):
            return None
    return None


def _temperature_status(temperature_c: float | None) -> str:
    if temperature_c is None:
        return "unknown"
    if temperature_c >= 85:
        return "critical"
    if temperature_c >= 78:
        return "hot"
    if temperature_c >= 70:
        return "warm"
    return "normal"


def _vcgencmd(argument: str) -> str | None:
    binary = shutil.which("vcgencmd")
    if not binary:
        return None
    try:
        result = subprocess.run(
            [binary, argument],
            check=False,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    return result.stdout.strip() or result.stderr.strip() or None


def _memory_info() -> dict[str, int] | None:
    meminfo = Path("/proc/meminfo")
    if not meminfo.exists():
        return None
    values: dict[str, int] = {}
    try:
        for line in meminfo.read_text(encoding="utf-8").splitlines():
            key, _, raw = line.partition(":")
            if key in {"MemTotal", "MemAvailable"}:
                values[key] = int(raw.strip().split()[0]) * 1024
    except (OSError, ValueError, IndexError):
        return None
    if "MemTotal" not in values or "MemAvailable" not in values:
        return None
    used = values["MemTotal"] - values["MemAvailable"]
    return {"total_bytes": values["MemTotal"], "available_bytes": values["MemAvailable"], "used_bytes": used}

