"""
CrossPlatformLinkerAgent — mirror a published video to linked accounts.

After a successful YouTube publish, if the source account has linked accounts and
mirror_to_linked is on, create lightweight mirror jobs that REUSE the already
rendered Shorts (no re-render), adapt SEO per platform, and enter the approval
queue (or auto-publish if the link is set to auto_approve), with a delay.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.models import JobStatus, PlatformAccount, VideoJob

logger = logging.getLogger("studio.linker")

DEFAULT_DELAY_MIN = 30
# Which Short format each platform mirrors.
MIRROR_SHORT = {"tiktok": 4, "instagram": 3}  # 60s for TikTok, 30s for IG


def mirror_after_publish(db: Session, source_job: VideoJob) -> list[int]:
    """Create mirror jobs for the source account's linked platforms. Returns ids."""
    if source_job.is_mirror:
        return []  # never mirror a mirror
    if not source_job.account_id:
        return []
    acct = db.get(PlatformAccount, source_job.account_id)
    if not acct or not acct.mirror_to_linked or not acct.linked_accounts:
        return []

    created: list[int] = []
    delay = timedelta(minutes=DEFAULT_DELAY_MIN)
    # Mirrors already created for this source job, keyed by target platform, so a
    # retry of a partially-failed source job doesn't create duplicate mirrors for
    # platforms that were already mirrored.
    existing_mirror_platforms = {
        p
        for (mirror_targets,) in db.execute(
            select(VideoJob.target_platforms).where(
                VideoJob.mirror_source_id == source_job.id,
                VideoJob.is_mirror.is_(True),
            )
        ).all()
        for p in (mirror_targets or [])
    }
    for platform, linked_id in (acct.linked_accounts or {}).items():
        if platform in (source_job.target_platforms or []):
            # source job already publishes directly to this platform; skip the
            # mirror to avoid double-publishing to the same linked account.
            continue
        if platform in existing_mirror_platforms:
            continue
        target = db.get(PlatformAccount, linked_id) if linked_id else None
        if not target or target.status != "active":
            continue
        mirror = _build_mirror(source_job, target, platform)
        mirror.scheduled_at = datetime.utcnow() + delay
        db.add(mirror)
        db.commit()
        db.refresh(mirror)
        created.append(mirror.id)
        logger.info("Mirror job %s created for %s (source %s)", mirror.id, platform, source_job.id)

        # Mirrors NEVER auto-publish: a mirror would push a public post to
        # TikTok/IG with no human review, violating the approval gate. We leave
        # every mirror in AWAITING_APPROVAL so a person reviews it first, even
        # when the target account opted into auto_approve_mirrors — that flag no
        # longer triggers an immediate dispatch_publish.
        if (target.schedule or {}).get("auto_approve_mirrors"):
            logger.info(
                "Mirror job %s held in AWAITING_APPROVAL (auto_approve_mirrors no "
                "longer auto-publishes; awaiting human review).", mirror.id,
            )
    return created


def _build_mirror(source: VideoJob, target: PlatformAccount, platform: str) -> VideoJob:
    seo = dict(source.seo_metadata or {})
    # Keep only the relevant platform's SEO block but preserve structure.
    return VideoJob(
        title=source.title,
        topic=source.topic,
        mode=source.mode,
        content_type=source.content_type,
        account_id=target.id,
        status=JobStatus.AWAITING_APPROVAL,
        approval_status="pending",
        progress=100,
        is_mirror=True,
        mirror_source_id=source.id,
        target_platforms=[platform],
        main_video_path=source.main_video_path,
        thumbnail_path=source.thumbnail_path,
        shorts_paths=source.shorts_paths or [],
        seo_metadata=seo,
        qc_status=source.qc_status,
        compliance_status=source.compliance_status,
        video_context={"mirror_of": source.id, "mirror_short": MIRROR_SHORT.get(platform)},
    )
