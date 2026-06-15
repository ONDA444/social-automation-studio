"""
PublisherAgent — multi-platform upload of an APPROVED job (never before approval).

Per platform: pick the active account, check quota + credentials, upload with
3x retry/backoff, record results in job.publish_status. YouTube gets the main
video + each Short; TikTok/Instagram get a vertical Short.

Honours: YouTube quota (no extra projects), TikTok approval hold, IG rate limit.
After a successful YouTube publish, triggers cross-platform mirroring if linked.
"""
from __future__ import annotations

import asyncio
import logging

from backend.database import SessionLocal
from backend.events import publish_event
from backend.models import JobStatus, VideoJob
from backend.agents.account_profile import AccountProfileService
from backend.uploaders import youtube as yt
from backend.uploaders import tiktok as tk
from backend.uploaders import instagram as ig

logger = logging.getLogger("studio.publisher")
RETRY_BACKOFFS = [60, 300, 900]  # 1min / 5min / 15min


def _emit(job_id, **fields):
    publish_event({"type": "publish_update", "job_id": job_id, **fields})


async def _with_retry(fn, *args, label="upload", **kwargs) -> dict:
    import os

    fast = os.getenv("STUDIO_FAST_RETRY") == "1"
    last = {}
    for attempt in range(3):
        result = await asyncio.to_thread(fn, *args, **kwargs)
        if result.get("ok"):
            return result
        last = result
        # Don't retry terminal states (approval/quota/auth/config).
        if result.get("status") in {"tiktok_pending_approval", "quota_exceeded",
                                     "auth_error", "not_configured", "library_missing"}:
            return result
        if attempt < 2:
            await asyncio.sleep(2 if fast else RETRY_BACKOFFS[attempt])
    return last


async def run_publish(job_id: int) -> dict:
    db = SessionLocal()
    job = None
    results: dict = {}
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return {"error": "job not found"}
        if job.status not in (JobStatus.APPROVED, JobStatus.PUBLISHING):
            return {"error": f"job não aprovado (status={job.status})"}

        svc = AccountProfileService(db)
        seo = job.seo_metadata or {}
        platforms = job.target_platforms or ["youtube"]
        job.status = JobStatus.PUBLISHING
        db.commit()
        _emit(job_id, status="publishing")

        shorts = job.shorts_paths or []
        # YouTube's publishAt must be in the FUTURE. In the slot-gated/auto-publish
        # model the slot has already arrived (scheduled_at <= now), so a past value
        # would make the API error or ignore the schedule — publish immediately
        # instead. Only defer when the slot is genuinely still ahead.
        from datetime import datetime as _dt
        publish_at = None
        if job.scheduled_at and job.scheduled_at > _dt.utcnow():
            publish_at = job.scheduled_at.isoformat() + "Z"
        # Privacy chosen for the job (default 'private' = nothing goes public
        # until the user explicitly opts in). STUDIO_TEST_MODE forces private.
        privacy = _resolve_privacy(job)

        # The job may be pinned to a specific account (job.account_id). Resolve it
        # up front so we publish on the RIGHT channel instead of the highest-quota
        # one — critical when the user has 2+ YouTube channels connected.
        pinned = svc.get(job.account_id) if job.account_id else None

        for platform in platforms:
            # Isolate each platform: a crash uploading to one must never abort the
            # others (or leave the whole multi-platform job stuck/ERROR).
            try:
                acct = _resolve_account(svc, platform, pinned)
                if not acct:
                    results[platform] = {"ok": False, "status": "no_account",
                                         "error": f"Sem conta ativa de {platform}."}
                    _emit(job_id, platform=platform, status="no_account")
                    continue
                # Guard: never hand an account without connected credentials to the
                # uploader (otherwise YouTube fails with a cryptic RefreshError).
                if not svc.has_valid_credentials(acct):
                    results[platform] = {
                        "ok": False, "status": "auth_error",
                        "error": "Conta sem credenciais conectadas. Conecte a conta em Contas.",
                    }
                    _emit(job_id, platform=platform, status="auth_error")
                    continue
                if not svc.can_upload(acct.id):
                    svc.pause(acct.id, "quota_exceeded")
                    results[platform] = {"ok": False, "status": "quota_exceeded",
                                         "error": f"Quota diária de {platform} atingida."}
                    _emit(job_id, platform=platform, status="quota_exceeded")
                    continue
                creds = svc.get_credentials(acct.id)

                if platform == "youtube":
                    results["youtube"] = await self_publish_youtube(job, seo, creds, publish_at, shorts, privacy)
                elif platform == "tiktok":
                    results["tiktok"] = await self_publish_tiktok(seo, creds, shorts)
                elif platform == "instagram":
                    results["instagram"] = await self_publish_instagram(seo, creds, shorts)
                else:
                    results[platform] = {"ok": False, "status": "unsupported"}

                if results.get(platform, {}).get("ok"):
                    svc.record_upload(acct.id)
                _emit(job_id, platform=platform, status=results[platform].get("status"))
            except Exception as exc:  # noqa: BLE001 — per-platform isolation
                logger.warning("Publicação em %s falhou para job %s: %s", platform, job_id, exc)
                results[platform] = {"ok": False, "status": "error", "error": str(exc)[:300]}
                _emit(job_id, platform=platform, status="error")

        job.publish_status = results
        job.status = _overall_status(results)
        db.commit()
        _emit(job_id, status=job.status.value, results=results)

        # Cross-platform mirror after a successful YouTube publish.
        if results.get("youtube", {}).get("ok"):
            try:
                from backend.agents.cross_platform_linker import mirror_after_publish

                mirror_after_publish(db, job)
            except Exception as exc:  # noqa: BLE001
                logger.debug("mirror skipped: %s", exc)

        return {"status": job.status.value, "results": results}
    except Exception as exc:  # noqa: BLE001
        # Never leave the job stuck in PUBLISHING — always land a terminal state.
        logger.exception("run_publish failed for job %s", job_id)
        if job is not None:
            try:
                job.publish_status = results or None
                job.status = JobStatus.ERROR
                job.error_message = f"Falha na publicação: {exc}"[:500]
                db.commit()
                _emit(job_id, status=JobStatus.ERROR.value, error=str(exc))
            except Exception:  # noqa: BLE001
                logger.exception("could not persist ERROR status for job %s", job_id)
        return {"status": JobStatus.ERROR.value, "error": str(exc), "results": results}
    finally:
        db.close()


