"""AutomationRule — regras SE->ENTÃO configuráveis (ver §27 do prompt mestre).

Uma linha por regra: quando `trigger` acontece para um job, a engine
(backend/agents/automation_engine.py) executa `action` com os guardas de
segurança dela. `runs`/`last_run_at` dão visibilidade real de execução.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, Boolean, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from backend.database import Base


class AutomationRule(Base):
    __tablename__ = "automation_rules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(160))
    # job_awaiting_approval | job_approved | job_error | job_published
    trigger: Mapped[str] = mapped_column(String(40))
    # notify | retry_job | collect_analytics
    action: Mapped[str] = mapped_column(String(40))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    # Ex.: {"max_attempts": 2} para retry_job
    config: Mapped[dict] = mapped_column(JSON, default=dict)

    runs: Mapped[int] = mapped_column(Integer, default=0)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "trigger": self.trigger,
            "action": self.action,
            "enabled": bool(self.enabled),
            "config": self.config or {},
            "runs": self.runs,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
