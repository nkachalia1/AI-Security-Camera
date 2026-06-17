from datetime import datetime, timedelta, timezone

from vision_appliance.config import Settings
from vision_appliance.event_generator import EventGenerator
from vision_appliance.models import Detection, MotionRegion, TrackedObject, Zone


def test_object_specific_descriptions_with_onnx_label():
    settings = Settings(zones=[Zone("workbench", 0.5, 0.4, 1.0, 1.0)])
    generator = EventGenerator(settings)
    now = datetime.now(timezone.utc)
    detection = Detection(
        label="backpack",
        confidence=0.82,
        bbox=(700, 420, 120, 160),
        source="onnx",
        track_id=7,
    )
    track = TrackedObject(
        track_id=7,
        label="backpack",
        bbox=detection.bbox,
        confidence=0.82,
        first_seen=now - timedelta(seconds=9),
        last_seen=now,
        stationary_since=now - timedelta(seconds=20),
        source="onnx",
        path=[(520, 430), (760, 500)],
    )

    events = generator.generate([detection], [track], [], now, (720, 1280, 3))
    summaries = [event.summary for event in events]

    assert "Backpack detected near workbench; small object, object-detector confidence 82%." in summaries
    assert "Backpack moved into workbench; steady movement rightward; object-detector confidence 82%." in summaries
    assert "Backpack remained unattended near workbench for 20 seconds; small object." in summaries


def test_generic_motion_does_not_emit_zone_events_by_default():
    settings = Settings(zones=[Zone("workbench", 0.5, 0.4, 1.0, 1.0)])
    generator = EventGenerator(settings)
    now = datetime.now(timezone.utc)
    detection = Detection(
        label="moving object",
        confidence=0.4,
        bbox=(700, 420, 120, 160),
        source="motion",
        track_id=2,
    )
    track = TrackedObject(
        track_id=2,
        label="moving object",
        bbox=detection.bbox,
        confidence=0.4,
        first_seen=now,
        last_seen=now,
        source="motion",
    )

    events = generator.generate([detection], [track], [], now, (720, 1280, 3))

    assert events == []


def test_large_motion_is_warning_severity():
    generator = EventGenerator(Settings())
    now = datetime.now(timezone.utc)
    motion_regions = [MotionRegion(bbox=(0, 0, 60, 60), area=3600)]

    events = generator.generate([], [], motion_regions, now, (100, 100, 3))

    assert len(events) == 1
    assert events[0].event_type == "large_motion"
    assert events[0].severity == "warning"
