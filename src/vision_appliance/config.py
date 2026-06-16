from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .models import Zone


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return float(raw)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw == "":
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _parse_zones(raw: str) -> list[Zone]:
    zones: list[Zone] = []
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        name, _, coords = item.partition(":")
        values = [part.strip() for part in coords.replace("|", ";").replace("/", ";").split(";")]
        try:
            x1, y1, x2, y2 = [float(value) for value in values]
        except ValueError:
            continue
        if name:
            zones.append(Zone(name=name, x1=x1, y1=y1, x2=x2, y2=y2))
    return zones


def _parse_label_set(raw: str) -> set[str]:
    raw = raw.strip()
    if not raw or raw == "*":
        return set()
    return {item.strip().lower() for item in raw.split(",") if item.strip()}


@dataclass(frozen=True)
class Settings:
    camera_index: int = 0
    camera_source: str | None = None
    frame_width: int = 1280
    frame_height: int = 720
    fps: int = 15
    data_dir: Path = Path("./data")
    clip_seconds_before: int = 4
    clip_seconds_after: int = 8
    retention_days: int = 14
    detection_interval: int = 5
    confidence_threshold: float = 0.45
    nms_threshold: float = 0.45
    min_motion_area: int = 5000
    motion_merge_pixels: int = 36
    max_motion_detections: int = 3
    emit_generic_motion_events: bool = False
    onnx_model: Path | None = None
    onnx_input_size: int = 640
    labels_file: Path = Path("./models/coco.names")
    detect_labels: set[str] = field(default_factory=set)
    zones: list[Zone] = field(default_factory=list)
    llm_provider: str = "rules"
    ollama_url: str = "http://127.0.0.1:11434/api/generate"
    ollama_model: str = "llama3.1"
    openai_model: str = "gpt-4.1-mini"
    save_debug_frames: bool = True
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "info"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "events.db"

    @property
    def clips_dir(self) -> Path:
        return self.data_dir / "clips"

    @property
    def frames_dir(self) -> Path:
        return self.data_dir / "frames"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    def ensure_directories(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.clips_dir.mkdir(parents=True, exist_ok=True)
        self.frames_dir.mkdir(parents=True, exist_ok=True)
        self.reports_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> Settings:
    raw_model = os.getenv("VISION_ONNX_MODEL", "").strip()
    raw_detect_labels = os.getenv(
        "VISION_DETECT_LABELS",
        "person,backpack,handbag,suitcase,laptop,cell phone,bottle,cup,book,keyboard,mouse,"
        "remote,chair,tv",
    )
    raw_zones = os.getenv(
        "VISION_ZONES",
        "workbench:0.58;0.45;0.98;0.98,entry:0.00;0.15;0.22;0.95",
    )
    return Settings(
        camera_index=_env_int("VISION_CAMERA_INDEX", 0),
        camera_source=os.getenv("VISION_CAMERA_SOURCE", "").strip() or None,
        frame_width=_env_int("VISION_FRAME_WIDTH", 1280),
        frame_height=_env_int("VISION_FRAME_HEIGHT", 720),
        fps=_env_int("VISION_FPS", 15),
        data_dir=Path(os.getenv("VISION_DATA_DIR", "./data")),
        clip_seconds_before=_env_int("VISION_CLIP_SECONDS_BEFORE", 4),
        clip_seconds_after=_env_int("VISION_CLIP_SECONDS_AFTER", 8),
        retention_days=_env_int("VISION_RETENTION_DAYS", 14),
        detection_interval=max(1, _env_int("VISION_DETECTION_INTERVAL", 5)),
        confidence_threshold=_env_float("VISION_CONFIDENCE_THRESHOLD", 0.45),
        nms_threshold=_env_float("VISION_NMS_THRESHOLD", 0.45),
        min_motion_area=_env_int("VISION_MIN_MOTION_AREA", 5000),
        motion_merge_pixels=_env_int("VISION_MOTION_MERGE_PIXELS", 36),
        max_motion_detections=_env_int("VISION_MAX_MOTION_DETECTIONS", 3),
        emit_generic_motion_events=_env_bool("VISION_EMIT_GENERIC_MOTION_EVENTS", False),
        onnx_model=Path(raw_model) if raw_model else None,
        onnx_input_size=_env_int("VISION_ONNX_INPUT_SIZE", 640),
        labels_file=Path(os.getenv("VISION_LABELS_FILE", "./models/coco.names")),
        detect_labels=_parse_label_set(raw_detect_labels),
        zones=_parse_zones(raw_zones),
        llm_provider=os.getenv("VISION_LLM_PROVIDER", "rules").lower(),
        ollama_url=os.getenv("VISION_OLLAMA_URL", "http://127.0.0.1:11434/api/generate"),
        ollama_model=os.getenv("VISION_OLLAMA_MODEL", "llama3.1"),
        openai_model=os.getenv("VISION_OPENAI_MODEL", "gpt-4.1-mini"),
        save_debug_frames=_env_bool("VISION_SAVE_DEBUG_FRAMES", True),
        host=os.getenv("VISION_HOST", "0.0.0.0"),
        port=_env_int("VISION_PORT", 8080),
        log_level=os.getenv("VISION_LOG_LEVEL", "info"),
    )
