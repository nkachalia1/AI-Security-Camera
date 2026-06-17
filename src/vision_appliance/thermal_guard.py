from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable

from .config import Settings
from .system_health import get_system_health


@dataclass(frozen=True)
class ThermalAction:
    mode: str
    active: bool
    reason: str
    temperature_c: float | None
    base_fps: int
    effective_fps: int
    base_detection_interval: int
    effective_detection_interval: int
    detection_paused: bool
    sampled_at: str | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "active": self.active,
            "reason": self.reason,
            "temperature_c": self.temperature_c,
            "base_fps": self.base_fps,
            "effective_fps": self.effective_fps,
            "base_detection_interval": self.base_detection_interval,
            "effective_detection_interval": self.effective_detection_interval,
            "detection_paused": self.detection_paused,
            "sampled_at": self.sampled_at,
        }


class ThermalGuard:
    def __init__(
        self,
        settings: Settings,
        health_reader: Callable[[], dict[str, Any]] = get_system_health,
    ):
        self.settings = settings
        self.health_reader = health_reader
        self._last_sample_at = 0.0
        self._last_sample_iso: str | None = None
        self._last_health: dict[str, Any] = {}

    def evaluate(self) -> ThermalAction:
        health = self._health()
        temperature = health.get("temperature_c")
        if not isinstance(temperature, (int, float)):
            temperature = None

        if not self.settings.thermal_guard_enabled:
            return self._action("disabled", False, "thermal guard disabled", temperature)
        if temperature is None:
            return self._action("unknown", False, "temperature unavailable", None)
        if temperature >= self.settings.thermal_critical_c:
            return self._action(
                "critical",
                True,
                "critical temperature; detection paused until the Pi cools",
                float(temperature),
                fps=self.settings.thermal_critical_fps,
                interval=self.settings.thermal_critical_detection_interval,
                paused=True,
            )
        if temperature >= self.settings.thermal_hot_c:
            return self._action(
                "hot",
                True,
                "hot temperature; lowering FPS and running YOLO less often",
                float(temperature),
                fps=self.settings.thermal_hot_fps,
                interval=self.settings.thermal_hot_detection_interval,
            )
        if temperature >= self.settings.thermal_warm_c:
            return self._action(
                "warm",
                True,
                "warm temperature; gently reducing workload",
                float(temperature),
                fps=self.settings.thermal_warm_fps,
                interval=self.settings.thermal_warm_detection_interval,
            )
        return self._action("normal", False, "temperature in normal operating range", float(temperature))

    def health(self) -> dict[str, Any]:
        health = dict(self._health())
        health["sampled_at"] = self._last_sample_iso
        health["sample_interval_seconds"] = self.settings.thermal_sample_seconds
        return health

    def _action(
        self,
        mode: str,
        active: bool,
        reason: str,
        temperature: float | None,
        fps: int | None = None,
        interval: int | None = None,
        paused: bool = False,
    ) -> ThermalAction:
        base_fps = max(1, int(self.settings.fps))
        base_interval = max(1, int(self.settings.detection_interval))
        effective_fps = min(base_fps, max(1, int(fps or base_fps)))
        effective_interval = max(base_interval, int(interval or base_interval))
        return ThermalAction(
            mode=mode,
            active=active,
            reason=reason,
            temperature_c=round(temperature, 1) if temperature is not None else None,
            base_fps=base_fps,
            effective_fps=effective_fps,
            base_detection_interval=base_interval,
            effective_detection_interval=effective_interval,
            detection_paused=paused,
            sampled_at=self._last_sample_iso,
        )

    def _health(self) -> dict[str, Any]:
        now = time.monotonic()
        if self._last_health and now - self._last_sample_at < self.settings.thermal_sample_seconds:
            return self._last_health
        self._last_health = self.health_reader()
        self._last_sample_at = now
        self._last_sample_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
        return self._last_health
