"""Inventory rows for ready-to-post videos stored in Google Drive."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, BigInteger, DateTime, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class ReadyVideo(Base):
    __tablename__ = "ready_videos"
    __table_args__ = (
        UniqueConstraint("drive_file_id", name="uq_ready_videos_drive_file_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)

    drive_file_id: Mapped[str] = mapped_column(String(160), index=True)
    drive_folder_id: Mapped[str | None] = mapped_column(String(160), index=True, default=None)
    name: Mapped[str] = mapped_column(String(300))
    mime_type: Mapped[str | None] = mapped_column(String(120), default=None)

    niche: Mapped[str | None] = mapped_column(String(120), index=True, default=None)
    folder_path: Mapped[str | None] = mapped_column(Text, default=None)
    content_type: Mapped[str] = mapped_column(String(60), default="auto")
    video_format: Mapped[str] = mapped_column(String(20), default="short")
    account_id: Mapped[int | None] = mapped_column(
        ForeignKey("platform_accounts.id", ondelete="SET NULL"), index=True, default=None
    )

    status: Mapped[str] = mapped_column(String(20), index=True, default="available")
    # available | reserved | used | rejected | missing | error
    reserved_job_id: Mapped[int | None] = mapped_column(Integer, default=None)
    used_job_id: Mapped[int | None] = mapped_column(Integer, default=None)
    reserved_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    used_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    size_bytes: Mapped[int | None] = mapped_column(BigInteger, default=None)
    duration_seconds: Mapped[int | None] = mapped_column(Integer, default=None)
    local_path: Mapped[str | None] = mapped_column(Text, default=None)
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)

    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "drive_file_id": self.drive_file_id,
            "drive_folder_id": self.drive_folder_id,
            "name": self.name,
            "mime_type": self.mime_type,
            "niche": self.niche,
            "folder_path": self.folder_path,
            "content_type": self.content_type,
            "format": self.video_format,
            "account_id": self.account_id,
            "status": self.status,
            "reserved_job_id": self.reserved_job_id,
            "used_job_id": self.used_job_id,
            "reserved_at": self.reserved_at.isoformat() if self.reserved_at else None,
            "used_at": self.used_at.isoformat() if self.used_at else None,
            "size_bytes": self.size_bytes,
            "duration_seconds": self.duration_seconds,
            "local_path": self.local_path,
            "metadata": self.metadata_json or {},
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
