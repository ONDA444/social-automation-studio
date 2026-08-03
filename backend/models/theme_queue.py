"""FIFO queue of themes/titles to be consumed by the content pipeline."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ThemeQueue(Base):
    __tablename__ = "theme_queue"
    __table_args__ = (
        # The scheduler and routers/themes.py both filter by
        # account_id == X AND status == 'pending' every cycle; without this the
        # plain single-column `status` index degrades to a scan of every
        # pending row across all accounts as the table grows.
        Index("ix_theme_queue_account_status", "account_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    # Nullable: a theme can be unassigned, and is detached (not deleted) if its
    # account is removed.
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="SET NULL"), default=None
    )

    theme: Mapped[str] = mapped_column(Text)  # the theme / title text

    content_type: Mapped[str] = mapped_column(
        String(60), default="film_recap_ai_images"
    )
    # "long" | "short" — lets a channel be Shorts-only on autopilot.
    video_format: Mapped[str] = mapped_column(String(20), default="long")
    target_platforms: Mapped[list] = mapped_column(
        JSON, default=lambda: ["youtube"]
    )

    # pending | consumed — indexed for fast FIFO pop of the next pending theme.
    status: Mapped[str] = mapped_column(String(20), index=True, default="pending")
    position: Mapped[int] = mapped_column(Integer, default=0)  # FIFO ordering

    # Real FK (ondelete=SET NULL): deleting a VideoJob shouldn't leave a
    # ThemeQueue row permanently stuck referencing a non-existent job — same
    # reasoning as ReadyVideo.reserved_job_id/used_job_id.
    consumed_job_id: Mapped[int | None] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="SET NULL"), index=True, default=None
    )

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "account_id": self.account_id,
            "theme": self.theme,
            "content_type": self.content_type,
            "format": self.video_format,
            "target_platforms": self.target_platforms or [],
            "status": self.status,
            "position": self.position,
            "consumed_job_id": self.consumed_job_id,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
