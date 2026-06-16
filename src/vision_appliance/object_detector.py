from __future__ import annotations

import logging
from pathlib import Path

import cv2
import numpy as np

from .config import Settings
from .models import Detection, MotionRegion

LOGGER = logging.getLogger(__name__)


class ObjectDetector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.labels = self._load_labels(settings.labels_file)
        self._net: cv2.dnn.Net | None = None
        self._hog: cv2.HOGDescriptor | None = None
        self.backend = "hog_motion"

        if settings.onnx_model and settings.onnx_model.exists():
            self._net = cv2.dnn.readNetFromONNX(str(settings.onnx_model))
            self.backend = "onnx"
            LOGGER.info("Loaded ONNX detector: %s", settings.onnx_model)
        else:
            self._hog = cv2.HOGDescriptor()
            self._hog.setSVMDetector(cv2.HOGDescriptor_getDefaultPeopleDetector())
            if settings.onnx_model:
                LOGGER.warning("ONNX model not found: %s; falling back to HOG + motion", settings.onnx_model)
            else:
                LOGGER.info("No ONNX detector configured; using HOG + motion fallback")

    def detect(self, frame: np.ndarray, motion_regions: list[MotionRegion]) -> list[Detection]:
        if self._net is not None:
            detections = self._detect_yolo_onnx(frame)
            if detections:
                return detections
        return self._detect_hog_and_motion(frame, motion_regions)

    def _detect_yolo_onnx(self, frame: np.ndarray) -> list[Detection]:
        height, width = frame.shape[:2]
        input_size = self.settings.onnx_input_size
        blob = cv2.dnn.blobFromImage(
            frame,
            scalefactor=1 / 255.0,
            size=(input_size, input_size),
            swapRB=True,
            crop=False,
        )
        assert self._net is not None
        self._net.setInput(blob)
        output = self._net.forward()

        predictions = np.squeeze(output)
        if predictions.ndim != 2:
            LOGGER.debug("Unexpected ONNX output shape: %s", output.shape)
            return []
        if predictions.shape[0] < predictions.shape[1]:
            predictions = predictions.T

        boxes: list[list[int]] = []
        confidences: list[float] = []
        class_ids: list[int] = []

        scale_x = width / input_size
        scale_y = height / input_size
        for row in predictions:
            if row.shape[0] < 6:
                continue
            scores = row[4:] if row.shape[0] == len(self.labels) + 4 else row[5:]
            if scores.size == 0:
                continue
            class_id = int(np.argmax(scores))
            confidence = float(scores[class_id])
            objectness = float(row[4]) if row.shape[0] != len(self.labels) + 4 else 1.0
            confidence *= objectness
            if confidence < self.settings.confidence_threshold:
                continue

            label = self.labels[class_id] if class_id < len(self.labels) else str(class_id)
            if self.settings.detect_labels and label.lower() not in self.settings.detect_labels:
                continue

            cx, cy, bw, bh = [float(v) for v in row[:4]]
            x = int((cx - bw / 2) * scale_x)
            y = int((cy - bh / 2) * scale_y)
            w = int(bw * scale_x)
            h = int(bh * scale_y)
            x = max(0, min(x, width - 1))
            y = max(0, min(y, height - 1))
            w = max(1, min(w, width - x))
            h = max(1, min(h, height - y))
            boxes.append([x, y, w, h])
            confidences.append(confidence)
            class_ids.append(class_id)

        indices = cv2.dnn.NMSBoxes(
            boxes,
            confidences,
            self.settings.confidence_threshold,
            self.settings.nms_threshold,
        )
        detections: list[Detection] = []
        for idx in np.array(indices).flatten().tolist():
            label = self.labels[class_ids[idx]] if class_ids[idx] < len(self.labels) else str(class_ids[idx])
            detections.append(
                Detection(
                    label=label,
                    confidence=confidences[idx],
                    bbox=tuple(boxes[idx]),  # type: ignore[arg-type]
                    source="onnx",
                    metadata={"class_id": class_ids[idx]},
                )
            )
        return detections

    def _detect_hog_and_motion(
        self,
        frame: np.ndarray,
        motion_regions: list[MotionRegion],
    ) -> list[Detection]:
        detections: list[Detection] = []
        if self._hog is not None:
            boxes, weights = self._hog.detectMultiScale(
                frame,
                winStride=(8, 8),
                padding=(8, 8),
                scale=1.05,
            )
            for box, weight in zip(boxes, weights):
                confidence = float(np.clip(weight, 0.25, 1.0))
                if confidence >= 0.25:
                    x, y, w, h = [int(v) for v in box]
                    detections.append(
                        Detection(
                            label="person",
                            confidence=confidence,
                            bbox=(x, y, w, h),
                            source="hog",
                        )
                    )

        frame_area = max(frame.shape[0] * frame.shape[1], 1)
        for region in motion_regions[: self.settings.max_motion_detections]:
            x, y, w, h = region.bbox
            if w < 32 or h < 48:
                continue
            aspect = h / max(w, 1)
            label = "person" if 1.2 <= aspect <= 4.6 and h > 110 else "moving object"
            confidence = min(0.85, max(0.30, region.area / (frame_area * 0.10)))
            detections.append(
                Detection(
                    label=label,
                    confidence=confidence,
                    bbox=(x, y, w, h),
                    source="motion",
                    metadata={"area": region.area},
                )
            )
        return _dedupe_detections(detections)

    @staticmethod
    def _load_labels(path: Path) -> list[str]:
        if not path.exists():
            LOGGER.warning("Label file not found: %s", path)
            return []
        return [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _dedupe_detections(detections: list[Detection]) -> list[Detection]:
    kept: list[Detection] = []
    for detection in sorted(detections, key=lambda item: item.confidence, reverse=True):
        if any(_iou(detection.bbox, other.bbox) > 0.45 for other in kept):
            continue
        kept.append(detection)
    return kept


def _iou(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ax2, ay2 = ax + aw, ay + ah
    bx2, by2 = bx + bw, by + bh
    inter_x1 = max(ax, bx)
    inter_y1 = max(ay, by)
    inter_x2 = min(ax2, bx2)
    inter_y2 = min(ay2, by2)
    inter_w = max(0, inter_x2 - inter_x1)
    inter_h = max(0, inter_y2 - inter_y1)
    inter_area = inter_w * inter_h
    union = aw * ah + bw * bh - inter_area
    return inter_area / max(union, 1)
