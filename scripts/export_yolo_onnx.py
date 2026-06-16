from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a YOLO model to ONNX for the Pi appliance.")
    parser.add_argument("--model", default="yolov8n.pt", help="Ultralytics model name or .pt path")
    parser.add_argument("--output", default="models/yolov8n.onnx", help="Destination ONNX path")
    parser.add_argument("--imgsz", type=int, default=640)
    parser.add_argument("--opset", type=int, default=12)
    parser.add_argument("--simplify", action="store_true")
    args = parser.parse_args()

    local_config = Path(".ultralytics").resolve()
    local_config.mkdir(parents=True, exist_ok=True)
    os.environ.setdefault("YOLO_CONFIG_DIR", str(local_config))
    os.environ.setdefault("MPLCONFIGDIR", str(local_config / "matplotlib"))

    try:
        from ultralytics import YOLO
    except ImportError as exc:
        raise SystemExit(
            "Install export dependencies first: pip install -r requirements-yolo-export.txt"
        ) from exc

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    model = YOLO(args.model)
    exported = Path(
        model.export(
            format="onnx",
            imgsz=args.imgsz,
            opset=args.opset,
            simplify=args.simplify,
            dynamic=False,
        )
    )
    if exported.resolve() != output.resolve():
        shutil.copy2(exported, output)
    print(output.resolve())


if __name__ == "__main__":
    main()
