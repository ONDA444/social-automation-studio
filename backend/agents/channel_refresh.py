"""Idempotent refresh for ready-video jobs belonging to a Channel.

Re-runs the curation layer (backend/agents/ready_video_curation.py) ONLY
when the input that actually drives its output changed: the video's own
vision analysis, or the channel's visual_theme/tts_voice. A checksum of
those inputs is stored on the job (video_context.curation_fingerprint) so a
channel-wide refresh skips every job that's already up to date instead of
burning an ffmpeg render on each one regardless.

Always re-curates from the PRISTINE Drive source (re-downloaded via the same
cached/idempotent DriveLibraryService.download_for_job the scheduler uses),
never from the job's current main_video_path -- curating an already-curated
video would compound (a second intro glued onto the first one) instead of
replacing it.

Best-effort throughout, same contract as apply_curation_layer: a failure
here must never leave the job pointing at a broken/missing file. If curation
produces nothing new this round, the job's last-good main_video_path is left
untouched and the fingerprint is NOT advanced, so the next refresh retries
instead of silently giving up forever.
"""
from __future__ import annotations

import hashlib
import json
import logging

from backend.models import Channel, JobStatus, ReadyVideo, VideoJob

logger = logging.getLogger("studio.channel_refresh")

# Jobs in these states have a real published/publishable main_video_path
# worth re-curating. PROCESSING/PUBLISHING/QUEUED are mid-flight (refreshing
# now would race the pipeline that's already touching them); ERROR has no
# reliable video to start from.
_REFRESHABLE_STATUSES = {JobStatus.AWAITING_APPROVAL, JobStatus.APPROVED, JobStatus.PUBLISHED}

_DEFAULT_BATCH_LIMIT = 50


def _fingerprint(job: VideoJob, channel: Channel) -> str:
    analysis = (job.video_context or {}).get("content_analysis") or {}
    payload = {
        "analysis_summary": analysis.get("summary"),
        "analysis_hook": analysis.get("hook"),
        "analysis_topics": analysis.get("topics"),
        "visual_theme": channel.resolved_visual_theme(),
        "tts_voice": channel.tts_voice,
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def refresh_job(db, channel: Channel, job: VideoJob) -> dict:
    """Best-effort -- never raises. Always returns a result dict describing
    what happened: {job_id, action, ...}. action is one of:
      skipped  -- not eligible, or nothing changed since the last refresh
      refreshed -- curation re-ran and produced a new main_video_path
      no_change_applied -- curation ran but produced nothing new (will
        retry on the next call; the job's existing video is left as-is)
      failed   -- couldn't even get the pristine source (download error)
    """
    if job.mode != "from_ready_video":
        return {"job_id": job.id, "action": "skipped", "reason": "not_a_ready_video_job"}
    if job.status not in _REFRESHABLE_STATUSES:
        return {"job_id": job.id, "action": "skipped",
                "reason": f"status={getattr(job.status, 'value', job.status)}"}

    ctx = dict(job.video_context or {})
    ready_video_id = ctx.get("ready_video_id")
    if not ready_video_id:
        return {"job_id": job.id, "action": "skipped", "reason": "no_ready_video_id"}
    ready = db.get(ReadyVideo, ready_video_id)
    if ready is None:
        return {"job_id": job.id, "action": "skipped", "reason": "ready_video_missing"}

    current_fp = _fingerprint(job, channel)
    if ctx.get("curation_fingerprint") == current_fp:
        logger.info("refresh_skipped job_id=%s channel_id=%s reason=unchanged", job.id, channel.id)
        return {"job_id": job.id, "action": "skipped", "reason": "unchanged"}

    from backend.agents.drive_library import DriveLibraryService

    try:
        source_path = DriveLibraryService(db).download_for_job(ready, job.id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("refresh_download_failed job_id=%s channel_id=%s error=%s", job.id, channel.id, exc)
        return {"job_id": job.id, "action": "failed", "reason": f"download: {exc}"[:200]}

    from backend.agents.ready_video_curation import apply_curation_layer

    analysis = ctx.get("content_analysis") or {}
    new_path = apply_curation_layer(
        job_id=job.id,
        local_path=source_path,
        analysis=analysis,
        context={"niche": channel.niche or ""},
        video_format=job.video_format,
        visual_theme=channel.resolved_visual_theme(),
        voice=channel.tts_voice,
    )

    if new_path == source_path:
        # apply_curation_layer's own best-effort contract: a failure inside
        # it falls back to the pristine path. Don't regress a job that
        # already had a successfully curated video -- leave main_video_path
        # alone and DON'T advance the fingerprint, so the next refresh call
        # retries instead of silently accepting the miss forever.
        logger.info("refresh_no_change job_id=%s channel_id=%s", job.id, channel.id)
        return {"job_id": job.id, "action": "no_change_applied"}

    job.main_video_path = new_path
    if job.video_format == "short":
        job.shorts_paths = [new_path]
    ctx["curation_fingerprint"] = current_fp
    job.video_context = ctx
    db.commit()
    logger.info("refresh_applied job_id=%s channel_id=%s", job.id, channel.id)
    return {"job_id": job.id, "action": "refreshed"}


def refresh_channel(db, channel: Channel, *, limit: int = _DEFAULT_BATCH_LIMIT) -> list[dict]:
    """Refresh every eligible ready-video job for this channel (most recent
    first), up to `limit`. One job failing never stops the rest."""
    jobs = (
        db.query(VideoJob)
        .filter(VideoJob.account_id == channel.account_id, VideoJob.mode == "from_ready_video")
        .order_by(VideoJob.created_at.desc())
        .limit(limit)
        .all()
    )
    results = []
    for job in jobs:
        try:
            results.append(refresh_job(db, channel, job))
        except Exception as exc:  # noqa: BLE001 -- one bad job must never stop the batch
            logger.warning("refresh_channel_unexpected_error job_id=%s channel_id=%s error=%s",
                            job.id, channel.id, exc)
            results.append({"job_id": job.id, "action": "failed", "reason": str(exc)[:200]})
    return results
