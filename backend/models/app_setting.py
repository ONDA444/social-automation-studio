"""Runtime-editable app settings (key/value), overriding the .env defaults.

Lets the dashboard persist things the user must be able to change without a
redeploy — e.g. the monetization CTA and the localization languages. Read via
backend.runtime_settings, which falls back to the .env `settings` when a key
has never been saved."""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AppSetting(Base):
    __tablename__ = "app_settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    def to_dict(self) -> dict:
        return {"key": self.key, "value": self.value}
