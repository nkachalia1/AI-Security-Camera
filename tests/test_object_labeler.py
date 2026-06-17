from datetime import datetime, timezone

from vision_appliance.database import EventStore
from vision_appliance.models import Detection, TrackedObject
from vision_appliance.object_labeler import ObjectLabelRegistry
from vision_appliance.object_tracker import CentroidTracker


def _track(label="backpack"):
    now = datetime.now(timezone.utc)
    return TrackedObject(
        track_id=1,
        label=label,
        detector_label="backpack",
        bbox=(100, 120, 80, 90),
        confidence=0.86,
        first_seen=now,
        last_seen=now,
        source="onnx",
    )


def test_operator_label_is_persisted_and_applied_to_matching_detection(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.initialize()
    registry = ObjectLabelRegistry(store)

    profile = registry.learn_from_track(_track(), (480, 640, 3), "work backpack")
    detection = Detection(label="backpack", confidence=0.82, bbox=(105, 124, 78, 88), source="onnx")

    labeled = registry.apply_to_detection(detection, (480, 640, 3))

    assert profile["name"] == "work backpack"
    assert labeled.label == "work backpack"
    assert labeled.detector_label == "backpack"
    assert labeled.custom_label == "work backpack"
    assert labeled.metadata["label_profile_id"] == profile["id"]


def test_operator_label_does_not_apply_to_far_detection(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.initialize()
    registry = ObjectLabelRegistry(store)
    registry.learn_from_track(_track(), (480, 640, 3), "work backpack")

    detection = Detection(label="backpack", confidence=0.82, bbox=(500, 340, 80, 90), source="onnx")

    labeled = registry.apply_to_detection(detection, (480, 640, 3))

    assert labeled.label == "backpack"
    assert labeled.detector_label == "backpack"
    assert labeled.custom_label is None


def test_operator_label_is_applied_to_existing_track_events(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.initialize()
    registry = ObjectLabelRegistry(store)
    registry.learn_from_track(_track(), (480, 640, 3), "work backpack")

    events = [
        {
            "id": 1,
            "timestamp": "2026-06-16T20:30:00+00:00",
            "event_type": "object_detected",
            "severity": "info",
            "summary": "Backpack detected near workbench; small object.",
            "track_id": 1,
            "label": "backpack",
            "zone": "workbench",
            "metadata": {},
        }
    ]

    labeled = registry.apply_to_events(events)

    assert labeled[0]["label"] == "work backpack"
    assert labeled[0]["summary"].startswith("work backpack detected near workbench")
    assert labeled[0]["metadata"]["custom_label"] == "work backpack"
    assert labeled[0]["metadata"]["base_label"] == "backpack"


def test_label_registry_reset_clears_persisted_profiles(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.initialize()
    registry = ObjectLabelRegistry(store)
    registry.learn_from_track(_track(), (480, 640, 3), "work backpack")

    deleted = registry.reset()

    assert deleted == 1
    assert registry.profiles() == []
    assert store.list_object_label_profiles() == []


def test_tracker_keeps_custom_label_after_detector_update():
    tracker = CentroidTracker()
    now = datetime.now(timezone.utc)

    assigned = tracker.update(
        [Detection(label="backpack", confidence=0.9, bbox=(100, 120, 80, 90), source="onnx")],
        now,
    )
    track_id = assigned[0].track_id
    assert track_id is not None

    tracker.label_track(track_id, "work backpack")
    tracker.update(
        [Detection(label="backpack", confidence=0.91, bbox=(104, 122, 80, 90), source="onnx")],
        now,
    )

    track = tracker.tracks[track_id]
    assert track.label == "work backpack"
    assert track.custom_label == "work backpack"
    assert track.detector_label == "backpack"


def test_tracker_can_clear_custom_labels_for_new_browser_session():
    tracker = CentroidTracker()
    now = datetime.now(timezone.utc)
    assigned = tracker.update(
        [Detection(label="backpack", confidence=0.9, bbox=(100, 120, 80, 90), source="onnx")],
        now,
    )
    track_id = assigned[0].track_id
    assert track_id is not None
    tracker.label_track(track_id, "work backpack")

    tracks = tracker.clear_custom_labels()

    assert tracks[0].label == "backpack"
    assert tracks[0].custom_label is None
    assert tracks[0].detector_label == "backpack"
