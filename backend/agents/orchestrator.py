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
import logging
from datetime import datetime

from backend.config import settings
from backend.database import SessionLocal
from backend.events import publish_event
from backend.models import JobStatus, PlatformAccount, VideoJob

from backend.agents.research import ResearchAgent
from backend.agents.scriptwriter import ScriptwriterAgent
from backend.agents.growth import (
    HookOptimizerAgent,
    RetentionEngineerAgent,
    PackagingStrategistAgent,
    ShortsHookAgent,
    ShortsStrategistAgent,
)
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

logger = logging.getLogger("studio.orchestrator")


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
        voice = None
        language = settings.default_language
        if job.account_id:
            acct = db.get(PlatformAccount, job.account_id)
            if acct:
                voice = acct.preferred_voice or None
                language = acct.content_language or language
        # Foreign-language channel but the voice is still the pt-BR default? Let the
        # narrator pick a language-matched voice (the pt clone/voice can't speak English).
        if voice == "pt-BR-AntonioNeural" and not (language or "").lower().startswith("pt"):
            voice = None
        ctx["language"] = language  # narrator/agents read the channel language from here
        ctx["target_platforms"] = job.target_platforms or ["youtube", "tiktok", "instagram"]
        ctx["format"] = getattr(job, "video_format", "long") or "long"  # long(16:9) | short(9:16)
        if job.style_dna:
            ctx["style_dna"] = job.style_dna  # remix: editing_director wears its style

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
            upd(status=JobStatus.PROCESSING, progress=3, agent="research")

            # Ground factual topics in real sources BEFORE writing, so narration
            # states true facts instead of hallucinating (e.g. a fake match result).
            research = await ResearchAgent(job_id, ctx).execute(
                title=job.title, topic=job.topic, content_type=job.content_type,
            )
            # Learning loop: distill what's actually worked on THIS channel from
            # real analytics and feed it to the scriptwriter + SEO, so each new
            # video leans toward what performs. Pure Python aggregation — no extra
            # LLM/API cost (free-API constraint preserved). Silent until enough
            # measured videos exist.
            try:
                from backend.agents.performance import PerformanceInsights
                ctx["performance_insights"] = PerformanceInsights(db).prompt_block(job.account_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("performance insights skipped: %s", exc)
                ctx["performance_insights"] = ""
            upd(progress=10, agent="scriptwriter")

            script = await ScriptwriterAgent(job_id, ctx).execute(
                title=job.title, topic=job.topic, mode=job.mode,
                content_type=job.content_type, style_dna=job.style_dna, language=language,
                research=research, video_format=ctx["format"],
            )
            job.script = script
            job.content_type = script.get("content_type", job.content_type)

            # Growth agents — engineer the video to win the algorithm BEFORE it's
            # narrated (so the hook/CTA are actually spoken) and packaged.
            upd(progress=30, agent="hook_optimizer")
            script = await HookOptimizerAgent(job_id, ctx).execute(script=script)
            upd(progress=33, agent="retention_engineer")
            script = await RetentionEngineerAgent(job_id, ctx).execute(script=script)
            upd(progress=36, agent="packaging_strategist")
            await PackagingStrategistAgent(job_id, ctx).execute(
                script=script, target_platforms=ctx.get("target_platforms"))
            job.script = script
            job.content_type = script.get("content_type", job.content_type)

            # Remix style-match: if the reference video has NO voice-over, the remix
            # is music-driven too (no narration) — follow the reference's style.
            ref_dna = ctx.get("style_dna") or {}
            if job.mode == "from_remix" and (ref_dna.get("audio") or {}).get("has_narration") is False:
                ctx["narrate"] = False
            upd(progress=40, agent="narrator")

            await NarratorAgent(job_id, ctx).execute(voice=voice, language=language)
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

            if ctx["format"] == "short":
                # Native vertical short: the rendered main video IS the short. Skip
                # the long->short factory; register it so the shorts agents annotate it.
                main_path = main.get("main_video_path")
                ctx["shorts"] = [{"num": 1, "name": "native", "path": main_path,
                                  "length": main.get("duration"), "from_start": True}]
                job.shorts_paths = [main_path] if main_path else []
            else:
                await ShortsFactoryAgent(job_id, ctx).execute()
                job.shorts_paths = [s["path"] for s in ctx.get("shorts", [])]

            # Shorts specialists — hook overlay + per-platform publish package.
            upd(progress=83, agent="shorts_hook")
            await ShortsHookAgent(job_id, ctx).execute()
            upd(progress=84, agent="shorts_strategist")
            await ShortsStrategistAgent(job_id, ctx).execute()
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
            research = ctx.get("research", {}) or {}
            job.video_context = {
                "qc": qc,
                "compliance": comp,
                "music": ctx.get("music", {}).get("track", {}).get("file"),
                "caption_style": cap_style,
                "narration_duration": ctx.get("narration", {}).get("total_duration"),
                "scene_sources": [a.get("source") for a in ctx.get("visuals", {}).get("scene_assets", [])],
                "research_grounded": research.get("grounded", False),
                "research_sources": research.get("sources", [])[:6],
                "packaging": ctx.get("packaging", {}),
                "shorts_meta": [
                    {"num": s.get("num"), "name": s.get("name"), "path": s.get("path"),
                     "length": s.get("length"), "hook_overlay": s.get("hook_overlay"),
                     "recommended": s.get("recommended"), "captions": s.get("captions"),
                     "hashtags": s.get("hashtags")}
                    for s in ctx.get("shorts", [])
                ],
            }
            # Auto-publish (hands-off): a video tied to a channel skips the human
            # gate and goes straight to APPROVED. _job_publish_due then publishes it
            # (it needs scheduled_at <= now, so stamp one if the job has none — e.g.
            # a manual video with a channel selected). Without a channel, or with
            # AUTO_PUBLISH off, the approval gate stays.
            if settings.auto_publish and job.account_id is not None:
                if job.scheduled_at is None:
                    job.scheduled_at = datetime.utcnow()
                upd(status=JobStatus.APPROVED, progress=100, agent=None, approval_status="approved")
                _emit_job(job_id, status="approved", qc=qc, compliance=comp)
                # "schedule" mode: upload to YouTube NOW as scheduled (publishAt = the
                # future slot) instead of waiting for _job_publish_due — that's what
                # makes it show as "Agendado" and go public exactly at the slot.
                # "immediate" mode leaves publishing to _job_publish_due at the slot.
                if (settings.publish_mode or "schedule").lower() == "schedule":
                    try:
                        from backend.pipeline.dispatch import dispatch_publish
                        # Claim the job (PUBLISHING) BEFORE dispatching so _job_publish_due
                        # cannot also grab this still-APPROVED job and publish it a 2nd
                        # time. The publisher accepts PUBLISHING; orphan recovery handles
                        # it safely on restart.
                        job.status = JobStatus.PUBLISHING
                        db.commit()
                        dispatch_publish(job_id)
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("auto dispatch_publish (schedule) falhou: %s", exc)
                return {"status": "approved_auto", "qc": qc, "compliance": comp}

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
