"""Post-publication metrics snapshots (2h / 24h / 7d) per platform."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column, relationship

from backend.database import Base


class VideoAnalytics(Base):
    __tablename__ = "video_analytics"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[int] = mapped_column(
        ForeignKey("video_jobs.id", ondelete="CASCADE"), index=True
    )

    platform: Mapped[str] = mapped_column(String(20))
    platform_video_id: Mapped[str | None] = mapped_column(String(120), default=None)
    snapshot_type: Mapped[str] = mapped_column(String(10), default="2h")  # 2h|24h|7d

    views: Mapped[int] = mapped_column(Integer, default=0)
    impressions: Mapped[int] = mapped_column(Integer, default=0)
    ctr: Mapped[float] = mapped_column(Float, default=0.0)
    likes: Mapped[int] = mapped_column(Integer, default=0)
    comments: Mapped[int] = mapped_column(Integer, default=0)
    shares: Mapped[int] = mapped_column(Integer, default=0)
    saves: Mapped[int] = mapped_column(Integer, default=0)
    retention_avg: Mapped[float] = mapped_column(Float, default=0.0)
    completion_rate: Mapped[float] = mapped_column(Float, default=0.0)

    # Watch-time (YouTube Analytics API; populated on the 24h/7d collect only).
    watch_minutes: Mapped[int] = mapped_column(Integer, default=0)
    avg_view_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    avg_view_pct: Mapped[float] = mapped_column(Float, default=0.0)
    subscribers_gained: Mapped[int] = mapped_column(Integer, default=0)

    thumbnail_variant: Mapped[str | None] = mapped_column(String(2), default=None)  # A|B
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    collected_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    job = relationship("VideoJob", back_populates="analytics")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "job_id": self.job_id,
            "platform": self.platform,
            "platform_video_id": self.platform_video_id,
            "snapshot_type": self.snapshot_type,
            "views": self.views,
            "impressions": self.impressions,
            "ctr": self.ctr,
            "likes": self.likes,
            "comments": self.comments,
            "shares": self.shares,
            "saves": self.saves,
            "retention_avg": self.retention_avg,
            "completion_rate": self.completion_rate,
            "watch_minutes": self.watch_minutes,
            "avg_view_seconds": self.avg_view_seconds,
            "avg_view_pct": self.avg_view_pct,
            "subscribers_gained": self.subscribers_gained,
            "thumbnail_variant": self.thumbnail_variant,
            "collected_at": self.collected_at.isoformat() if self.collected_at else None,
        }
