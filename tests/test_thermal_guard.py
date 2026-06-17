from vision_appliance.config import Settings
from vision_appliance.thermal_guard import ThermalGuard


def _guard_for_temp(temp):
    settings = Settings(fps=24, detection_interval=5)
    return ThermalGuard(settings, health_reader=lambda: {"temperature_c": temp})


def test_thermal_guard_hot_mode_reduces_workload():
    action = _guard_for_temp(80).evaluate()

    assert action.mode == "hot"
    assert action.active is True
    assert action.effective_fps == 8
    assert action.effective_detection_interval == 15
    assert action.detection_paused is False


def test_thermal_guard_critical_mode_pauses_detection():
    action = _guard_for_temp(86).evaluate()

    assert action.mode == "critical"
    assert action.effective_fps == 3
    assert action.effective_detection_interval == 60
    assert action.detection_paused is True


def test_thermal_guard_never_raises_user_configured_fps():
    settings = Settings(fps=6, detection_interval=20)
    guard = ThermalGuard(settings, health_reader=lambda: {"temperature_c": 80})

    action = guard.evaluate()

    assert action.effective_fps == 6
    assert action.effective_detection_interval == 20


def test_thermal_guard_reuses_cached_health_sample():
    calls = []
    settings = Settings(thermal_sample_seconds=60)
    guard = ThermalGuard(settings, health_reader=lambda: calls.append(1) or {"temperature_c": 72})

    first = guard.evaluate()
    second = guard.health()

    assert first.mode == "warm"
    assert second["temperature_c"] == 72
    assert second["sample_interval_seconds"] == 60
    assert len(calls) == 1
