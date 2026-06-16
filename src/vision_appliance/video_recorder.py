from __future__ import annotations

import logging
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import cv2
import numpy as np

from .config import Settings
from .models import IncidentEvent

LOGGER = logging.getLogger(__name__)


@dataclass
class _ActiveClip:
    writer: cv2.VideoWriter
    path: Path
    end_time: float


class ClipRecorder:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._buffer: deque[tuple[float, np.ndarray]] = deque(
            maxlen=max(1, settings.fps * (settings.clip_seconds_before + settings.clip_seconds_after + 2))
        )
        self._active: list[_ActiveClip] = []

    def add_frame(self, frame: np.ndarray, captured_at: float) -> None:
        self._buffer.append((captured_at, frame.copy()))
        still_active: list[_ActiveClip] = []
        for clip in self._active:
            if captured_at <= clip.end_time:
                clip.writer.write(frame)
                still_active.append(clip)
            else:
                clip.writer.release()
                LOGGER.info("Closed event clip: %s", clip.path)
        self._active = still_active

    def save_frame(self, frame: np.ndarray, event: IncidentEvent) -> str | None:
        if not self.settings.save_debug_frames:
            return None
        filename = self._event_filename(event, suffix=".jpg")
        path = self.settings.frames_dir / filename
        ok = cv2.imwrite(str(path), frame)
        return str(path) if ok else None

    def start_event_clip(
        self,
        frame: np.ndarray,
        captured_at: float,
        event: IncidentEvent,
    ) -> str | None:
        height, width = frame.shape[:2]
        filename = self._event_filename(event, suffix=".mp4")
        path = self.settings.clips_dir / filename
        writer = cv2.VideoWriter(
            str(path),
            cv2.VideoWriter_fourcc(*"mp4v"),
            float(self.settings.fps),
            (width, height),
        )
        if not writer.isOpened():
            LOGGER.warning("Could not open clip writer for %s", path)
            return None

        start_time = captured_at - self.settings.clip_seconds_before
        for frame_time, buffered_frame in self._buffer:
            if frame_time >= start_time:
                writer.write(buffered_frame)
        writer.write(frame)
        self._active.append(
            _ActiveClip(
                writer=writer,
                path=path,
                end_time=captured_at + self.settings.clip_seconds_after,
            )
        )
        return str(path)

    def close(self) -> None:
        for clip in self._active:
            clip.writer.release()
        self._active = []

    @staticmethod
    def _event_filename(event: IncidentEvent, suffix: str) -> str:
        stamp = event.timestamp.strftime("%Y%m%d_%H%M%S")
        label = event.label or event.event_type
        label = "".join(char if char.isalnum() or char in {"-", "_"} else "_" for char in label)
        track = f"_track{event.track_id}" if event.track_id is not None else ""
        return f"{stamp}_{event.event_type}_{label}{track}{suffix}"


def cleanup_old_files(root: Path, retention_days: int) -> int:
    if retention_days <= 0 or not root.exists():
        return 0
    cutoff = time.time() - retention_days * 24 * 60 * 60
    deleted = 0
    for path in root.rglob("*"):
        if path.is_file() and path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)
            deleted += 1
    return deleted

