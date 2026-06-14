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

        results: dict = {}
        shorts = job.shorts_paths or []
        publish_at = job.scheduled_at.isoformat() + "Z" if job.scheduled_at else None

        for platform in platforms:
            acct = svc.get_active_account(platform)
            if not acct:
                results[platform] = {"ok": False, "status": "no_account",
                                     "error": f"Sem conta ativa de {platform}."}
                continue
            if not svc.can_upload(acct.id):
                svc.pause(acct.id, "quota_exceeded")
                results[platform] = {"ok": False, "status": "quota_exceeded",
                                     "error": f"Quota diária de {platform} atingida."}
                _emit(job_id, platform=platform, status="quota_exceeded")
                continue
            creds = svc.get_credentials(acct.id)

            if platform == "youtube":
                results["youtube"] = await self_publish_youtube(job, seo, creds, publish_at, shorts)
            elif platform == "tiktok":
                results["tiktok"] = await self_publish_tiktok(seo, creds, shorts)
            elif platform == "instagram":
                results["instagram"] = await self_publish_instagram(seo, creds, shorts)
            else:
                results[platform] = {"ok": False, "status": "unsupported"}

            if results.get(platform, {}).get("ok"):
                svc.record_upload(acct.id)
            _emit(job_id, platform=platform, status=results[platform].get("status"))

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
    finally:
        db.close()


async def self_publish_youtube(job, seo, creds, publish_at, shorts) -> dict:
    y = seo.get("youtube", {})
    main = await _with_retry(
        yt.upload_video, job.main_video_path, y.get("title", job.title),
        y.get("description", ""), y.get("tags", []), creds,
        category_id=y.get("category_id", "22"), publish_at=publish_at,
        thumbnail_path=job.thumbnail_path, label="yt-main",
    )
    short_results = []
    if main.get("ok"):
        for sp in shorts:
            short_results.append(await _with_retry(
                yt.upload_video, sp, (y.get("title", job.title) + " #shorts")[:100],
                y.get("description", ""), y.get("tags", []), creds,
                category_id=y.get("category_id", "22"), label="yt-short",
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
