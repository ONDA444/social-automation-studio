"""Celery tasks wrapping the orchestrator."""
from __future__ import annotations

import asyncio

from backend.pipeline.celery_app import app


@app.task(name="pipeline.process_job", bind=True, max_retries=0)
def process_job(self, job_id: int) -> dict:
    """Run the full production pipeline for a job (blocking, in a worker)."""
    from backend.agents.orchestrator import run_pipeline

    return asyncio.run(run_pipeline(job_id))


@app.task(name="pipeline.publish_job")
def publish_job(job_id: int) -> dict:
    """Publish an approved job to its target platforms."""
    from backend.agents.publisher import run_publish

    return asyncio.run(run_publish(job_id))
