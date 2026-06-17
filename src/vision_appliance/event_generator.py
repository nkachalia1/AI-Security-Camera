from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from .config import Settings
from .models import Detection, IncidentEvent, MotionRegion, TrackedObject


WATCHED_UNATTENDED_LABELS = {
    "backpack",
    "handbag",
    "suitcase",
    "laptop",
    "cell phone",
    "bottle",
    "package",
}

DESCRIBED_OBJECT_LABELS = WATCHED_UNATTENDED_LABELS | {
    "book",
    "cup",
    "keyboard",
    "mouse",
    "remote",
    "scissors",
    "sports ball",
    "umbrella",
}


@dataclass
class _TrackMemory:
    entered_emitted: bool = False
    exit_emitted: bool = False
    object_detected_emitted: bool = False
    unattended_emitted: bool = False
    zones_emitted: set[str] = field(default_factory=set)
    first_label: str | None = None


class EventGenerator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._memory: defaultdict[int, _TrackMemory] = defaultdict(_TrackMemory)
        self._last_large_motion_at: datetime | None = None
        self.unattended_after = timedelta(seconds=18)
        self.large_motion_cooldown = timedelta(seconds=20)

    def generate(
        self,
        detections: list[Detection],
        tracks: Iterable[TrackedObject],
        motion_regions: list[MotionRegion],
        timestamp: datetime,
        frame_shape: tuple[int, int, int],
    ) -> list[IncidentEvent]:
        events: list[IncidentEvent] = []
        height, width = frame_shape[:2]
        detection_by_track = {det.track_id: det for det in detections if det.track_id is not None}

        for track in tracks:
            memory = self._memory[track.track_id]
            memory.first_label = memory.first_label or track.label
            active_detection = detection_by_track.get(track.track_id)

            if self._is_person(track) and not memory.entered_emitted and track.disappeared_frames == 0:
                memory.entered_emitted = True
                context = self._event_context(track, active_detection, width, height, timestamp)
                events.append(
                    IncidentEvent(
                        event_type="person_entered",
                        summary=f"Person entered room {context['location']}; {context['size']} detection.",
                        severity="info",
                        timestamp=timestamp,
                        track_id=track.track_id,
                        label="person",
                        zone=context["zone"],
                        metadata=context,
                    )
                )

            if self._should_emit_object_detected(track, memory):
                memory.object_detected_emitted = True
                context = self._event_context(track, active_detection, width, height, timestamp)
                events.append(
                    IncidentEvent(
                        event_type="object_detected",
                        summary=(
                            f"{_object_name(track.label)} detected {context['location']}; "
                            f"{context['size']} object, {context['confidence']}."
                        ),
                        severity="info",
                        timestamp=timestamp,
                        track_id=track.track_id,
                        label=track.label,
                        zone=context["zone"],
                        metadata=context,
                    )
                )

            if self._is_person(track) and not memory.exit_emitted and track.disappeared_frames >= 18:
                memory.exit_emitted = True
                context = self._event_context(track, active_detection, width, height, timestamp)
                events.append(
                    IncidentEvent(
                        event_type="person_exited",
                        summary=(
                            f"Person exited room after {context['duration']}; "
                            f"last seen {context['location']}."
                        ),
                        severity="info",
                        timestamp=timestamp,
                        track_id=track.track_id,
                        label="person",
                        zone=context["zone"],
                        metadata=context,
                    )
                )

            if active_detection is not None:
                events.extend(self._zone_events(track, active_detection, memory, timestamp, width, height))

            if self._looks_unattended(track, timestamp) and not memory.unattended_emitted:
                memory.unattended_emitted = True
                context = self._event_context(track, active_detection, width, height, timestamp)
                events.append(
                    IncidentEvent(
                        event_type="unattended_object",
                        summary=(
                            f"{_object_name(track.label)} remained unattended {context['location']} "
                            f"for {context['stationary_duration']}; {context['size']} object."
                        ),
                        severity="warning",
                        timestamp=timestamp,
                        track_id=track.track_id,
                        label=track.label,
                        zone=context["zone"],
                        metadata={
                            **context,
                            "stationary_since": track.stationary_since.isoformat(timespec="seconds")
                            if track.stationary_since
                            else None,
                            "raw_confidence": track.confidence,
                        },
                    )
                )

        if self._large_motion(motion_regions, frame_shape, timestamp):
            events.append(
                IncidentEvent(
                    event_type="large_motion",
                    summary="Large movement detected in the room.",
                    severity="notice",
                    timestamp=timestamp,
                    metadata={"regions": [region.as_dict() for region in motion_regions[:3]]},
                )
            )
        return events

    def _zone_events(
        self,
        track: TrackedObject,
        detection: Detection,
        memory: _TrackMemory,
        timestamp: datetime,
        width: int,
        height: int,
    ) -> list[IncidentEvent]:
        events: list[IncidentEvent] = []
        if not self.settings.emit_generic_motion_events and track.label == "moving object":
            return events
        for zone in self.settings.zones:
            if not zone.contains_bbox_center(detection.bbox, width, height):
                continue
            track.zones_seen.add(zone.name)
            if zone.name in memory.zones_emitted:
                continue
            memory.zones_emitted.add(zone.name)
            label = "Person" if self._is_person(track) else _object_name(track.label)
            verb = "approached" if self._is_person(track) else "moved into"
            context = self._event_context(track, detection, width, height, timestamp)
            movement = f"; {context['movement']}" if context["movement"] != "movement not yet established" else ""
            events.append(
                IncidentEvent(
                    event_type="zone_entry",
                    summary=f"{label} {verb} {zone.name}{movement}; {context['confidence']}.",
                    severity="info",
                    timestamp=timestamp,
                    track_id=track.track_id,
                    label=track.label,
                    zone=zone.name,
                    metadata=context,
                )
            )
        return events

    def _should_emit_object_detected(self, track: TrackedObject, memory: _TrackMemory) -> bool:
        if memory.object_detected_emitted or track.disappeared_frames > 0:
            return False
        label = self._semantic_label(track)
        if label in {"person", "moving object"}:
            return False
        if track.source in {"motion", "hog"}:
            return False
        return label in DESCRIBED_OBJECT_LABELS

    def _looks_unattended(self, track: TrackedObject, timestamp: datetime) -> bool:
        if track.disappeared_frames > 0 or track.stationary_since is None:
            return False
        label = self._semantic_label(track)
        if label == "person":
            return False
        if label == "moving object" and track.source == "motion":
            return False
        if label not in WATCHED_UNATTENDED_LABELS:
            return False
        return timestamp - track.stationary_since >= self.unattended_after

    def _large_motion(
        self,
        motion_regions: list[MotionRegion],
        frame_shape: tuple[int, int, int],
        timestamp: datetime,
    ) -> bool:
        if self._last_large_motion_at and timestamp - self._last_large_motion_at < self.large_motion_cooldown:
            return False
        height, width = frame_shape[:2]
        frame_area = max(width * height, 1)
        large_area = sum(region.area for region in motion_regions[:4])
        if large_area / frame_area < 0.22:
            return False
        self._last_large_motion_at = timestamp
        return True

    @staticmethod
    def _is_person(track: TrackedObject) -> bool:
        return EventGenerator._semantic_label(track) == "person"

    @staticmethod
    def _semantic_label(track: TrackedObject) -> str:
        return (track.detector_label or track.label).lower()

    def _track_location(
        self,
        track: TrackedObject,
        detection: Detection | None,
        width: int,
        height: int,
    ) -> str:
        bbox = detection.bbox if detection is not None else track.bbox
        for zone in self.settings.zones:
            if zone.contains_bbox_center(bbox, width, height):
                return f"near {zone.name}"
        x, y, w, h = bbox
        cx = (x + w / 2) / max(width, 1)
        cy = (y + h / 2) / max(height, 1)
        horizontal = "left side" if cx < 0.33 else "right side" if cx > 0.66 else "center"
        vertical = "front" if cy > 0.66 else "back" if cy < 0.33 else "middle"
        if horizontal == "center":
            return f"in the {vertical} center"
        return f"on the {vertical} {horizontal}"

    def _event_context(
        self,
        track: TrackedObject,
        detection: Detection | None,
        width: int,
        height: int,
        timestamp: datetime,
    ) -> dict[str, str | float | int | None]:
        bbox = detection.bbox if detection is not None else track.bbox
        location = self._track_location(track, detection, width, height)
        return {
            "location": location,
            "zone": location.removeprefix("near ") if location.startswith("near ") else None,
            "movement": self._movement_phrase(track, width, height),
            "size": self._size_phrase(bbox, width, height),
            "duration": _duration_phrase(timestamp - track.first_seen),
            "stationary_duration": _duration_phrase(timestamp - track.stationary_since)
            if track.stationary_since
            else "unknown duration",
            "confidence": _confidence_phrase(track),
            "source": track.source,
            "confidence_score": round(float(track.confidence), 3),
            "track_id": track.track_id,
        }

    @staticmethod
    def _movement_phrase(track: TrackedObject, width: int, height: int) -> str:
        if len(track.path) < 2:
            return "movement not yet established"
        start_x, start_y = track.path[0]
        end_x, end_y = track.path[-1]
        dx = end_x - start_x
        dy = end_y - start_y
        distance = (dx**2 + dy**2) ** 0.5
        frame_diag = max((width**2 + height**2) ** 0.5, 1)
        if distance / frame_diag < 0.035:
            return "mostly stationary"
        horizontal = "rightward" if dx > 0 else "leftward"
        vertical = "toward the front" if dy > 0 else "toward the back"
        if abs(dx) > abs(dy) * 1.6:
            direction = horizontal
        elif abs(dy) > abs(dx) * 1.6:
            direction = vertical
        else:
            direction = f"{vertical} and {horizontal}"
        speed = "fast" if distance / frame_diag > 0.18 else "steady"
        return f"{speed} movement {direction}"

    @staticmethod
    def _size_phrase(bbox: tuple[int, int, int, int], width: int, height: int) -> str:
        _, _, w, h = bbox
        ratio = (w * h) / max(width * height, 1)
        if ratio >= 0.22:
            return "large foreground"
        if ratio >= 0.08:
            return "medium-sized"
        return "small"


def _title_label(label: str) -> str:
    return label[:1].upper() + label[1:]


def _object_name(label: str) -> str:
    if label.lower() == "cell phone":
        return "Cell phone"
    return _title_label(label)


def _duration_phrase(delta: timedelta | None) -> str:
    if delta is None:
        return "unknown duration"
    seconds = max(0, int(delta.total_seconds()))
    if seconds < 2:
        return "under 2 seconds"
    if seconds < 60:
        return f"{seconds} seconds"
    minutes, remainder = divmod(seconds, 60)
    if minutes == 1:
        return f"1 minute {remainder} seconds"
    return f"{minutes} minutes {remainder} seconds"


def _confidence_phrase(track: TrackedObject) -> str:
    confidence = int(round(track.confidence * 100))
    if track.source == "onnx":
        return f"object-detector confidence {confidence}%"
    if track.source == "hog":
        return f"person-detector confidence {confidence}%"
    if track.source == "motion":
        return "motion-based detection"
    return f"{track.source} confidence {confidence}%"
