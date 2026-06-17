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
from .object_labeler import ObjectLabelRegistry
from .motion_detector import MotionDetector
from .object_detector import ObjectDetector
from .object_tracker import CentroidTracker
from .thermal_guard import ThermalAction, ThermalGuard
from .video_recorder import ClipRecorder, cleanup_media_by_count, cleanup_old_files

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
        self.thermal_guard = ThermalGuard(settings)
        self.label_registry = ObjectLabelRegistry(store)

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._tracker_lock = threading.Lock()
        self._last_thermal_event_mode = "normal"
        self._last_frame_shape: tuple[int, int, int] | None = None
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
            "throttle": self.thermal_guard.evaluate().as_dict(),
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

    def system_health(self) -> dict[str, Any]:
        return self.thermal_guard.health()

    def object_label_profiles(self) -> list[dict[str, Any]]:
        return self.label_registry.profiles()

    def apply_object_labels_to_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return self.label_registry.apply_to_events(events)

    def recent_events_for_report(self, limit: int) -> list[dict[str, Any]]:
        return self.apply_object_labels_to_events(self.store.recent_events(limit=limit))

    def label_track(self, track_id: int, label: str) -> dict[str, Any] | None:
        with self._tracker_lock:
            track = self.tracker.tracks.get(track_id)
            if track is None:
                return None
            frame_shape = self._last_frame_shape or (
                self.settings.frame_height,
                self.settings.frame_width,
                3,
            )
            profile = self.label_registry.learn_from_track(track, frame_shape, label)
            labeled_track = self.tracker.label_track(track_id, profile["name"])
            if labeled_track is None:
                return None
            labeled_payload = labeled_track.as_dict()
            tracks = [item.as_dict() for item in self.tracker.active_tracks()]
            label_event = IncidentEvent(
                event_type="object_labeled",
                summary=(
                    f"{_title_label(labeled_track.detector_label or labeled_track.label)} "
                    f"track #{track_id} labeled as {profile['name']}."
                ),
                severity="info",
                timestamp=datetime.now(timezone.utc),
                track_id=track_id,
                label=profile["name"],
                metadata={
                    "custom_label": profile["name"],
                    "base_label": labeled_track.detector_label or labeled_track.label,
                    "label_profile_id": profile["id"],
                },
            )
            event_id = self.store.insert_event(label_event)
            LOGGER.info("Event %s: %s", event_id, label_event.summary)
            self._prune_event_history()
        with self._lock:
            self._latest_status["tracks"] = tracks
        return {"track": labeled_payload, "profile": profile}

    def delete_object_label_profile(self, profile_id: int) -> bool:
        return self.label_registry.delete_profile(profile_id)

    def reset_object_labels(self) -> dict[str, Any]:
        deleted_profiles = self.label_registry.reset()
        with self._tracker_lock:
            tracks = [track.as_dict() for track in self.tracker.clear_custom_labels()]
        with self._lock:
            self._latest_status["tracks"] = tracks
            self._latest_status["detections"] = [
                _clear_detection_label(detection) for detection in self._latest_status["detections"]
            ]
        return {"deleted_profiles": deleted_profiles, "tracks": tracks}

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
                loop_started = time.monotonic()
                thermal_action = self.thermal_guard.evaluate()
                packet = self.camera.read()
                if packet is None:
                    continue

                now = datetime.fromtimestamp(packet.captured_at, tz=timezone.utc)
                self._last_frame_shape = packet.frame.shape
                detections = []
                tracked_detections = []
                motion_regions = []
                active_tracks = []
                pending_events: list[IncidentEvent] = []
                self._record_thermal_transition(thermal_action, now)

                if not thermal_action.detection_paused:
                    motion_regions, _ = self.motion_detector.detect(packet.frame)
                    if packet.index % thermal_action.effective_detection_interval == 0:
                        detections = self.object_detector.detect(packet.frame, motion_regions)
                        detections = self.label_registry.apply_to_detections(
                            detections,
                            packet.frame.shape,
                        )
                        with self._tracker_lock:
                            tracked_detections = self.tracker.update(detections, now)
                            active_tracks = self.tracker.active_tracks()
                            events = self.event_generator.generate(
                                tracked_detections,
                                active_tracks,
                                motion_regions,
                                now,
                                packet.frame.shape,
                            )
                        pending_events = events
                    else:
                        with self._tracker_lock:
                            active_tracks = self.tracker.active_tracks()
                            tracked_detections = [
                                track.as_dict()
                                for track in active_tracks
                                if track.disappeared_frames == 0
                            ]
                else:
                    with self._tracker_lock:
                        active_tracks = self.tracker.active_tracks()
                        tracked_detections = [
                            track.as_dict() for track in active_tracks if track.disappeared_frames == 0
                        ]

                motion_payload = [region.as_dict() for region in motion_regions]
                detection_payload = [
                    item.as_dict() if hasattr(item, "as_dict") else item for item in tracked_detections
                ]
                tracks_payload = [track.as_dict() for track in active_tracks]
                overlay = draw_overlay(
                    packet.frame,
                    detections=detection_payload,
                    motion_regions=motion_payload[:8],
                    zones=[zone.as_dict() for zone in self.settings.zones],
                )
                self.recorder.add_frame(overlay, packet.captured_at)
                self._record_events(pending_events, overlay, packet.captured_at)

                frame_times.append(time.monotonic())
                frame_times = frame_times[-max(5, thermal_action.effective_fps * 3) :]
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
                            "throttle": thermal_action.as_dict(),
                        }
                    )

                if time.monotonic() - last_cleanup > 3600:
                    deleted = cleanup_old_files(self.settings.clips_dir, self.settings.retention_days)
                    deleted += cleanup_old_files(self.settings.frames_dir, self.settings.retention_days)
                    if deleted:
                        LOGGER.info("Retention cleanup deleted %s old files", deleted)
                    last_cleanup = time.monotonic()

                self._pace_loop(loop_started, thermal_action.effective_fps)
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
        self._prune_event_history()

    def _record_thermal_transition(self, action: ThermalAction, timestamp: datetime) -> None:
        previous = self._last_thermal_event_mode
        if action.mode == previous:
            return
        self._last_thermal_event_mode = action.mode
        if action.mode in {"unknown", "disabled"}:
            return

        if action.active:
            severity = "critical" if action.detection_paused else "warning"
            if action.mode == "warm":
                severity = "notice"
            summary = (
                f"Thermal guard entered {action.mode} mode at {action.temperature_c} C; "
                f"{action.reason}."
            )
            event_type = "thermal_throttle"
        elif previous in {"warm", "hot", "critical"}:
            severity = "info"
            summary = (
                f"Thermal guard returned to normal mode at {action.temperature_c} C; "
                "restored configured FPS and detection cadence."
            )
            event_type = "thermal_recovered"
        else:
            return

        event_id = self.store.insert_event(
            IncidentEvent(
                event_type=event_type,
                summary=summary,
                severity=severity,
                timestamp=timestamp,
                metadata=action.as_dict(),
            )
        )
        LOGGER.info("Event %s: %s", event_id, summary)
        self._prune_event_history()

    def _prune_event_history(self) -> None:
        deleted_events = self.store.prune_events(self.settings.history_limit)
        for event in deleted_events:
            _delete_child_media(event.get("clip_path"), self.settings.data_dir)
            _delete_child_media(event.get("frame_path"), self.settings.data_dir)
        cleanup_media_by_count(
            self.settings.clips_dir,
            {".mp4", ".avi", ".mov"},
            self.settings.history_limit,
        )
        cleanup_media_by_count(
            self.settings.frames_dir,
            {".jpg", ".jpeg", ".png"},
            self.settings.history_limit,
        )

    @staticmethod
    def _pace_loop(loop_started: float, fps: int) -> None:
        target_seconds = 1 / max(1, fps)
        remaining = target_seconds - (time.monotonic() - loop_started)
        if remaining > 0:
            time.sleep(min(remaining, 1.0))


