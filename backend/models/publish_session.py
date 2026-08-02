"""PublishSession — the day's publishing record for one Channel.

Tracks REFERENCES into VideoJob (planned_items/published_items are lists of
video_job ids), never a copy of job state -- VideoJob stays the single source
of truth for what a job is doing. This is what lets "how many videos has
channel X posted today, how many are left" be answered without re-deriving it
from a full VideoJob scan on every request, while the actual queue/dispatch
machinery in scheduler.py is untouched.
"""
from __future__ import annotations

from datetime import date as date_, datetime

from sqlalchemy import JSON, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base

# pending: created, nothing dispatched yet.
# running: at least one planned item is not yet published/errored.
# completed: every planned item published successfully.
# partial_failure: session's window closed (or day ended) with >=1 planned
#   item that errored out and was never recovered.
SESSION_STATUSES = ("pending", "running", "completed", "partial_failure")


class PublishSession(Base):
    __tablename__ = "publish_sessions"
    __table_args__ = (
        UniqueConstraint("channel_id", "date", name="uq_publish_sessions_channel_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    channel_id: Mapped[int] = mapped_column(
        ForeignKey("channels.id", ondelete="CASCADE"), index=True
    )
    date: Mapped[date_] = mapped_column(Date, index=True)

    planned_items: Mapped[list] = mapped_column(JSON, default=list)    # [video_job_id, ...]
    published_items: Mapped[list] = mapped_column(JSON, default=list)  # [video_job_id, ...]
    status: Mapped[str] = mapped_column(String(20), default="pending")

    started_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    channel = relationship("Channel", back_populates="sessions")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "channel_id": self.channel_id,
            "date": self.date.isoformat() if self.date else None,
            "planned_items": self.planned_items or [],
            "published_items": self.published_items or [],
            "remaining": max(0, len(self.planned_items or []) - len(self.published_items or [])),
            "status": self.status,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "finished_at": self.finished_at.isoformat() if self.finished_at else None,
        }
