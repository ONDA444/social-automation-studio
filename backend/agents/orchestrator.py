"""
OrchestratorAgent — drives the full production pipeline for one VideoJob and
persists state/artifacts to the DB, emitting WebSocket events throughout.

Order (captions & music are produced before the editor that consumes them):
  Scriptwriter -> Narrator -> Visuals -> EditingDirector -> MusicCurator
  -> CaptionAgent -> VideoEditor -> ShortsFactory -> SEOAgent
  -> QualityControl -> ComplianceAgent -> [AWAITING APPROVAL]

Publishing happens separately, only after human approval (see publisher).
Per-agent retry/backoff is handled inside BaseAgent.execute(); if a stage still
fails, the job is marked 'error' and the queue continues.
"""
from __future__ import annotations

import asyncio

from backend.config import settings
from backend.database import SessionLocal
from backend.events import publish_event
from backend.models import JobStatus, PlatformAccount, VideoJob

from backend.agents.scriptwriter import ScriptwriterAgent
from backend.agents.narrator import NarratorAgent
from backend.agents.visuals import VisualsAgent
from backend.agents.editing_director import EditingDirectorAgent
from backend.agents.music_curator import MusicCuratorAgent
from backend.agents.caption_agent import CaptionAgent
from backend.agents.video_editor import VideoEditorAgent
from backend.agents.shorts_factory import ShortsFactoryAgent
from backend.agents.seo_agent import SEOAgent
from backend.agents.quality_control import QualityControlAgent
from backend.agents.compliance_agent import ComplianceAgent


def _emit_job(job_id: int, **fields) -> None:
    publish_event({"type": "job_update", "job_id": job_id, **fields})


async def run_pipeline(job_id: int) -> dict:
    """Execute the pipeline for a job. Safe to call from Celery or in-process."""
    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return {"error": "job not found"}

        ctx: dict = dict(job.video_context or {})
        voice = settings.default_tts_voice
        language = settings.default_language
        if job.account_id:
            acct = db.get(PlatformAccount, job.account_id)
            if acct:
                voice = acct.preferred_voice or voice
                language = acct.content_language or language
        ctx["target_platforms"] = job.target_platforms or ["youtube", "tiktok", "instagram"]

        def upd(status=None, progress=None, agent=None, **extra):
            if status is not None:
                job.status = status
            if progress is not None:
                job.progress = progress
            if agent is not None:
                job.current_agent = agent
            for k, v in extra.items():
                setattr(job, k, v)
            db.commit()
            _emit_job(job_id, status=(job.status.value if hasattr(job.status, "value") else job.status),
                      progress=job.progress, current_agent=job.current_agent)

        try:
            upd(status=JobStatus.PROCESSING, progress=3, agent="scriptwriter")

            script = await ScriptwriterAgent(job_id, ctx).execute(
                title=job.title, topic=job.topic, mode=job.mode,
                content_type=job.content_type, style_dna=job.style_dna, language=language,
            )
            job.script = script
            job.content_type = script.get("content_type", job.content_type)
            upd(progress=40, agent="narrator")

            await NarratorAgent(job_id, ctx).execute(voice=voice)
            upd(progress=55, agent="visuals")

            await VisualsAgent(job_id, ctx).execute()
            upd(progress=64, agent="editing_director")

            plan = await EditingDirectorAgent(job_id, ctx).execute()
            job.editing_plan = plan
            upd(progress=68, agent="music_curator")

            await MusicCuratorAgent(job_id, ctx).execute()
            upd(progress=70, agent="caption_agent")

            style = (plan.get("text_style") or "bold_impact")
            cap_style = style if style in {"minimal", "bold_impact", "highlight_words", "karaoke", "tiktok_box"} else "bold_impact"
            await CaptionAgent(job_id, ctx).execute(style=cap_style)
            upd(progress=72, agent="video_editor")

            main = await VideoEditorAgent(job_id, ctx).execute()
            job.main_video_path = main.get("main_video_path")
            upd(progress=82, agent="shorts_factory")

            await ShortsFactoryAgent(job_id, ctx).execute()
            job.shorts_paths = [s["path"] for s in ctx.get("shorts", [])]
            upd(progress=86, agent="seo_agent")

            seo = await SEOAgent(job_id, ctx).execute(language=language)
            job.seo_metadata = seo
            upd(progress=88, agent="quality_control")

            qc = await QualityControlAgent(job_id, ctx).execute()
            job.qc_status = qc["status"]
            db.commit()
            if qc["status"] not in ("qc_passed", "qc_warning"):
                upd(status=JobStatus.ERROR, agent=None,
                    error_message=f"QC reprovou: {qc['status']} — {'; '.join(qc.get('warnings', []))}")
                _emit_job(job_id, status="error", qc=qc)
                return {"status": "error", "qc": qc}

            upd(progress=92, agent="compliance_agent")
            comp = await ComplianceAgent(job_id, ctx).execute()
            job.compliance_status = comp["status"]

            # thumbnail (variant A landscape) for the approval card
            thumbs = ctx.get("visuals", {}).get("thumbnails", {})
            job.thumbnail_path = thumbs.get("A", {}).get("landscape")

            # Keep stored context lean (heavy arrays live in their JSON files on disk).
            job.video_context = {
                "qc": qc,
                "compliance": comp,
                "music": ctx.get("music", {}).get("track", {}).get("file"),
                "caption_style": cap_style,
                "narration_duration": ctx.get("narration", {}).get("total_duration"),
                "scene_sources": [a.get("source") for a in ctx.get("visuals", {}).get("scene_assets", [])],
            }
            upd(status=JobStatus.AWAITING_APPROVAL, progress=100, agent=None, approval_status="pending")
            _emit_job(job_id, status="awaiting_approval", qc=qc, compliance=comp)
            return {"status": "awaiting_approval", "qc": qc, "compliance": comp}

        except Exception as exc:  # noqa: BLE001
            upd(status=JobStatus.ERROR, agent=None, error_message=str(exc)[:500])
            _emit_job(job_id, status="error", error=str(exc)[:500])
            return {"status": "error", "error": str(exc)}
    finally:
        db.close()


if __name__ == "__main__":
    import sys

    jid = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    print(asyncio.run(run_pipeline(jid)))
