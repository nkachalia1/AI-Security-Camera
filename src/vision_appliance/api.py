from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, HTMLResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles

from .config import Settings, load_settings
from .database import EventStore
from .llm_summarizer import IncidentSummarizer
from .pipeline import VisionPipeline, safe_child_path
from .system_health import get_system_health

LOGGER = logging.getLogger(__name__)


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
        system = get_system_health()
        return {
            "ok": bool(status["running"]) and status.get("last_error") is None,
            "status": status,
            "system": system,
        }

    @app.get("/status")
    def status() -> dict:
        payload = pipeline.status()
        payload["system"] = get_system_health()
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
            "onnx_model": str(settings.onnx_model) if settings.onnx_model else None,
            "onnx_input_size": settings.onnx_input_size,
            "detect_labels": sorted(settings.detect_labels),
            "zones": [zone.as_dict() for zone in settings.zones],
            "llm_provider": settings.llm_provider,
            "retention_days": settings.retention_days,
        }

    @app.get("/events")
    def events(
        limit: int = Query(default=100, ge=1, le=500),
        event_type: str | None = None,
    ) -> list[dict]:
        return store.list_events(limit=limit, event_type=event_type)

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
        return _list_media(settings.clips_dir, suffixes={".mp4", ".avi", ".mov"})

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
    def reports(limit: int = Query(default=20, ge=1, le=100)) -> list[dict]:
        return store.list_reports(limit=limit)

    @app.post("/reports/generate")
    def generate_report(limit: int = Query(default=25, ge=1, le=100)) -> dict:
        recent = store.recent_events(limit=limit)
        report = summarizer.summarize(recent)
        report_id = store.insert_report(
            title=report.title,
            body=report.body,
            event_ids=report.event_ids,
            created_at=report.created_at,
        )
        return {
            "id": report_id,
            "title": report.title,
            "body": report.body,
            "event_ids": report.event_ids,
            "created_at": report.created_at,
        }

    return app


def _list_media(root: Path, suffixes: set[str]) -> list[dict]:
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
    return items


def _media_file(root: Path, name: str) -> FileResponse:
    try:
        path = safe_child_path(root, name)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=404, detail="Media not found")
    return FileResponse(path)


app = create_app()
