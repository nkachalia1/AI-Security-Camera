from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import Settings, load_settings
from .database import EventStore
from .llm_summarizer import IncidentSummarizer
from .pipeline import VisionPipeline, safe_child_path

LOGGER = logging.getLogger(__name__)


class ObjectLabelRequest(BaseModel):
    label: str = Field(min_length=1, max_length=64)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    settings.ensure_directories()
    store = EventStore(settings.db_path)
    store.initialize()
    pipeline = VisionPipeline(settings, store)
    summarizer = IncidentSummarizer(settings)
    static_dir = Path(__file__).parent / "static"

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        pipeline.start()
        try:
            yield
        finally:
            pipeline.stop()

    app = FastAPI(title="AI Security Camera", version="0.1.0", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=static_dir), name="static")
    app.state.settings = settings
    app.state.store = store
    app.state.pipeline = pipeline

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> FileResponse:
        return FileResponse(static_dir / "index.html")

    @app.get("/health")
    def health() -> dict:
        status = pipeline.status()
        system = pipeline.system_health()
        return {
            "ok": bool(status["running"]) and status.get("last_error") is None,
            "status": status,
            "system": system,
        }

    @app.get("/status")
    def status() -> dict:
        payload = pipeline.status()
        payload["system"] = pipeline.system_health()
        return payload

    @app.post("/pipeline/start")
    def start_pipeline() -> dict:
        pipeline.start()
        return {"running": True}

    @app.post("/pipeline/stop")
    def stop_pipeline() -> dict:
        pipeline.stop()
        return {"running": False}

    @app.get("/config")
    def config() -> dict:
        return {
            "camera_index": settings.camera_index,
            "camera_source": settings.camera_source,
            "frame_width": settings.frame_width,
            "frame_height": settings.frame_height,
            "fps": settings.fps,
            "detection_interval": settings.detection_interval,
            "clip_seconds_before": settings.clip_seconds_before,
            "clip_seconds_after": settings.clip_seconds_after,
            "clip_encoder": settings.clip_encoder,
            "history_limit": settings.history_limit,
            "onnx_model": str(settings.onnx_model) if settings.onnx_model else None,
            "onnx_input_size": settings.onnx_input_size,
            "detect_labels": sorted(settings.detect_labels),
            "zones": [zone.as_dict() for zone in settings.zones],
            "thermal_guard": {
                "enabled": settings.thermal_guard_enabled,
                "sample_seconds": settings.thermal_sample_seconds,
                "warm_c": settings.thermal_warm_c,
                "hot_c": settings.thermal_hot_c,
                "critical_c": settings.thermal_critical_c,
                "warm_fps": settings.thermal_warm_fps,
                "hot_fps": settings.thermal_hot_fps,
                "critical_fps": settings.thermal_critical_fps,
                "warm_detection_interval": settings.thermal_warm_detection_interval,
                "hot_detection_interval": settings.thermal_hot_detection_interval,
                "critical_detection_interval": settings.thermal_critical_detection_interval,
            },
            "llm_provider": settings.llm_provider,
            "retention_days": settings.retention_days,
        }

    @app.get("/events")
    def events(
        limit: int | None = Query(default=None, ge=1, le=500),
        event_type: str | None = None,
    ) -> list[dict]:
        return pipeline.apply_object_labels_to_events(
            store.list_events(limit=limit or settings.history_limit, event_type=event_type)
        )

    @app.get("/object-labels")
    def object_labels() -> list[dict]:
        return pipeline.object_label_profiles()

    @app.post("/objects/{track_id}/label")
    def label_object(track_id: int, payload: ObjectLabelRequest) -> dict:
        try:
            result = pipeline.label_track(track_id, payload.label)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(status_code=404, detail="Object track not found")
        return result

    @app.delete("/object-labels/{profile_id}")
    def delete_object_label(profile_id: int) -> dict:
        if not pipeline.delete_object_label_profile(profile_id):
            raise HTTPException(status_code=404, detail="Object label profile not found")
        return {"deleted": True, "id": profile_id}

    @app.post("/object-labels/reset")
    def reset_object_labels() -> dict:
        return pipeline.reset_object_labels()

    @app.get("/latest-frame")
    def latest_frame(annotated: bool = True) -> Response:
        frame = pipeline.latest_frame(annotated=annotated)
        if frame is None:
            raise HTTPException(status_code=503, detail="No camera frame is available yet")
        return Response(content=frame, media_type="image/jpeg")

    @app.get("/stream")
    async def stream() -> StreamingResponse:
        async def frames() -> AsyncIterator[bytes]:
            last = b""
            while True:
                frame = pipeline.latest_frame(annotated=True)
                if frame and frame != last:
                    last = frame
                    yield b"--frame\r\nContent-Type: image/jpeg\r\n\r\n" + frame + b"\r\n"
                await asyncio.sleep(0.10)

        return StreamingResponse(frames(), media_type="multipart/x-mixed-replace; boundary=frame")

    @app.get("/clips")
    def clips() -> list[dict]:
        return _list_media(
            settings.clips_dir,
            suffixes={".mp4", ".avi", ".mov"},
            keep=settings.history_limit,
        )

    @app.get("/clips/{name}")
    def clip_file(name: str) -> FileResponse:
        return _media_file(settings.clips_dir, name)

    @app.get("/frames")
    def frames() -> list[dict]:
        return _list_media(settings.frames_dir, suffixes={".jpg", ".jpeg", ".png"})

    @app.get("/frames/{name}")
    def frame_file(name: str) -> FileResponse:
        return _media_file(settings.frames_dir, name)

    @app.get("/reports")
    def reports(limit: int | None = Query(default=None, ge=1, le=100)) -> list[dict]:
        return store.list_reports(limit=limit or settings.history_limit)

    @app.post("/reports/generate")
    def generate_report(limit: int = Query(default=25, ge=1, le=100)) -> dict:
        recent = pipeline.recent_events_for_report(limit=limit)
        report = summarizer.summarize(recent)
        report_id = store.insert_report(
            title=report.title,
            body=report.body,
            event_ids=report.event_ids,
            created_at=report.created_at,
        )
        store.prune_reports(settings.history_limit)
        return {
            "id": report_id,
            "title": report.title,
            "body": report.body,
            "event_ids": report.event_ids,
            "created_at": report.created_at,
        }

    return app


def _list_media(root: Path, suffixes: set[str], keep: int | None = None) -> list[dict]:
    root.mkdir(parents=True, exist_ok=True)
    items = []
    for path in sorted(root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True):
        if not path.is_file() or path.suffix.lower() not in suffixes:
            continue
        stat = path.stat()
        items.append(
            {
                "name": path.name,
                "size": stat.st_size,
                "modified_at": stat.st_mtime,
                "url": f"/{root.name}/{path.name}",
            }
        )
    if keep is not None:
        for item in items[keep:]:
            safe_child_path(root, item["name"]).unlink(missing_ok=True)
        return items[:keep]
    return items


def _media_file(root: Path, name: str) -> FileResponse:
    try:
        path = safe_child_path(root, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path, media_type=_media_type(path))


def _media_type(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix == ".mp4":
        return "video/mp4"
    if suffix == ".mov":
        return "video/quicktime"
    if suffix == ".avi":
        return "video/x-msvideo"
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    return None


app = create_app()
