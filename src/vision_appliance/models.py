from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


BBox = tuple[int, int, int, int]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def isoformat(dt: datetime | None = None) -> str:
    return (dt or utc_now()).isoformat(timespec="seconds")


@dataclass(frozen=True)
class Zone:
    name: str
    x1: float
    y1: float
    x2: float
    y2: float

    def contains_point(self, x: float, y: float, frame_width: int, frame_height: int) -> bool:
        nx = x / max(frame_width, 1)
        ny = y / max(frame_height, 1)
        return self.x1 <= nx <= self.x2 and self.y1 <= ny <= self.y2

    def contains_bbox_center(self, bbox: BBox, frame_width: int, frame_height: int) -> bool:
        x, y, w, h = bbox
        return self.contains_point(x + w / 2, y + h / 2, frame_width, frame_height)

    def as_dict(self) -> dict[str, float | str]:
        return {"name": self.name, "x1": self.x1, "y1": self.y1, "x2": self.x2, "y2": self.y2}


@dataclass
class MotionRegion:
    bbox: BBox
    area: float

    @property
    def centroid(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)

    def as_dict(self) -> dict[str, Any]:
        return {"bbox": list(self.bbox), "area": self.area, "centroid": list(self.centroid)}


@dataclass
class Detection:
    label: str
    confidence: float
    bbox: BBox
    source: str = "unknown"
    track_id: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def centroid(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)

    def with_track(self, track_id: int) -> "Detection":
        return Detection(
            label=self.label,
            confidence=self.confidence,
            bbox=self.bbox,
            source=self.source,
            track_id=track_id,
            metadata=dict(self.metadata),
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "confidence": round(float(self.confidence), 4),
            "bbox": list(self.bbox),
            "centroid": list(self.centroid),
            "source": self.source,
            "track_id": self.track_id,
            "metadata": self.metadata,
        }


@dataclass
class TrackedObject:
    track_id: int
    label: str
    bbox: BBox
    confidence: float
    first_seen: datetime
    last_seen: datetime
    disappeared_frames: int = 0
    zones_seen: set[str] = field(default_factory=set)
    stationary_since: datetime | None = None
    last_centroid: tuple[int, int] | None = None
    path: list[tuple[int, int]] = field(default_factory=list)
    source: str = "unknown"

    @property
    def centroid(self) -> tuple[int, int]:
        x, y, w, h = self.bbox
        return (x + w // 2, y + h // 2)

    def as_dict(self) -> dict[str, Any]:
        return {
            "track_id": self.track_id,
            "label": self.label,
            "bbox": list(self.bbox),
            "confidence": round(float(self.confidence), 4),
            "first_seen": self.first_seen.isoformat(timespec="seconds"),
            "last_seen": self.last_seen.isoformat(timespec="seconds"),
            "disappeared_frames": self.disappeared_frames,
            "zones_seen": sorted(self.zones_seen),
            "stationary_since": self.stationary_since.isoformat(timespec="seconds")
            if self.stationary_since
            else None,
            "centroid": list(self.centroid),
            "path": [list(point) for point in self.path[-12:]],
            "source": self.source,
        }


@dataclass
class IncidentEvent:
    event_type: str
    summary: str
    severity: str = "info"
    timestamp: datetime = field(default_factory=utc_now)
    track_id: int | None = None
    label: str | None = None
    zone: str | None = None
    clip_path: str | None = None
    frame_path: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "summary": self.summary,
            "severity": self.severity,
            "timestamp": self.timestamp.isoformat(timespec="seconds"),
            "track_id": self.track_id,
            "label": self.label,
            "zone": self.zone,
            "clip_path": self.clip_path,
            "frame_path": self.frame_path,
            "metadata": self.metadata,
        }
