"""The central pipeline entity — one row per video being produced."""
from __future__ import annotations

import enum
from datetime import datetime

from sqlalchemy import JSON, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class JobStatus(str, enum.Enum):
    QUEUED = "queued"
    PROCESSING = "processing"
    AWAITING_APPROVAL = "awaiting_approval"
    APPROVED = "approved"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    ERROR = "error"
    # Platform-specific holding states
    TIKTOK_PENDING_APPROVAL = "tiktok_pending_approval"
    AWAITING_QUOTA = "awaiting_quota"


class VideoJob(Base):
    __tablename__ = "video_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # --- Input ---
    title: Mapped[str] = mapped_column(String(300))
    topic: Mapped[str | None] = mapped_column(Text, default=None)
    # mode: from_title | from_remix | from_idea
    mode: Mapped[str] = mapped_column(String(40), default="from_title")
    # content_type: film_recap_ai_images | sports_highlights | quote_viral
    content_type: Mapped[str] = mapped_column(String(60), default="film_recap_ai_images")
    # video_format: "long" (16:9 YouTube, ~minutos) | "short" (9:16 vertical <60s,
    # nativo p/ TikTok/Reels/Shorts — NÃO derivado de um vídeo longo).
    video_format: Mapped[str] = mapped_column(String(20), default="long")
    reference_url: Mapped[str | None] = mapped_column(Text, default=None)

    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="SET NULL"), default=None
    )

    # --- Pipeline state ---
    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus, native_enum=False, length=40), default=JobStatus.QUEUED, index=True
    )
    current_agent: Mapped[str | None] = mapped_column(String(60), default=None)
    progress: Mapped[int] = mapped_column(Integer, default=0)  # 0..100
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    # Separate budget from retry_count: retry_count also gets bumped by unrelated
    # LLM-failure retries (scheduler._job_resurrect_llm_failures), so sharing one
    # counter could exhaust the one-time orphan-resume allowance before the job
    # ever actually died mid-render. See backend.main._apply_orphan_transition.
    orphan_resume_count: Mapped[int] = mapped_column(Integer, default=0)
    error_message: Mapped[str | None] = mapped_column(Text, default=None)

    # --- Shared context + per-agent artifacts (JSON blobs) ---
    video_context: Mapped[dict] = mapped_column(JSON, default=dict)
    style_dna: Mapped[dict | None] = mapped_column(JSON, default=None)
    script: Mapped[dict | None] = mapped_column(JSON, default=None)
    editing_plan: Mapped[dict | None] = mapped_column(JSON, default=None)
    seo_metadata: Mapped[dict | None] = mapped_column(JSON, default=None)

    # --- Quality / compliance / approval gates ---
    qc_status: Mapped[str | None] = mapped_column(String(40), default=None)
    compliance_status: Mapped[str | None] = mapped_column(String(40), default=None)
    approval_status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected

    # --- Outputs ---
    main_video_path: Mapped[str | None] = mapped_column(Text, default=None)
    thumbnail_path: Mapped[str | None] = mapped_column(Text, default=None)
    shorts_paths: Mapped[list] = mapped_column(JSON, default=list)

    # --- Publishing ---
    target_platforms: Mapped[list] = mapped_column(JSON, default=list)  # ["youtube","tiktok",...]
    publish_status: Mapped[dict] = mapped_column(JSON, default=dict)  # {platform: status}
    scheduled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    # Set ONLY by the automated scheduler's slot-filling path (backend/scheduler.py
    # _create_theme_job / _try_create_ready_video_job) to "{account_id}:{slot_iso}".
    # A unique index on this column (see database.ensure_indexes) turns the
    # slot-clash check from a racy check-then-act SELECT into a real DB-enforced
    # claim: two concurrent inserts for the same slot can no longer both succeed.
    # Left NULL for manual/immediate/mirror jobs, which may legitimately share a
    # scheduled_at.
    schedule_slot_key: Mapped[str | None] = mapped_column(String(160), default=None)

    # Cross-platform mirror bookkeeping
    is_mirror: Mapped[bool] = mapped_column(default=False)
    mirror_source_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="SET NULL"), default=None
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    account = relationship("PlatformAccount", back_populates="jobs", foreign_keys=[account_id])
    analytics = relationship(
        "VideoAnalytics", back_populates="job", cascade="all, delete-orphan"
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "topic": self.topic,
            "mode": self.mode,
            "content_type": self.content_type,
            "format": self.video_format,
            "reference_url": self.reference_url,
            "account_id": self.account_id,
            "status": self.status.value if isinstance(self.status, JobStatus) else self.status,
            "current_agent": self.current_agent,
            "progress": self.progress,
            "retry_count": self.retry_count,
            "error_message": self.error_message,
            "style_dna": self.style_dna,
            "script": self.script,
            "editing_plan": self.editing_plan,
            "seo_metadata": self.seo_metadata,
            "qc_status": self.qc_status,
            "compliance_status": self.compliance_status,
            "approval_status": self.approval_status,
            "video_context": self.video_context or {},
            "main_video_path": self.main_video_path,
            "thumbnail_path": self.thumbnail_path,
            "shorts_paths": self.shorts_paths or [],
            "target_platforms": self.target_platforms or [],
            "publish_status": self.publish_status or {},
            "scheduled_at": self.scheduled_at.isoformat() if self.scheduled_at else None,
            "is_mirror": self.is_mirror,
            "mirror_source_id": self.mirror_source_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    # Heavy per-agent artifacts (~30–80 KB each). List views never need them — only
    # the detail view GET /jobs/{id} does. Dropping them from list responses cuts
    # the payload ~95% with NO feature loss (status, the trending badge in
    # video_context, publish_status and thumbnail are all kept).
    _HEAVY_FIELDS = ("style_dna", "script", "editing_plan", "seo_metadata")

    def to_dict_slim(self) -> dict:
        """Lightweight serialization for LIST endpoints (full dict minus the heavy
        JSON blobs)."""
        d = self.to_dict()
        for k in self._HEAVY_FIELDS:
            d.pop(k, None)
        return d
