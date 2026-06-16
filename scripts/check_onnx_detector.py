from __future__ import annotations

import argparse
import sys
import urllib.request
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from vision_appliance.config import Settings  # noqa: E402
from vision_appliance.object_detector import ObjectDetector  # noqa: E402


def read_frame(source: str):
    if source.startswith(("http://", "https://")):
        with urllib.request.urlopen(source, timeout=10) as response:
            data = np.frombuffer(response.read(), dtype=np.uint8)
        frame = cv2.imdecode(data, cv2.IMREAD_COLOR)
        if frame is None:
            raise RuntimeError(f"Could not decode image from {source}")
        return frame

    path = Path(source)
    frame = cv2.imread(str(path))
    if frame is None:
        raise RuntimeError(f"Could not read image: {path}")
    return frame


def parse_labels(raw: str) -> set[str]:
    if not raw or raw == "*":
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a YOLO ONNX detector against one image/frame.")
    parser.add_argument("--model", required=True)
    parser.add_argument("--labels", default="models/coco.names")
    parser.add_argument("--source", required=True, help="Image path or URL such as http://host/latest.jpg")
    parser.add_argument("--confidence", type=float, default=0.35)
    parser.add_argument("--input-size", type=int, default=640)
    parser.add_argument("--detect-labels", default="*")
    args = parser.parse_args()

    settings = Settings(
        onnx_model=Path(args.model),
        labels_file=Path(args.labels),
        confidence_threshold=args.confidence,
        onnx_input_size=args.input_size,
        detect_labels=parse_labels(args.detect_labels),
    )
    detector = ObjectDetector(settings)
    frame = read_frame(args.source)
    detections = detector.detect(frame, motion_regions=[])

    if not detections:
        print("No detections")
        return

    for detection in detections:
        x, y, w, h = detection.bbox
        print(
            f"{detection.label:16s} conf={detection.confidence:.2f} "
            f"bbox=({x},{y},{w},{h}) source={detection.source}"
        )


if __name__ == "__main__":
    main()
