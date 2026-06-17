from datetime import datetime, timezone
import os

from vision_appliance.database import EventStore
from vision_appliance.models import IncidentEvent
from vision_appliance.video_recorder import cleanup_media_by_count


def test_event_and_report_history_are_pruned_to_limit(tmp_path):
    store = EventStore(tmp_path / "events.db")
    store.initialize()

    for index in range(7):
        store.insert_event(
            IncidentEvent(
                event_type="object_detected",
                summary=f"event {index}",
                timestamp=datetime.fromtimestamp(index, tz=timezone.utc),
            )
        )
        store.insert_report(
            title=f"report {index}",
            body="body",
            event_ids=[],
            created_at=datetime.fromtimestamp(index, tz=timezone.utc).isoformat(timespec="seconds"),
        )

    deleted_events = store.prune_events(5)
    deleted_reports = store.prune_reports(5)

    assert [event["summary"] for event in deleted_events] == ["event 0", "event 1"]
    assert deleted_reports == 2
    assert [event["summary"] for event in store.list_events(limit=10)] == [
        "event 6",
        "event 5",
        "event 4",
        "event 3",
        "event 2",
    ]
    assert [report["title"] for report in store.list_reports(limit=10)] == [
        "report 6",
        "report 5",
        "report 4",
        "report 3",
        "report 2",
    ]


def test_clip_history_is_pruned_to_limit(tmp_path):
    for index in range(7):
        path = tmp_path / f"clip-{index}.mp4"
        path.write_bytes(b"clip")
        timestamp = 1_700_000_000 + index
        os.utime(path, (timestamp, timestamp))

    deleted = cleanup_media_by_count(tmp_path, {".mp4"}, keep=5)

    assert deleted == 2
    assert sorted(path.name for path in tmp_path.glob("*.mp4")) == [
        "clip-2.mp4",
        "clip-3.mp4",
        "clip-4.mp4",
        "clip-5.mp4",
        "clip-6.mp4",
    ]
