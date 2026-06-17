from datetime import datetime, timezone

import numpy as np

from vision_appliance.config import Settings
from vision_appliance.models import IncidentEvent
from vision_appliance.video_recorder import ClipRecorder


def _frame(value: int):
    return np.full((4, 4, 3), value, dtype=np.uint8)


def test_event_clip_uses_four_second_prebuffer_and_eight_second_postbuffer(tmp_path, monkeypatch):
    writers = []

    class FakeWriter:
        def __init__(self, *_args):
            self.frames = []
            self.released = False
            writers.append(self)

        def isOpened(self):
            return True

        def write(self, frame):
            self.frames.append(int(frame[0, 0, 0]))

        def release(self):
            self.released = True

    monkeypatch.setattr("vision_appliance.video_recorder.cv2.VideoWriter", FakeWriter)
    monkeypatch.setattr("vision_appliance.video_recorder.cv2.VideoWriter_fourcc", lambda *_args: 0)

    settings = Settings(
        data_dir=tmp_path,
        fps=1,
        clip_seconds_before=4,
        clip_seconds_after=8,
        clip_encoder="opencv",
    )
    settings.ensure_directories()
    recorder = ClipRecorder(settings)

    for timestamp in range(11):
        recorder.add_frame(_frame(timestamp), float(timestamp))

    event = IncidentEvent(
        event_type="object_detected",
        summary="Backpack detected near workbench.",
        timestamp=datetime.fromtimestamp(10, tz=timezone.utc),
        label="backpack",
        track_id=7,
    )

    path = recorder.start_event_clip(_frame(10), 10.0, event)
    for timestamp in range(11, 20):
        recorder.add_frame(_frame(timestamp), float(timestamp))

    assert path is not None
    assert len(writers) == 1
    assert writers[0].frames == list(range(6, 19))
    assert writers[0].released is True