async def self_publish_youtube(job, seo, creds, publish_at, shorts, privacy="private") -> dict:
    y = seo.get("youtube", {})
    main = await _with_retry(
        yt.upload_video, job.main_video_path, y.get("title", job.title),
        y.get("description", ""), y.get("tags", []), creds,
        category_id=y.get("category_id", "22"), privacy=privacy, publish_at=publish_at,
        thumbnail_path=job.thumbnail_path, label="yt-main",
    )
    short_results = []
    if main.get("ok"):
        for sp in shorts:
            short_results.append(await _with_retry(
                yt.upload_video, sp, (y.get("title", job.title) + " #shorts")[:100],
                y.get("description", ""), y.get("tags", []), creds,
                category_id=y.get("category_id", "22"), privacy=privacy, label="yt-short",
            ))
    return {**main, "shorts": short_results}


async def self_publish_tiktok(seo, creds, shorts) -> dict:
    caption = seo.get("tiktok", {}).get("caption", "")
    target = _pick_short(shorts, prefer=4) or _pick_short(shorts, prefer=2)
    if not target:
        return {"ok": False, "platform": "tiktok", "status": "no_short", "error": "Sem Short para TikTok."}
    return await _with_retry(tk.upload_video, target, caption, creds, label="tiktok")


async def self_publish_instagram(seo, creds, shorts) -> dict:
    ig_meta = seo.get("instagram", {})
    caption = ig_meta.get("caption", "")
    hashtags = " ".join(ig_meta.get("hashtags", []))
    target = _pick_short(shorts, prefer=3) or _pick_short(shorts, prefer=2)
    if not target:
        return {"ok": False, "platform": "instagram", "status": "no_short", "error": "Sem Short para IG."}
    return await _with_retry(ig.upload_reel, target, f"{caption}\n\n{hashtags}".strip(), creds, label="ig")


def _resolve_account(svc, platform, pinned):
    """Resolve the account to publish on for a given platform.

    If the job is pinned to a specific account (job.account_id) and that account
    is for THIS platform and has valid connected credentials, use it — this makes
    us publish on the channel the user chose instead of whichever active account
    happens to have the most quota. Otherwise fall back to get_active_account so
    extra target platforms (or an empty/invalid pin) still resolve sensibly.
    """
    if pinned and pinned.platform == platform and svc.has_valid_credentials(pinned):
        return pinned
    return svc.get_active_account(platform)


def _resolve_privacy(job) -> str:
    """Privacy for this publish — safe by default.

    Default is 'private': nothing goes public until the user explicitly opts in
    (via job.privacy). STUDIO_TEST_MODE=1 hard-forces 'private' regardless of the
    job's choice so test runs never leak public uploads.
    """
    import os

    if os.getenv("STUDIO_TEST_MODE") == "1":
        return "private"
    choice = getattr(job, "privacy", None)
    if choice in {"public", "unlisted", "private"}:
        return choice
    return "private"


def _pick_short(shorts: list[str], prefer: int) -> str | None:
    """Pick a short by its format number suffix (video_*_short_<n>.mp4), else first."""
    for s in shorts:
        if f"_short_{prefer}." in s:
            return s
    return shorts[0] if shorts else None


def _overall_status(results: dict) -> JobStatus:
    statuses = [r.get("status") for r in results.values()]
    if any(r.get("ok") for r in results.values()):
        return JobStatus.PUBLISHED
    if "tiktok_pending_approval" in statuses:
        return JobStatus.TIKTOK_PENDING_APPROVAL
    return JobStatus.ERROR


if __name__ == "__main__":
    import sys

    jid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(asyncio.run(run_publish(jid)))
