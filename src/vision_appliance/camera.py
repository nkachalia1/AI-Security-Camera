from __future__ import annotations

import logging
import time
from dataclasses import dataclass

import cv2
import numpy as np

from .config import Settings

LOGGER = logging.getLogger(__name__)


@dataclass
class FramePacket:
    frame: np.ndarray
    captured_at: float
    index: int


class OpenCVCamera:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._capture: cv2.VideoCapture | None = None
        self._frame_index = 0

    def open(self) -> None:
        source = self._source()
        capture = cv2.VideoCapture(source)
        if not capture.isOpened():
            raise RuntimeError(f"Could not open camera source {source!r}")
        if isinstance(source, int):
            capture.set(cv2.CAP_PROP_FRAME_WIDTH, self.settings.frame_width)
            capture.set(cv2.CAP_PROP_FRAME_HEIGHT, self.settings.frame_height)
            capture.set(cv2.CAP_PROP_FPS, self.settings.fps)
        self._capture = capture
        LOGGER.info("Camera opened: source=%s", source)

    def read(self) -> FramePacket | None:
        if self._capture is None:
            self.open()
        assert self._capture is not None
        ok, frame = self._capture.read()
        if not ok or frame is None:
            LOGGER.warning("Camera read failed")
            time.sleep(0.25)
            return None
        self._frame_index += 1
        return FramePacket(frame=frame, captured_at=time.time(), index=self._frame_index)

    def release(self) -> None:
        if self._capture is not None:
            self._capture.release()
            self._capture = None
            LOGGER.info("Camera released")

    def _source(self) -> int | str:
        source = self.settings.camera_source
        if not source:
            return self.settings.camera_index
        if source.isdigit():
            return int(source)
        return source


def encode_jpeg(frame: np.ndarray, quality: int = 82) -> bytes:
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("Could not encode frame as JPEG")
    return buffer.tobytes()


def draw_overlay(
    frame: np.ndarray,
    detections: list[dict],
    motion_regions: list[dict],
    zones: list[dict],
) -> np.ndarray:
    annotated = frame.copy()
    height, width = annotated.shape[:2]

    for zone in zones:
        x1 = int(float(zone["x1"]) * width)
        y1 = int(float(zone["y1"]) * height)
        x2 = int(float(zone["x2"]) * width)
        y2 = int(float(zone["y2"]) * height)
        cv2.rectangle(annotated, (x1, y1), (x2, y2), (80, 160, 255), 2)
        cv2.putText(
            annotated,
            str(zone["name"]),
            (x1 + 6, max(20, y1 + 22)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.6,
            (80, 160, 255),
            2,
            cv2.LINE_AA,
        )

    for region in motion_regions:
        x, y, w, h = [int(v) for v in region["bbox"]]
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (60, 190, 80), 1)

    for detection in detections:
        x, y, w, h = [int(v) for v in detection["bbox"]]
        label = detection["label"]
        track_id = detection.get("track_id")
        confidence = detection.get("confidence", 0.0)
        caption = f"{label} {confidence:.2f}"
        if track_id is not None:
            caption = f"#{track_id} {caption}"
        cv2.rectangle(annotated, (x, y), (x + w, y + h), (40, 210, 255), 2)
        cv2.putText(
            annotated,
            caption,
            (x, max(18, y - 8)),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.55,
            (40, 210, 255),
            2,
            cv2.LINE_AA,
        )
    return annotated
