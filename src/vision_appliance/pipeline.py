from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .camera import OpenCVCamera, draw_overlay, encode_jpeg
from .config import Settings
from .database import EventStore
from .event_generator import EventGenerator
from .models import IncidentEvent
from .motion_detector import MotionDetector
from .object_detector import ObjectDetector
from .object_tracker import CentroidTracker
from .video_recorder import ClipRecorder, cleanup_old_files

LOGGER = logging.getLogger(__name__)


class VisionPipeline:
    def __init__(self, settings: Settings, store: EventStore):
        self.settings = settings
        self.store = store
        self.camera = OpenCVCamera(settings)
        self.motion_detector = MotionDetector(
            min_area=settings.min_motion_area,
            merge_pixels=settings.motion_merge_pixels,
        )
        self.object_detector = ObjectDetector(settings)
        self.tracker = CentroidTracker(max_disappeared=max(10, settings.fps * 2))
        self.event_generator = EventGenerator(settings)
        self.recorder = ClipRecorder(settings)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._latest_jpeg: bytes | None = None
        self._latest_raw_jpeg: bytes | None = None
        self._latest_status: dict[str, Any] = {
            "running": False,
            "frame_index": 0,
            "fps": 0.0,
            "detections": [],
            "motion_regions": [],
            "tracks": [],
            "last_error": None,
            "started_at": None,
            "detector": "initializing",
        }

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="vision-pipeline", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)
        self.recorder.close()
        self.camera.release()
        with self._lock:
            self._latest_status["running"] = False

    def latest_frame(self, annotated: bool = True) -> bytes | None:
        with self._lock:
            return self._latest_jpeg if annotated else self._latest_raw_jpeg

    def status(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._latest_status)

    def _run(self) -> None:
        started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        frame_times: list[float] = []
        last_cleanup = 0.0

        with self._lock:
            self._latest_status.update(
                {
                    "running": True,
                    "started_at": started_at,
                    "last_error": None,
                    "detector": self.object_detector.backend,
                }
            )

        try:
            self.camera.open()
            while not self._stop.is_set():
                packet = self.camera.read()
                if packet is None:
                    continue

                now = datetime.fromtimestamp(packet.captured_at, tz=timezone.utc)
                motion_regions, _ = self.motion_detector.detect(packet.frame)
                detections = []
                tracked_detections = []
                if packet.index % self.settings.detection_interval == 0:
                    detections = self.object_detector.detect(packet.frame, motion_regions)
                    tracked_detections = self.tracker.update(detections, now)
                    events = self.event_generator.generate(
                        tracked_detections,
                        self.tracker.active_tracks(),
                        motion_regions,
                        now,
                        packet.frame.shape,
                    )
                    self._record_events(events, packet.frame, packet.captured_at)
                else:
                    tracked_detections = [
                        track.as_dict() for track in self.tracker.active_tracks() if track.disappeared_frames == 0
                    ]

                motion_payload = [region.as_dict() for region in motion_regions]
                detection_payload = [
                    item.as_dict() if hasattr(item, "as_dict") else item for item in tracked_detections
                ]
                tracks_payload = [track.as_dict() for track in self.tracker.active_tracks()]
                overlay = draw_overlay(
                    packet.frame,
                    detections=detection_payload,
                    motion_regions=motion_payload[:8],
                    zones=[zone.as_dict() for zone in self.settings.zones],
                )
                self.recorder.add_frame(overlay, packet.captured_at)

                frame_times.append(time.monotonic())
                frame_times = frame_times[-max(5, self.settings.fps * 3) :]
                fps = _estimate_fps(frame_times)

                with self._lock:
                    self._latest_jpeg = encode_jpeg(overlay)
                    self._latest_raw_jpeg = encode_jpeg(packet.frame)
                    self._latest_status.update(
                        {
                            "running": True,
                            "frame_index": packet.index,
                            "fps": round(fps, 2),
                            "detections": detection_payload,
                            "motion_regions": motion_payload[:12],
                            "tracks": tracks_payload,
                            "last_error": None,
                        }
                    )

                if time.monotonic() - last_cleanup > 3600:
                    deleted = cleanup_old_files(self.settings.clips_dir, self.settings.retention_days)
                    deleted += cleanup_old_files(self.settings.frames_dir, self.settings.retention_days)
                    if deleted:
                        LOGGER.info("Retention cleanup deleted %s old files", deleted)
                    last_cleanup = time.monotonic()
        except Exception as exc:
            LOGGER.exception("Vision pipeline stopped after error")
            with self._lock:
                self._latest_status.update({"running": False, "last_error": str(exc)})
        finally:
            self.recorder.close()
            self.camera.release()
            with self._lock:
                self._latest_status["running"] = False

    def _record_events(self, events: list[IncidentEvent], frame, captured_at: float) -> None:
        for event in events:
            event.frame_path = self.recorder.save_frame(frame, event)
            event.clip_path = self.recorder.start_event_clip(frame, captured_at, event)
            event_id = self.store.insert_event(event)
            LOGGER.info("Event %s: %s", event_id, event.summary)


def _estimate_fps(frame_times: list[float]) -> float:
    if len(frame_times) < 2:
        return 0.0
    elapsed = frame_times[-1] - frame_times[0]
    if elapsed <= 0:
        return 0.0
    return (len(frame_times) - 1) / elapsed


def safe_child_path(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise ValueError("Path escapes storage root")
    return candidate
