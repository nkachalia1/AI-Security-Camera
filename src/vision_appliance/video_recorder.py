from __future__ import annotations

import logging
import shutil
import subprocess
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from .config import Settings
from .models import IncidentEvent

LOGGER = logging.getLogger(__name__)


class _ClipWriter(Protocol):
    def isOpened(self) -> bool: ...

    def write(self, frame: np.ndarray) -> None: ...

    def release(self) -> None: ...


@dataclass
class _ActiveClip:
    writer: _ClipWriter
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
        closed_any = False
        for clip in self._active:
            if captured_at <= clip.end_time:
                clip.writer.write(frame)
                still_active.append(clip)
            else:
                clip.writer.release()
                LOGGER.info("Closed event clip: %s", clip.path)
                closed_any = True
        self._active = still_active
        if closed_any:
            cleanup_media_by_count(
                self.settings.clips_dir,
                {".mp4", ".avi", ".mov"},
                self.settings.history_limit,
            )

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
        writer = self._open_writer(path, width, height)
        if not writer.isOpened():
            LOGGER.warning("Could not open clip writer for %s", path)
            return None

        start_time = captured_at - self.settings.clip_seconds_before
        wrote_trigger_frame = False
        for frame_time, buffered_frame in self._buffer:
            if frame_time >= start_time:
                writer.write(buffered_frame)
                if abs(frame_time - captured_at) < 0.001:
                    wrote_trigger_frame = True
        if not wrote_trigger_frame:
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

    def _open_writer(self, path: Path, width: int, height: int) -> _ClipWriter:
        encoder = self.settings.clip_encoder
        if encoder in {"auto", "ffmpeg"} and shutil.which("ffmpeg"):
            return _FfmpegVideoWriter(path, self.settings.fps, width, height)
        if encoder == "ffmpeg":
            LOGGER.warning("VISION_CLIP_ENCODER=ffmpeg requested, but ffmpeg is not installed")
        return _OpenCvVideoWriter(path, self.settings.fps, width, height)

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


def cleanup_media_by_count(root: Path, suffixes: set[str], keep: int) -> int:
    if keep <= 0 or not root.exists():
        return 0
    files = [
        path
        for path in root.iterdir()
        if path.is_file() and path.suffix.lower() in suffixes
    ]
    files.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    deleted = 0
    for path in files[keep:]:
        path.unlink(missing_ok=True)
        deleted += 1
    return deleted


class _FfmpegVideoWriter:
    def __init__(self, path: Path, fps: int, width: int, height: int):
        self.path = path
        self._process: subprocess.Popen[bytes] | None = None
        command = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "bgr24",
            "-s",
            f"{width}x{height}",
            "-r",
            str(max(1, fps)),
            "-i",
            "-",
            "-an",
            "-vcodec",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-movflags",
            "+faststart",
            str(path),
        ]
        try:
            self._process = subprocess.Popen(command, stdin=subprocess.PIPE)
        except OSError as exc:
            LOGGER.warning("Could not start ffmpeg clip writer: %s", exc)

    def isOpened(self) -> bool:
        return self._process is not None and self._process.stdin is not None

    def write(self, frame: np.ndarray) -> None:
        if not self.isOpened():
            return
        assert self._process is not None and self._process.stdin is not None
        try:
            self._process.stdin.write(frame.tobytes())
        except (BrokenPipeError, OSError):
            LOGGER.warning("ffmpeg clip writer stopped unexpectedly for %s", self.path)
            self.release()

    def release(self) -> None:
        if self._process is None:
            return
        if self._process.stdin:
            try:
                self._process.stdin.close()
            except OSError:
                pass
        try:
            self._process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._process.kill()
            self._process.wait(timeout=2)
        self._process = None


class _OpenCvVideoWriter:
    def __init__(self, path: Path, fps: int, width: int, height: int):
        self.path = path
        self._writer: cv2.VideoWriter | None = None
        for codec in ("avc1", "H264", "mp4v"):
            writer = cv2.VideoWriter(
                str(path),
                cv2.VideoWriter_fourcc(*codec),
                float(max(1, fps)),
                (width, height),
            )
            if writer.isOpened():
                self._writer = writer
                LOGGER.info("Opened OpenCV clip writer for %s with codec %s", path, codec)
                if codec == "mp4v":
                    LOGGER.warning(
                        "OpenCV fell back to mp4v; install ffmpeg for browser-playable H.264 clips"
                    )
                break

    def isOpened(self) -> bool:
        return self._writer is not None and self._writer.isOpened()

    def write(self, frame: np.ndarray) -> None:
        if self._writer is not None:
            self._writer.write(frame)

    def release(self) -> None:
        if self._writer is not None:
            self._writer.release()
            self._writer = None
