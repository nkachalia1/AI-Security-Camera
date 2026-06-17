from __future__ import annotations

import math
import re
import threading
from typing import Any

from .database import EventStore
from .models import BBox, Detection, TrackedObject


MAX_LABEL_LENGTH = 64
CENTER_DISTANCE_THRESHOLD = 0.18
AREA_RATIO_THRESHOLD = 2.5


class ObjectLabelRegistry:
    def __init__(self, store: EventStore):
        self.store = store
        self._lock = threading.RLock()
        self._profiles = store.list_object_label_profiles()

    def profiles(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._profiles)

    def apply_to_events(self, events: list[dict[str, Any]]) -> list[dict[str, Any]]:
        with self._lock:
            profiles_by_track = {
                int(profile["last_track_id"]): profile
                for profile in self._profiles
                if profile.get("last_track_id") is not None
            }

        labeled_events = []
        for event in events:
            track_id = event.get("track_id")
            profile = profiles_by_track.get(int(track_id)) if track_id is not None else None
            if profile is None:
                labeled_events.append(event)
                continue

            updated = dict(event)
            metadata = dict(updated.get("metadata") or {})
            metadata.update(
                {
                    "custom_label": profile["name"],
                    "base_label": profile["base_label"],
                    "label_profile_id": profile["id"],
                }
            )
            updated["metadata"] = metadata
            updated["label"] = profile["name"]
            updated["summary"] = _summary_with_custom_label(
                str(updated["summary"]),
                str(profile["base_label"]),
                str(profile["name"]),
            )
            labeled_events.append(updated)
        return labeled_events

    def apply_to_detections(
        self,
        detections: list[Detection],
        frame_shape: tuple[int, int, int],
    ) -> list[Detection]:
        return [self.apply_to_detection(detection, frame_shape) for detection in detections]

    def apply_to_detection(
        self,
        detection: Detection,
        frame_shape: tuple[int, int, int],
    ) -> Detection:
        base_label = _base_detection_label(detection)
        with self._lock:
            profile = self._best_profile(base_label, _normalize_bbox(detection.bbox, frame_shape))
        if profile is None:
            if detection.detector_label:
                return detection
            return Detection(
                label=detection.label,
                confidence=detection.confidence,
                bbox=detection.bbox,
                source=detection.source,
                track_id=detection.track_id,
                metadata=dict(detection.metadata),
                detector_label=base_label,
                custom_label=detection.custom_label,
            )

        metadata = dict(detection.metadata)
        metadata.update({"label_profile_id": profile["id"], "base_label": base_label})
        return Detection(
            label=profile["name"],
            confidence=detection.confidence,
            bbox=detection.bbox,
            source=detection.source,
            track_id=detection.track_id,
            metadata=metadata,
            detector_label=base_label,
            custom_label=profile["name"],
        )

    def learn_from_track(
        self,
        track: TrackedObject,
        frame_shape: tuple[int, int, int],
        label: str,
    ) -> dict[str, Any]:
        clean = clean_label(label)
        base_label = _base_track_label(track)
        bbox_norm = _normalize_bbox(track.bbox, frame_shape)
        with self._lock:
            existing = self._best_profile(base_label, bbox_norm)
            if existing is None:
                profile = self.store.create_object_label_profile(
                    name=clean,
                    base_label=base_label,
                    bbox_norm=bbox_norm,
                    track_id=track.track_id,
                )
                self._profiles.insert(0, profile)
            else:
                profile = self.store.update_object_label_profile(
                    profile_id=int(existing["id"]),
                    name=clean,
                    base_label=base_label,
                    bbox_norm=bbox_norm,
                    track_id=track.track_id,
                )
                if profile is None:
                    raise ValueError("Object label profile no longer exists")
                self._replace_profile(profile)
        return profile

    def delete_profile(self, profile_id: int) -> bool:
        deleted = self.store.delete_object_label_profile(profile_id)
        if deleted:
            with self._lock:
                self._profiles = [
                    profile for profile in self._profiles if int(profile["id"]) != profile_id
                ]
        return deleted

    def reset(self) -> int:
        deleted = self.store.clear_object_label_profiles()
        with self._lock:
            self._profiles = []
        return deleted

    def _replace_profile(self, profile: dict[str, Any]) -> None:
        self._profiles = [
            profile if int(item["id"]) == int(profile["id"]) else item for item in self._profiles
        ]

    def _best_profile(
        self,
        base_label: str,
        bbox_norm: tuple[float, float, float, float],
    ) -> dict[str, Any] | None:
        best: dict[str, Any] | None = None
        best_score = float("inf")
        for profile in self._profiles:
            if str(profile["base_label"]).lower() != base_label:
                continue
            score = _profile_score(tuple(profile["bbox_norm"]), bbox_norm)
            if score < best_score:
                best = profile
                best_score = score
        return best if best is not None and best_score <= 1.0 else None


def clean_label(raw: str) -> str:
    label = " ".join(raw.strip().split())
    if not label:
        raise ValueError("Label cannot be empty")
    if len(label) > MAX_LABEL_LENGTH:
        raise ValueError(f"Label must be {MAX_LABEL_LENGTH} characters or fewer")
    return label


def _summary_with_custom_label(summary: str, base_label: str, custom_label: str) -> str:
    if custom_label.lower() in summary.lower():
        return summary

    base_name = _title_label(base_label)
    replaced = re.sub(
        rf"^{re.escape(base_name)}\b",
        custom_label,
        summary,
        count=1,
        flags=re.IGNORECASE,
    )
    if replaced != summary:
        return replaced
    return f"{custom_label}: {summary}"


def _base_detection_label(detection: Detection) -> str:
    return (detection.detector_label or detection.label).strip().lower()


def _base_track_label(track: TrackedObject) -> str:
    return (track.detector_label or track.label).strip().lower()


def _title_label(label: str) -> str:
    return label[:1].upper() + label[1:]


def _normalize_bbox(bbox: BBox, frame_shape: tuple[int, int, int]) -> tuple[float, float, float, float]:
    height, width = frame_shape[:2]
    x, y, w, h = bbox
    return (
        _clamp(x / max(width, 1)),
        _clamp(y / max(height, 1)),
        _clamp(w / max(width, 1)),
        _clamp(h / max(height, 1)),
    )


def _profile_score(
    profile_bbox: tuple[float, float, float, float],
    bbox: tuple[float, float, float, float],
) -> float:
    profile_x, profile_y, profile_w, profile_h = profile_bbox
    x, y, w, h = bbox
    profile_center = (profile_x + profile_w / 2, profile_y + profile_h / 2)
    center = (x + w / 2, y + h / 2)
    center_distance = math.hypot(profile_center[0] - center[0], profile_center[1] - center[1])
    if center_distance > CENTER_DISTANCE_THRESHOLD:
        return float("inf")

    profile_area = max(profile_w * profile_h, 0.0001)
    area = max(w * h, 0.0001)
    area_ratio = max(profile_area / area, area / profile_area)
    if area_ratio > AREA_RATIO_THRESHOLD:
        return float("inf")

    return center_distance / CENTER_DISTANCE_THRESHOLD + (area_ratio - 1) / AREA_RATIO_THRESHOLD


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, round(float(value), 4)))
