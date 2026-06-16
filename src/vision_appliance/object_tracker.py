from __future__ import annotations

import math
from datetime import datetime

from .models import Detection, TrackedObject


class CentroidTracker:
    def __init__(self, max_disappeared: int = 25, max_distance: int = 120, stationary_pixels: int = 24):
        self.max_disappeared = max_disappeared
        self.max_distance = max_distance
        self.stationary_pixels = stationary_pixels
        self._next_id = 1
        self.tracks: dict[int, TrackedObject] = {}

    def update(self, detections: list[Detection], timestamp: datetime) -> list[Detection]:
        if not detections:
            self._mark_all_disappeared()
            return []

        unmatched_track_ids = set(self.tracks.keys())
        assigned: list[Detection] = []

        for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
            track_id = self._best_match(detection, unmatched_track_ids)
            if track_id is None:
                track_id = self._register(detection, timestamp)
            else:
                self._update_track(track_id, detection, timestamp)
                unmatched_track_ids.discard(track_id)
            assigned.append(detection.with_track(track_id))

        for track_id in list(unmatched_track_ids):
            track = self.tracks.get(track_id)
            if track is None:
                continue
            track.disappeared_frames += 1
            if track.disappeared_frames > self.max_disappeared:
                del self.tracks[track_id]

        return assigned

    def active_tracks(self) -> list[TrackedObject]:
        return sorted(self.tracks.values(), key=lambda track: track.track_id)

    def _register(self, detection: Detection, timestamp: datetime) -> int:
        track_id = self._next_id
        self._next_id += 1
        centroid = detection.centroid
        self.tracks[track_id] = TrackedObject(
            track_id=track_id,
            label=detection.label,
            bbox=detection.bbox,
            confidence=detection.confidence,
            first_seen=timestamp,
            last_seen=timestamp,
            stationary_since=timestamp,
            last_centroid=centroid,
            path=[centroid],
            source=detection.source,
        )
        return track_id

    def _update_track(self, track_id: int, detection: Detection, timestamp: datetime) -> None:
        track = self.tracks[track_id]
        previous = track.last_centroid or track.centroid
        distance = _distance(previous, detection.centroid)
        if distance > self.stationary_pixels:
            track.stationary_since = timestamp
        elif track.stationary_since is None:
            track.stationary_since = timestamp
        track.label = detection.label if detection.source != "motion" else track.label
        track.bbox = detection.bbox
        if detection.source != "motion":
            track.confidence = detection.confidence
        track.last_seen = timestamp
        track.disappeared_frames = 0
        track.last_centroid = detection.centroid
        track.path.append(detection.centroid)
        track.path = track.path[-20:]
        if detection.source != "motion":
            track.source = detection.source

    def _best_match(self, detection: Detection, candidates: set[int]) -> int | None:
        best_id: int | None = None
        best_distance = float("inf")
        for track_id in candidates:
            track = self.tracks[track_id]
            if not _labels_compatible(track.label, detection.label):
                continue
            distance = _distance(track.centroid, detection.centroid)
            if distance < best_distance:
                best_id = track_id
                best_distance = distance
        if best_id is not None and best_distance <= self.max_distance:
            return best_id
        return None

    def _mark_all_disappeared(self) -> None:
        for track_id in list(self.tracks.keys()):
            track = self.tracks[track_id]
            track.disappeared_frames += 1
            if track.disappeared_frames > self.max_disappeared:
                del self.tracks[track_id]


def _distance(a: tuple[int, int], b: tuple[int, int]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _labels_compatible(existing: str, incoming: str) -> bool:
    if existing == incoming:
        return True
    generic = {"moving object"}
    return existing in generic or incoming in generic
