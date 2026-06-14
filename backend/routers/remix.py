"""Remix Engine API — analyze a reference into StyleDNA, then create a remix job."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from backend.agents.analyzer import AnalyzerAgent
from backend.database import get_db
from backend.models import JobStatus, VideoJob
from backend.pipeline.dispatch import dispatch_job

router = APIRouter(prefix="/remix", tags=["remix"])


class AnalyzeRequest(BaseModel):
    source: str  # URL or local path


class RemixCreate(BaseModel):
    title: str = Field(..., min_length=1)
    source: str | None = None
    style_dna: dict | None = None
    content_type: str | None = None
    account_id: int | None = None
    target_platforms: list[str] = Field(default_factory=lambda: ["youtube"])


@router.post("/analyze")
async def analyze(payload: AnalyzeRequest):
    agent = AnalyzerAgent(job_id=None, emit=False)
    try:
        dna = await agent.execute(source=payload.source)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(400, f"falha ao analisar: {exc}")
    return {"style_dna": dna}


@router.post("/create")
async def create_remix(payload: RemixCreate, db: Session = Depends(get_db)):
    dna = payload.style_dna
    if dna is None and payload.source:
        dna = await AnalyzerAgent(job_id=None, emit=False).execute(source=payload.source)
    if dna is None:
        raise HTTPException(400, "forneça 'source' ou 'style_dna'")

    content_type = payload.content_type or dna.get("content_type", "film_recap_ai_images")
    job = VideoJob(
        title=payload.title,
        mode="from_remix",
        content_type=content_type,
        style_dna=dna,
        reference_url=payload.source,
        account_id=payload.account_id,
        target_platforms=payload.target_platforms,
        status=JobStatus.QUEUED,
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    transport = dispatch_job(job.id)
    return {"job": job.to_dict(), "dispatch": transport}
