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


def _channel_config_from_account(acct) -> dict:
    """Map a PlatformAccount → the scriptwriter's CHANNEL CONFIG shape.

    Without this the scriptwriter ran on bare defaults — the channel's niche,
    audience, tone and (critically) `avoid_topics` never reached the script, so a
    video never honoured "don't talk about X". Only meaningful values are set so the
    scriptwriter's CHANNEL_DEFAULTS still fill the rest."""
    cfg: dict = {}
    if getattr(acct, "display_name", None):
        cfg["channel_name"] = acct.display_name
    if getattr(acct, "content_language", None):
        cfg["language"] = acct.content_language
    identity: dict = {}
    if getattr(acct, "niche", None) and acct.niche.strip():
        identity["niche"] = acct.niche.strip()
    if getattr(acct, "target_audience", None):
        identity["target_audience"] = acct.target_audience
    if identity:
        cfg["identity"] = identity
    tone = getattr(acct, "content_tone", None)
    if tone and tone != "neutral":
        cfg["voice"] = {"tone": tone}
    avoid = [t for t in (getattr(acct, "avoid_topics", None) or []) if t]
    if avoid:
        cfg["guardrails"] = {"forbidden_topics": avoid}
    return cfg


async def run_pipeline(job_id: int) -> dict:
    """Execute the pipeline for a job. Safe to call from Celery or in-process."""
    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return {"error": "job not found"}

        ctx: dict = dict(job.video_context or {})
        # Human-initiated jobs (the "Novo vídeo" form / CSV import) are flagged so
        # they always stop at the approval gate — the user decides whether to post.
        # Only the scheduler's hands-off automation auto-publishes. Captured BEFORE
        # video_context is rebuilt below (the rebuild would otherwise drop it).
        require_approval = bool(ctx.get("require_approval"))
        # Trending freshness guard: a moment that took too long to render (stuck behind
        # retries past its window) is stale — force it through human Approval instead of
        # auto-publishing an out-of-date "moment".
        if ctx.get("is_trending") and not require_approval and job.created_at:
            age_h = (datetime.utcnow() - job.created_at).total_seconds() / 3600
            if age_h > settings.trending_freshness_ttl_h:
                require_approval = True
                logger.info("Trending job %s stale (%.1fh) -> approval, not auto-publish.", job_id, age_h)
        voice = None
        language = settings.default_language
        music_style = "balanced"  # calm | balanced | energetic (per-channel music vibe)
        if job.account_id:
            acct = db.get(PlatformAccount, job.account_id)
            if acct:
                voice = acct.preferred_voice or None
                language = acct.content_language or language
                music_style = getattr(acct, "music_style", None) or "balanced"
                # Feed the channel's identity (niche, audience, tone, avoid_topics)
                # to the scriptwriter — was never populated, so the script ignored
                # the channel's rules (incl. "don't talk about X").
                ctx["channel_config"] = _channel_config_from_account(acct)
        # Foreign-language channel but the voice is still the pt-BR default? Let the
        # narrator pick a language-matched voice (the pt clone/voice can't speak English).
        if voice == "pt-BR-AntonioNeural" and not (language or "").lower().startswith("pt"):
            voice = None
        ctx["language"] = language  # narrator/agents read the channel language from here
        ctx["voice"] = voice        # channel's configured voice, available to all agents
        ctx["music_style"] = music_style  # editing_director adapts the music vibe per channel
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
                # The real headline of a trending moment (stored at creation) is the
                # strongest factual anchor — pass it so grounding targets the actual
                # event, not the LLM's vague 1-line angle.
                trend_evidence=ctx.get("trend_evidence", ""),
            )
            if research.get("risk_flag") and not require_approval:
                # Theme matches this channel's avoid_topics (e.g. the exact
                # franchise/team that already earned a Content ID strike) —
                # don't auto-publish blind, same treatment as a stale trending job.
                require_approval = True
                logger.info("Job %s theme matches avoid_topics -> approval, not auto-publish.", job_id)
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
            db.commit()
            if comp["status"] == "blocked":
                upd(status=JobStatus.ERROR, agent=None,
                    error_message=f"Compliance bloqueou: {'; '.join(comp.get('blocks', []))}")
                _emit_job(job_id, status="error", compliance=comp)
                return {"status": "error", "compliance": comp}

            # Thumbnail A/B: alternate deterministically by job id so the two
            # variants VisualsAgent already renders (and analytics.py already
            # groups CTR by, reading the A/B back from this exact path's
            # filename — see AnalyticsAgent._thumbnail_variant) actually both
            # ship. Always picking "A" meant the "A/B test" never had any B
            # data to compare against.
            thumbs = ctx.get("visuals", {}).get("thumbnails", {})
            variant = "B" if job_id % 2 else "A"
            job.thumbnail_path = (thumbs.get(variant, {}).get("landscape")
                                   or thumbs.get("A", {}).get("landscape"))

            # Voice integrity is now best-effort at SYNTHESIS time (narrator tries same-
            # gender edge voices before any gTTS fallback). We do NOT force a fallback
            # render into Approval: on Railway edge-tts is routinely blocked, so gating on
            # it would send EVERY automated video to Approval — the opposite of what the
            # operator wants. The fallback is still recorded in video_context below for
            # visibility, and only logged as a warning here.
            narration = ctx.get("narration", {}) or {}
            voice_fallback_used = bool(narration.get("voice_fallback_used"))
            if voice_fallback_used:
                logger.warning("Job %s: voz em fallback (%s) — publicando mesmo assim (auto).",
                               job_id, narration.get("tts_provider"))
                # Auto-published videos skip human review, so the fallback flag must be
                # surfaced somewhere more visible than a log line — emit a dedicated
                # WS event the operator dashboard can badge/filter on.
                _emit_job(job_id, status="voice_fallback", voice_fallback_used=True,
                          tts_provider=narration.get("tts_provider"))

            # Keep stored context lean (heavy arrays live in their JSON files on disk).
            research = ctx.get("research", {}) or {}
            job.video_context = {
                "qc": qc,
                "compliance": comp,
                "music": ctx.get("music", {}).get("track", {}).get("file"),
                "caption_style": cap_style,
                "narration_duration": ctx.get("narration", {}).get("total_duration"),
                "tts_provider": narration.get("tts_provider"),
                "voice_fallback_used": bool(narration.get("voice_fallback_used")),
                "voice": narration.get("voice"),
                "scene_sources": [a.get("source") for a in ctx.get("visuals", {}).get("scene_assets", [])],
                "require_approval": require_approval,  # persist for restart/re-run
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
            # (it needs scheduled_at <= now, so stamp one if the job has none). This
            # is ONLY for the scheduler's automation — a manually created video
            # (require_approval) always stops at the gate so the user can choose to
            # post or not. Without a channel, or with AUTO_PUBLISH off, the gate stays.
            if settings.auto_publish and job.account_id is not None and not require_approval:
                if job.scheduled_at is None:
                    job.scheduled_at = datetime.utcnow()
                upd(status=JobStatus.APPROVED, progress=100, agent=None, approval_status="approved")
                _emit_job(job_id, status="approved", qc=qc, compliance=comp,
                          voice_fallback_used=voice_fallback_used)
                # "schedule" mode: upload to YouTube NOW as scheduled (publishAt = the
                # future slot) instead of waiting for _job_publish_due — that's what
                # makes it show as "Agendado" and go public exactly at the slot.
                # "immediate" mode leaves publishing to _job_publish_due at the slot.
                # This early-dispatch trick ONLY works for YouTube: TikTok and
                # Instagram have no publish_at/schedule support in run_publish, so
                # dispatching now would make them go live immediately regardless of
                # scheduled_at. So only YouTube gets dispatched early (as a platform
                # SUBSET, via dispatch_publish(platforms=...)) — TikTok/Instagram are
                # left untouched for _job_publish_due to publish once the slot
                # actually arrives. run_publish (backend/agents/publisher.py) keeps
                # the job APPROVED after a successful partial (YouTube-only) run, so
                # the scheduler can still pick it up for the remaining platforms.
                target_platforms = list(dict.fromkeys(job.target_platforms or ["youtube"]))
                youtube_now = [p for p in target_platforms if p == "youtube"]
                if youtube_now and (settings.publish_mode or "schedule").lower() == "schedule":
                    try:
                        from backend.pipeline.dispatch import dispatch_publish
                        # Claim the job (PUBLISHING) BEFORE dispatching so _job_publish_due
                        # cannot also grab this still-APPROVED job and publish it a 2nd
                        # time. The publisher accepts PUBLISHING; orphan recovery handles
                        # it safely on restart. If other platforms remain untouched,
                        # run_publish puts the job back to APPROVED once the YouTube
                        # upload finishes, so the scheduler can still reach them later.
                        job.status = JobStatus.PUBLISHING
                        db.commit()
                        dispatch_publish(job_id, platforms=youtube_now)
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
