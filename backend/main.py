"""
Social Automation Studio — FastAPI entrypoint.

Boots standalone with an empty .env. Routers from later phases are loaded
defensively so a missing/unfinished module never blocks startup.
"""
from __future__ import annotations

import asyncio
import importlib
import logging
import os
import shutil

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
# Compress JSON responses — list endpoints (jobs/dashboard) are mostly text and
# shrink ~5-10x over the wire, a big win for the cross-origin Vercel frontend.
app.add_middleware(GZipMiddleware, minimum_size=1000)


class _StripApiPrefix:
    """
    Pure-ASGI middleware: rewrites '/api/...' -> '/...' before routing.

    Why pure ASGI (not BaseHTTPMiddleware): Starlette's BaseHTTPMiddleware
    `call_next` ignores any scope you mutate on the Request — it replays the
    *original* scope captured in its closure, so a path rewrite there is a no-op.
    Mutating the ASGI `scope` dict directly here is the only thing routing sees.

    The Vercel build baked VITE_API_URL=.../api, so every call arrives as
    /api/<route>; backend routes live at root. This strips the prefix for both
    HTTP and WebSocket so /api/dashboard and /dashboard both resolve.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            path = scope.get("path", "")
            if path.startswith("/api/"):
                new_path = path[4:]  # "/api/jobs" -> "/jobs"
                scope = dict(scope)
                scope["path"] = new_path
                if scope.get("raw_path"):
                    scope["raw_path"] = new_path.encode()
        await self.app(scope, receive, send)


app.add_middleware(_StripApiPrefix)

# Routers added across phases. Missing modules are skipped with a log line.
_ROUTER_MODULES = [
    "backend.routers.jobs",
    "backend.routers.remix",
    "backend.routers.accounts",
    "backend.routers.workspaces",
    "backend.routers.schedule",
    "backend.routers.dashboard",
    "backend.routers.analytics",
    "backend.routers.themes",
    "backend.routers.settings",
    "backend.routers.drive_library",
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

# Built frontend (SPA) served from THIS service — one deploy updates everything,
# no separate host / deploy limit. Built bundle lives in frontend/dist.
from backend.config import ROOT_DIR as _ROOT_DIR  # noqa: E402

_FRONTEND_DIST = _ROOT_DIR / "frontend" / "dist"
_RESERVED_PREFIXES = {"api", "files", "media", "health", "ws", "docs", "openapi.json", "redoc"}


# A PROCESSING render orphan auto-resumes at most this many times across restarts;
# beyond it (i.e. resuming it killed the process again) it parks for manual Retry so
# a heavy/OOM render can't crash-loop the container.
_ORPHAN_RESUME_MAX = 1
# Distinctive phrase MUST also be listed in scheduler._NO_AUTO_RETRY_MARKERS so the
# resurrection cron never auto-retries an interrupted render (that would reintroduce
# intermittent OOM downtime). Manual Retry still works regardless.
_RENDER_INTERRUPTED_MSG = (
    "Render interrompido (reinício do servidor) — use Retry para gerar de novo."
)


def _safe_boot() -> bool:
    """SAFE_BOOT=1 makes startup NOT re-dispatch heavy renders — the recovery valve
    for an OOM crash-loop (an interrupted video job that re-OOMs the container on every
    restart until Railway gives up). Publish-status recovery still runs (no duplicates);
    only the re-render of PROCESSING/QUEUED jobs is held so the API boots clean."""
    return os.getenv("SAFE_BOOT", "").strip().lower() in {"1", "true", "yes", "on"}


def _recover_orphan_jobs() -> None:
    """
    In-process pipeline (no Redis): any job left in PUBLISHING/PROCESSING when
    the server starts is an ORPHAN — its task died on the restart.

    - PROCESSING interrupted -> ERROR (render half-done; use Retry).
    - PUBLISHING: decided by publish_status.youtube to AVOID DUPLICATE uploads:
        * already has ok+video_id   -> PUBLISHED (it's on the channel; never resend).
        * was mid-upload ("uploading") -> ERROR + "verifique o canal" (the video MAY
          already be up; do NOT auto-republish — that's how duplicates happened).
        * upload never started      -> APPROVED (safe to publish on the next tick).

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
            safe = _safe_boot()
            recovered = 0
            for job in orphans:
                if job.status == JobStatus.PROCESSING:
                    # A render that was running when the process died is the PRIME
                    # SUSPECT for the death (an OOM render that blew the RAM ceiling).
                    # Auto-resuming it on EVERY boot is exactly what crash-looped the
                    # whole service. So: resume it AT MOST ONCE (covers a benign
                    # deploy/restart — "a deploy shouldn't cost the video"); if it
                    # comes back as an orphan again, park it for MANUAL Retry with a
                    # message the scheduler WON'T auto-resurrect (see _NO_AUTO_RETRY_
                    # MARKERS) — so a heavy/OOM render can never loop the container.
                    # SAFE_BOOT parks immediately (no resume at all).
                    if (not safe) and (job.retry_count or 0) < _ORPHAN_RESUME_MAX:
                        job.retry_count = (job.retry_count or 0) + 1
                        job.status = JobStatus.QUEUED
                        job.error_message = None
                        job.current_agent = None
                        job.progress = 0
                    else:
                        job.status = JobStatus.ERROR
                        job.error_message = _RENDER_INTERRUPTED_MSG
                else:  # PUBLISHING orphan — never blindly republish (duplicate risk)
                    ps = job.publish_status if isinstance(job.publish_status, dict) else {}
                    yt = ps.get("youtube") or {}
                    if yt.get("ok") and yt.get("video_id"):
                        job.status = JobStatus.PUBLISHED  # already live — never resend
                    elif yt.get("status") == "uploading":
                        job.status = JobStatus.ERROR
                        job.error_message = (
                            "Publicação interrompida por reinício — o vídeo PODE já estar no "
                            "canal. Verifique o YouTube antes de usar Retry (evita duplicar)."
                        )
                    else:
                        job.status = JobStatus.APPROVED  # upload never started — safe
                recovered += 1
            if recovered:
                db.commit()
            logger.info("Recuperação de órfãos: %d job(s) ajustado(s) ao iniciar.", recovered)
        finally:
            db.close()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Recuperação de órfãos falhou (ignorada): %s", exc)


def _redispatch_queued_jobs() -> None:
    """
    In-process mode only: re-dispatch jobs left in QUEUED.

    A QUEUED job that isn't running means its dispatch never completed (e.g. it was
    enqueued to a Celery queue with no worker before USE_CELERY was disabled, or the
    process restarted before pickup). Celery mode is skipped — a real worker owns the
    queue there. Bounded to avoid a thundering herd on boot.
    """
    if settings.use_celery:
        return
    if _safe_boot():
        logger.warning("SAFE_BOOT ativo — re-dispatch de jobs QUEUED ignorado no boot.")
        return
    try:
        from backend.database import SessionLocal
        from backend.models import JobStatus, VideoJob
        from backend.pipeline.dispatch import dispatch_job

        db = SessionLocal()
        try:
            stuck = (
                db.query(VideoJob)
                .filter(VideoJob.status == JobStatus.QUEUED)
                .order_by(VideoJob.created_at.asc())
                .limit(200)
                .all()
            )
            ids = [j.id for j in stuck]
        finally:
            db.close()
        for jid in ids:
            dispatch_job(jid)
        if ids:
            logger.info("Re-disparados %d job(s) presos em QUEUED (modo in-process).", len(ids))
    except Exception as exc:  # noqa: BLE001
        logger.warning("Re-dispatch de QUEUED falhou (ignorado): %s", exc)


@app.on_event("startup")
async def _on_startup() -> None:
    settings.ensure_dirs()
    try:
        from backend.database import Base, engine
        Base.metadata.create_all(bind=engine)
        logger.info("Tabelas criadas/verificadas no banco.")
    except Exception as exc:  # noqa: BLE001
        logger.warning("create_all falhou: %s", exc)
    try:
        from backend.database import ensure_columns, ensure_indexes

        ensure_columns()  # idempotent: adds new columns (e.g. video_format) to old DBs
        ensure_indexes()  # idempotent: indexes the scheduler's hot query paths
    except Exception as exc:  # noqa: BLE001
        logger.warning("ensure_columns/indexes falhou: %s", exc)
    _recover_orphan_jobs()
    _redispatch_queued_jobs()
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
    from fastapi.responses import FileResponse

    idx = _FRONTEND_DIST / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
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
    # Path-segment containment (NOT string prefix): str.startswith would let a
    # sibling dir with a shared prefix ('/app/output-secret' vs '/app/output')
    # escape the allow-list.
    if not any(target == root or root in target.parents for root in allowed):
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


@app.get("/terms")
async def terms_of_service():
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Terms of Service – SAS Automation</title>
<style>body{font-family:sans-serif;max-width:800px;margin:60px auto;padding:0 20px;line-height:1.6}h1{font-size:1.8rem}</style>
</head><body>
<h1>Terms of Service</h1>
<p><strong>Last updated: June 2026</strong></p>
<p>SAS Automation ("Service") is a social media content management platform that allows users to schedule and publish videos to TikTok, Instagram, and YouTube via their official APIs.</p>
<h2>1. Use of the Service</h2>
<p>By using this Service, you agree to comply with the Terms of Service of all connected platforms (TikTok, Instagram, YouTube). The Service acts on your behalf using OAuth authorization — your credentials are never stored.</p>
<h2>2. Content</h2>
<p>You are solely responsible for the content you publish through this Service. You must own or have the rights to any content submitted.</p>
<h2>3. API Usage</h2>
<p>This Service uses the TikTok Content Posting API, Meta Graph API, and YouTube Data API solely to post content authorized by you via OAuth.</p>
<h2>4. Limitation of Liability</h2>
<p>The Service is provided "as is". We are not liable for any damages arising from use of the Service.</p>
<h2>5. Contact</h2>
<p>For questions, contact: orionreidas@proton.me</p>
</body></html>""")


@app.get("/privacy")
async def privacy_policy():
    from fastapi.responses import HTMLResponse
    return HTMLResponse("""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
<title>Privacy Policy – SAS Automation</title>
<style>body{font-family:sans-serif;max-width:800px;margin:60px auto;padding:0 20px;line-height:1.6}h1{font-size:1.8rem}</style>
</head><body>
<h1>Privacy Policy</h1>
<p><strong>Last updated: June 2026</strong></p>
<p>SAS Automation respects your privacy. This policy explains what data we collect and how we use it.</p>
<h2>1. Data We Collect</h2>
<p>We collect only the OAuth tokens necessary to post content on your behalf to TikTok, Instagram, and YouTube. No personal data beyond platform usernames and access tokens is stored.</p>
<h2>2. How We Use Data</h2>
<p>OAuth tokens are used exclusively to publish content you have scheduled through the Service. We do not share your data with third parties.</p>
<h2>3. Data Retention</h2>
<p>OAuth tokens are stored encrypted and can be revoked at any time from the connected platform's settings or within this Service.</p>
<h2>4. Third-Party APIs</h2>
<p>This Service integrates with TikTok, Meta (Instagram), and Google (YouTube) APIs. Their respective privacy policies apply to data processed by their platforms.</p>
<h2>5. Contact</h2>
<p>For privacy requests, contact: orionreidas@proton.me</p>
</body></html>""")


@app.get("/tiktok{token}.txt")
async def tiktok_site_verification(token: str):
    """TikTok URL-prefix ownership check. TikTok serves a file named
    tiktok<token>.txt whose body is `tiktok-developers-site-verification=<token>`.
    Reconstructing it from the path means any current/future token verifies."""
    from fastapi.responses import PlainTextResponse
    return PlainTextResponse(f"tiktok-developers-site-verification={token}")


# SPA catch-all — MUST be last so real API/util routes match first. Serves a built
# asset if it exists, else index.html (client-side routing handles /queue, etc.).
@app.get("/{full_path:path}")
async def _spa(full_path: str):
    from fastapi import HTTPException
    from fastapi.responses import FileResponse

    if full_path.split("/")[0] in _RESERVED_PREFIXES:
        raise HTTPException(404, "Not Found")
    candidate = _FRONTEND_DIST / full_path
    if full_path and candidate.is_file():
        return FileResponse(str(candidate))
    idx = _FRONTEND_DIST / "index.html"
    if idx.is_file():
        return FileResponse(str(idx))
    raise HTTPException(404, "frontend não compilado")
