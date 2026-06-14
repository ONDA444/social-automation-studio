"""
BaseAgent — shared machinery for every pipeline stage.

Provides:
  * retry with backoff (spec default 30s / 2min / 5min, overridable/shortened for tests)
  * structured JSON logging to logs/agents.log
  * live WebSocket events via backend.events.publish_event
  * a shared VideoContext dict passed between agents of the same job

Agents implement async `run(**kwargs)` and call `self.emit(...)` for progress.
Use `await agent.execute(**kwargs)` to get retry + eventing for free.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Any

from backend.config import ROOT_DIR
from backend.events import publish_event

# --- JSON file logger (one line per event) ---
_log_path = ROOT_DIR / "logs" / "agents.log"
_log_path.parent.mkdir(parents=True, exist_ok=True)

_json_logger = logging.getLogger("studio.agents")
if not _json_logger.handlers:
    _json_logger.setLevel(logging.INFO)
    _fh = logging.FileHandler(_log_path, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(message)s"))
    _json_logger.addHandler(_fh)
    _json_logger.propagate = False

# When testing, long real backoffs are painful. STUDIO_FAST_RETRY shortens them.
_FAST = os.getenv("STUDIO_FAST_RETRY") == "1"


class AgentError(Exception):
    """Raised when an agent exhausts its retries."""


class BaseAgent:
    name: str = "base"
    # Spec: 3 attempts, backoff 30s / 2min / 5min.
    max_retries: int = 3
    backoffs: list[int] = [30, 120, 300]

    def __init__(
        self,
        job_id: int | None = None,
        context: dict[str, Any] | None = None,
        emit: bool = True,
    ) -> None:
        self.job_id = job_id
        self.context: dict[str, Any] = context if context is not None else {}
        self._emit = emit

    # ---- progress / logging ----
    def emit(
        self,
        status: str,
        message: str = "",
        progress: int | None = None,
        **extra: Any,
    ) -> None:
        event = {
            "type": "agent_event",
            "job_id": self.job_id,
            "agent": self.name,
            "status": status,  # started | progress | completed | retry | error
            "message": message,
            "progress": progress,
            **extra,
        }
        try:
            _json_logger.info(json.dumps(event, default=str))
        except Exception:
            pass
        if self._emit:
            publish_event(event)

    # ---- contract ----
    async def run(self, **kwargs: Any) -> Any:  # pragma: no cover - abstract
        raise NotImplementedError

    async def execute(self, **kwargs: Any) -> Any:
        """Run `run()` with retry + backoff + events."""
        last_exc: Exception | None = None
        self.emit("started", f"{self.name} iniciado")
        for attempt in range(self.max_retries):
            try:
                result = await self.run(**kwargs)
                self.emit("completed", f"{self.name} concluído")
                return result
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                is_last = attempt == self.max_retries - 1
                if is_last:
                    self.emit("error", f"{self.name} falhou: {exc}", error=str(exc))
                    break
                wait = 2 if _FAST else self.backoffs[min(attempt, len(self.backoffs) - 1)]
                self.emit(
                    "retry",
                    f"{self.name} tentativa {attempt + 1} falhou, repetindo em {wait}s",
                    attempt=attempt + 1,
                    error=str(exc),
                )
                await asyncio.sleep(wait)
        raise AgentError(f"{self.name} esgotou tentativas: {last_exc}") from last_exc

    # ---- helpers ----
    @staticmethod
    def job_dir(job_id: int | None, base: str) -> Path:
        """Per-job working subfolder under output/ or tmp/."""
        label = "adhoc" if job_id is None else job_id  # 0 is a valid id
        d = Path(base) / f"job_{label}"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def ctx_get(self, key: str, default: Any = None) -> Any:
        return self.context.get(key, default)

    def ctx_set(self, key: str, value: Any) -> None:
        self.context[key] = value


def run_agent_cli(agent_factory, **run_kwargs) -> Any:
    """Helper for `python -m backend.agents.X --test` blocks."""
    agent = agent_factory()
    return asyncio.run(agent.execute(**run_kwargs))
