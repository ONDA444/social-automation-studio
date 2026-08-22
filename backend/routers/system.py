"""System-wide self-heal — 'Corrigir sistema' does everything a person would
do by hand to bring the whole pipeline back after ANY kind of outage: not
just requeue failed videos, but reconnect-driven job resumption for every
connected platform, an immediate stuck-job sweep, disk cleanup, and
restarting a dead in-process scheduler. This is the single action behind the
Config page's big 'Corrigir sistema' button.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.models import PlatformAccount
from backend.routers.jobs import fix_errors

logger = logging.getLogger("studio")

router = APIRouter(prefix="/system", tags=["system"])


_SEVERITY_BY_STATUS = {
    "error": "ERROR",
    "retry": "WARNING",
    "started": "INFO",
    "progress": "INFO",
    "completed": "SUCCESS",
}


@router.get("/logs")
def agent_logs(
    level: str | None = Query(None),
    agent: str | None = Query(None),
    job_id: int | None = Query(None),
    limit: int = Query(200, le=1000),
):
    """Structured, filterable view over logs/agents.log (one JSON event per line).

    Reads the file TAIL-first so a huge log never gets parsed in full: only the
    last ~256KB are scanned, which covers hundreds of recent events. Newest first.
    """
    import json

    from backend.config import ROOT_DIR

    path = ROOT_DIR / "logs" / "agents.log"
    if not path.is_file():
        return {"events": [], "note": "nenhum log de agente registrado ainda"}

    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 256 * 1024))
            raw = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        return {"events": [], "note": f"falha ao ler o log: {exc}"}

    lines = raw.splitlines()
    if size > 256 * 1024 and lines:
        lines = lines[1:]  # first line is likely truncated mid-JSON — drop it

    events: list[dict] = []
    for line in reversed(lines):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        ev["severity"] = _SEVERITY_BY_STATUS.get(ev.get("status", ""), "INFO")
        if level and ev["severity"] != level.upper():
            continue
        if agent and ev.get("agent") != agent:
            continue
        if job_id is not None and ev.get("job_id") != job_id:
            continue
        events.append(ev)
        if len(events) >= limit:
            break
    return {"events": events}


@router.get("/health-detail")
def health_detail():
    """Same checks as /dashboard/health, exposed here too so the Config page's
    fix-all card can show current state without depending on the dashboard
    router's response shape."""
    from backend.agents.error_recovery import get_system_health

    return get_system_health()


@router.post("/fix-all")
def fix_all(db: Session = Depends(get_db)):
    """Runs every self-heal step this system knows about, in order, never
    letting one step's failure block the rest. Returns a breakdown of what
    was fixed automatically and what still needs a human, with plain-
    Portuguese reasons for the latter."""
    from backend.agents.error_recovery import get_system_health
    from backend.pipeline.dispatch import resume_account_blocked_jobs, resume_drive_blocked_jobs
    from backend.scheduler import _job_recover_stuck_publishing, ensure_scheduler_running, scheduler_status

    result = {
        "video_jobs": {"fixed": [], "fixed_count": 0, "skipped": [], "skipped_count": 0},
        "accounts_resumed": {},
        "drive_resumed": [],
        "stuck_jobs_swept": False,
        "scheduler": {"was_down": False, "now_running": True},
        "needs_human": [],
    }

    # 1. Every ERROR video job (skips the handful with duplicate-upload risk).
    try:
        result["video_jobs"] = fix_errors(db=db)
    except Exception:  # noqa: BLE001 — one step failing must never sink the rest
        logger.exception("fix_all: fix_errors falhou")

    # 2. Per-account YouTube/TikTok/Instagram reconnect resurrection — a safety
    #    net for a channel that reconnected but whose parked jobs weren't
    #    picked up yet (e.g. the callback's own resume call errored).
    try:
        accounts = db.execute(
            select(PlatformAccount).where(PlatformAccount.status == "active")
        ).scalars().all()
        for acct in accounts:
            resumed = resume_account_blocked_jobs(acct.id)
            if resumed:
                result["accounts_resumed"][acct.id] = resumed
    except Exception:  # noqa: BLE001
        logger.exception("fix_all: resume_account_blocked_jobs falhou")

    # 3. The single SHARED Google Drive connection.
    try:
        result["drive_resumed"] = resume_drive_blocked_jobs()
    except Exception:  # noqa: BLE001
        logger.exception("fix_all: resume_drive_blocked_jobs falhou")

    # 4. Force the stuck-PUBLISHING/PROCESSING sweep NOW instead of waiting up
    #    to 10 minutes for the next scheduled tick.
    try:
        _job_recover_stuck_publishing()
        result["stuck_jobs_swept"] = True
    except Exception:  # noqa: BLE001
        logger.exception("fix_all: recover_stuck_publishing falhou")

    # 5. Make sure the scheduler itself is alive — a dead scheduler would
    #    silently stop running every periodic recovery job above, forever.
    try:
        before = scheduler_status()
        result["scheduler"]["was_down"] = before["leader"] and not before["alive"]
        result["scheduler"]["now_running"] = ensure_scheduler_running()
    except Exception:  # noqa: BLE001
        logger.exception("fix_all: ensure_scheduler_running falhou")

    # 6. Anything still red/yellow needs a human (DB down, disk physically
    #    full, missing API key, ...) — report it instead of pretending
    #    everything is fixed.
    try:
        health = get_system_health()
        for name, check in health["checks"].items():
            if check["status"] != "green":
                result["needs_human"].append(
                    {"check": name, "status": check["status"], "detail": check["detail"]}
                )
    except Exception:  # noqa: BLE001
        logger.exception("fix_all: get_system_health falhou")

    return result
