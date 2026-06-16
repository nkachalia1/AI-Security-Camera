from vision_appliance.system_health import _temperature_status


def test_temperature_status_bands():
    assert _temperature_status(None) == "unknown"
    assert _temperature_status(60) == "normal"
    assert _temperature_status(72) == "warm"
    assert _temperature_status(80) == "hot"
    assert _temperature_status(85) == "critical"