def _estimate_fps(frame_times: list[float]) -> float:
    if len(frame_times) < 2:
        return 0.0
    elapsed = frame_times[-1] - frame_times[0]
    if elapsed <= 0:
        return 0.0
    return (len(frame_times) - 1) / elapsed


def _title_label(label: str) -> str:
    return label[:1].upper() + label[1:]


def _delete_child_media(raw_path: Any, data_dir: Path) -> None:
    if not raw_path:
        return
    try:
        path = Path(str(raw_path)).resolve()
        root = data_dir.resolve()
    except OSError:
        return
    if path != root and root not in path.parents:
        return
    path.unlink(missing_ok=True)


def _clear_detection_label(detection: Any) -> Any:
    if not isinstance(detection, dict):
        return detection
    cleaned = dict(detection)
    base_label = cleaned.get("detector_label") or cleaned.get("label")
    cleaned["label"] = base_label
    cleaned["custom_label"] = None
    metadata = dict(cleaned.get("metadata") or {})
    metadata.pop("label_profile_id", None)
    metadata.pop("custom_label", None)
    cleaned["metadata"] = metadata
    return cleaned


def safe_child_path(root: Path, name: str) -> Path:
    candidate = (root / name).resolve()
    root_resolved = root.resolve()
    if root_resolved not in candidate.parents and candidate != root_resolved:
        raise ValueError("Path escapes storage root")
    return candidate
