"""
Celery application. Target for:  celery -A backend.pipeline.celery_app worker
Both `app` and `celery_app` are exported so either -A form resolves.
"""
from __future__ import annotations

from celery import Celery

from backend.config import settings

app = Celery(
    "studio",
    broker=settings.redis_url,
    backend=settings.redis_url,
    include=["backend.pipeline.video_pipeline"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="America/Sao_Paulo",
    enable_utc=True,
    task_acks_late=True,
    worker_max_tasks_per_child=20,        # recycle workers (ffmpeg/memory hygiene)
    broker_connection_retry_on_startup=True,
)

# Periodic jobs (TrendingAgent etc.) are registered in Phase 6 via this schedule.
app.conf.beat_schedule = {}

celery_app = app  # alias
