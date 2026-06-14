"""
Social Automation Studio — FastAPI entrypoint.

Boots standalone with an empty .env. Routers from later phases are loaded
defensively so a missing/unfinished module never blocks startup.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import shutil

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from backend import events
from backend.config import settings
from backend.database import engine

logging.basicConfig(
    level=settings.log_level,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("studio")

app = FastAPI(title="Social Automation Studio", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers added across phases. Missing modules are skipped with a log line.
_ROUTER_MODULES = [
    "backend.routers.jobs",
    "backend.routers.remix",
    "backend.routers.accounts",
    "backend.routers.workspaces",
    "backend.routers.schedule",
    "backend.routers.dashboard",
    "backend.routers.analytics",
]


def _load_routers() -> None:
    for mod_path in _ROUTER_MODULES:
        try:
            mod = importlib.import_module(mod_path)
            app.include_router(mod.router)
            logger.info("Loaded router: %s", mod_path)
        except ModuleNotFoundError:
            logger.debug("Router not present yet: %s", mod_path)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Router %s failed to load: %s", mod_path, exc)


_load_routers()

# Serve generated videos/thumbnails for in-app previews.
settings.ensure_dirs()
app.mount("/files", StaticFiles(directory=str(settings.abs_path(settings.output_dir))), name="files")


def _recover_orphan_jobs() -> None:
    """
    In-process pipeline (no Redis): any job left in PUBLISHING/PROCESSING when
    the server starts is an ORPHAN — its task died on the restart.

    - PUBLISHING (or approval_status == 'approved') -> APPROVED (ready to republish).
    - PROCESSING interrupted -> ERROR with an actionable message (use Retry).

    Wrapped in try/except so a recovery failure never blocks boot.
    """
    try:
        from backend.database import SessionLocal
        from backend.models import JobStatus, VideoJob

        db = SessionLocal()
        try:
            orphans = (
                db.query(VideoJob)
                .filter(VideoJob.status.in_([JobStatus.PUBLISHING, JobStatus.PROCESSING]))
                .all()
            )
            recovered = 0
            for job in orphans:
                if job.status == JobStatus.PUBLISHING or job.approval_status == "approved":
                    job.status = JobStatus.APPROVED
                else:
                    job.status = JobStatus.ERROR
                    job.error_message = (
                        "Interrompido por reinicialização do servidor — use Retry"
                    )
                recovered += 1
            if recovered:
                db.commit()
            logger.info("Recuperação de órfãos: %d job(s) ajustado(s) ao iniciar.", recovered)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recuperação de órfãos falhou (ignorada): %s", exc)


@app.on_event("startup")
async def _on_startup() -> None:
    settings.ensure_dirs()
    _recover_orphan_jobs()
    events.set_main_loop(asyncio.get_running_loop())
    # Best-effort live-event relay; no-op if Redis is down.
    asyncio.create_task(events.redis_listener())

    # Periodic jobs (trending/quota/heartbeat/analytics). Disable with STUDIO_NO_SCHEDULER=1.
    import os

    if os.getenv("STUDIO_NO_SCHEDULER") != "1":
        try:
            from backend.scheduler import start_scheduler

            start_scheduler()
        except Exception as exc:  # noqa: BLE001
            logger.warning("Scheduler not started: %s", exc)

    logger.info("Social Automation Studio API started (production=%s).", settings.is_production)


@app.on_event("shutdown")
async def _on_shutdown() -> None:
    try:
        from backend.scheduler import shutdown_scheduler

        shutdown_scheduler()
    except Exception:
        pass


@app.get("/")
async def root():
    return {"service": "Social Automation Studio", "status": "ok", "docs": "/docs"}


@app.get("/media")
async def media(path: str):
    """Stream a generated artifact (video/thumbnail) — restricted to output/tmp/cache/assets."""
    from pathlib import Path

    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    target = Path(path).resolve()
    allowed = [
        settings.abs_path(settings.output_dir).resolve(),
        settings.abs_path(settings.temp_dir).resolve(),
        settings.abs_path(settings.cache_dir).resolve(),
        settings.abs_path(settings.assets_dir).resolve(),
    ]
    if not any(str(target).startswith(str(root)) for root in allowed):
        raise HTTPException(403, "caminho não permitido")
    if not target.is_file():
        raise HTTPException(404, "arquivo não encontrado")
    return FileResponse(str(target))


@app.get("/health")
async def health():
    """Service health snapshot — used by Railway healthcheck and the Settings page."""
    checks: dict[str, dict] = {}

    # Database
    try:
        from sqlalchemy import text

        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["database"] = {"status": "green", "detail": settings.sqlalchemy_url.split("://")[0]}
    except Exception as exc:
        checks["database"] = {"status": "red", "detail": str(exc)}

    # Redis
    try:
        import redis

        r = redis.from_url(settings.redis_url, decode_responses=True, socket_connect_timeout=2)
        r.ping()
        checks["redis"] = {"status": "green", "detail": "reachable"}
    except Exception:
        checks["redis"] = {"status": "yellow", "detail": "unreachable (Celery disabled; in-process mode)"}

    # FFmpeg
    if shutil.which("ffmpeg"):
        checks["ffmpeg"] = {"status": "green", "detail": "found"}
    else:
        checks["ffmpeg"] = {"status": "red", "detail": "not on PATH"}

    # edge-tts importability
    try:
        importlib.import_module("edge_tts")
        checks["edge_tts"] = {"status": "green", "detail": "import ok"}
    except Exception:
        checks["edge_tts"] = {"status": "yellow", "detail": "not installed"}

    # LLM keys present?
    checks["groq"] = {"status": "green" if settings.groq_api_key else "yellow",
                      "detail": "key set" if settings.groq_api_key else "no key (set GROQ_API_KEY)"}

    overall = "green"
    if any(c["status"] == "red" for c in checks.values()):
        overall = "red"
    elif any(c["status"] == "yellow" for c in checks.values()):
        overall = "yellow"

    return {"status": overall, "checks": checks}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await events.manager.connect(ws)
    try:
        await ws.send_json({"type": "connected", "message": "AgentLog stream live"})
        while True:
            # Keep the socket open; we don't expect inbound messages.
            await ws.receive_text()
    except WebSocketDisconnect:
        events.manager.disconnect(ws)
    except Exception:
        events.manager.disconnect(ws)
