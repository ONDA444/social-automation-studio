"""Manual video upload — analyze a user-picked local file and package it for Approvals.

Reuses ready_video_seo's analysis/SEO engine (it only reads name/folder_path/niche
off a duck-typed "ready" object, never a real ReadyVideo row) so a video uploaded
from the user's own PC gets the same title/description/tags treatment as a Drive
ready-video, without touching Drive's rotation/inventory — this is a standalone,
one-off job, not a member of that pool.
"""
from __future__ import annotations

import asyncio
import logging
import os

import re

from backend.agents.ready_video_seo import OPERATIONAL_WORDS
from backend.database import SessionLocal
from backend.events import publish_event
from backend.models import JobStatus, VideoJob

logger = logging.getLogger("studio.manual_upload")

_TOKEN_RE = re.compile(r"[a-zA-Z0-9À-ÿ]+")
# Generic words a user's own filename commonly contains that carry no real
# content signal (unlike a specific brand/game/topic word) — excluded so a
# plain name like "meu_video_novo.mp4" doesn't produce a false "known" token.
_GENERIC_FILENAME_WORDS = {
    "meu", "minha", "meus", "minhas", "novo", "nova", "novos", "novas",
    "arquivo", "projeto", "teste", "clipe", "gravacao", "gravação",
}


def _meaningful_tokens(text: str | None) -> set[str]:
    if not text:
        return set()
    return {
        t for t in (m.lower() for m in _TOKEN_RE.findall(text))
        if len(t) >= 3 and not t.isdigit()
        and t not in OPERATIONAL_WORDS and t not in _GENERIC_FILENAME_WORDS
    }


def _looks_hallucinated(analysis: dict, filename: str) -> bool:
    """A vision-LLM analysis with zero overlap against the file's own name is a
    strong sign the model fabricated a plausible-sounding but unrelated story
    instead of describing the real frames — this happened with a gameplay
    video (filename "ONDA_HUB_COMPLETO...") described as a sitcom about
    fictional characters. Deliberately filename-only, not niche: a niche is a
    broad category ("comedia") that legitimately won't appear verbatim in a
    specific video's description, so using it here would false-positive on
    perfectly good analyses. Only fires when the filename actually HAS a
    specific-looking token to check against, to avoid flagging generic names."""
    known = _meaningful_tokens(os.path.splitext(filename)[0])
    if not known:
        return False
    described = " ".join([
        analysis.get("summary") or "",
        " ".join(analysis.get("entities") or []),
        " ".join(analysis.get("topics") or []),
        " ".join(analysis.get("keywords") or []),
    ])
    described_tokens = _meaningful_tokens(described)
    return known.isdisjoint(described_tokens)

# Same ceiling as the YouTube-upload/Drive-download timeouts in publisher.py: a
# hung ffprobe/ffmpeg/Gemini call must free the render-semaphore slot and
# surface as a normal job ERROR instead of wedging the pipeline forever. Kept
# short (see publisher.py's _UPLOAD_TIMEOUT_S comment) -- a dead network call
# shows zero forward progress for its entire duration, so waiting longer only
# delays the failure/retry without ever helping it succeed.
_ANALYZE_TIMEOUT_S = 300


class _UploadContext:
    """Duck-typed stand-in for a ReadyVideo row — ready_video_seo only reads
    these three attributes off the `ready` argument it's given."""

    def __init__(self, name: str, folder_path: str | None, niche: str | None):
        self.name = name
        self.folder_path = folder_path
        self.niche = niche


def _emit(job_id, **fields) -> None:
    publish_event({"type": "manual_upload_update", "job_id": job_id, **fields})


async def run_analyze_upload(job_id: int) -> None:
    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return
        try:
            # _analyze_sync opens its OWN session (job_id only, not this
            # coroutine's `db`/`job`) — SQLAlchemy Sessions are not thread-safe,
            # and sharing one across the event-loop thread and this off-thread
            # worker is undefined behavior that can silently deadlock the
            # underlying DBAPI connection with ZERO cpu usage, indistinguishable
            # from a hung network call.
            await asyncio.wait_for(
                asyncio.to_thread(_analyze_sync, job_id), timeout=_ANALYZE_TIMEOUT_S
            )
            db.refresh(job)
        except asyncio.TimeoutError:
            job.status = JobStatus.ERROR
            job.error_message = "Análise do vídeo demorou demais (timeout)."
            db.commit()
            _emit(job_id, status="error", error=job.error_message)
    except Exception as exc:  # noqa: BLE001 — a job must never hang in PROCESSING
        logger.exception("run_analyze_upload failed for job %s", job_id)
        try:
            job = db.get(VideoJob, job_id)
            if job:
                job.status = JobStatus.ERROR
                job.error_message = f"Falha ao analisar vídeo: {exc}"[:500]
                db.commit()
                _emit(job_id, status="error", error=str(exc))
        except Exception:  # noqa: BLE001
            logger.exception("could not persist ERROR status for upload job %s", job_id)
    finally:
        db.close()


def _analyze_sync(job_id: int) -> None:
    """Runs on a worker thread (see run_analyze_upload) — opens its own DB
    session (see that function's comment for why). ffprobe, frame extraction,
    and the optional Gemini call are all blocking calls, each with its own
    explicit timeout inside ready_video_seo."""
    from backend.agents.ready_video_seo import build_ready_video_package
    from backend.models import PlatformAccount

    db = SessionLocal()
    try:
        job = db.get(VideoJob, job_id)
        if not job:
            return
        local_path = job.main_video_path
        if not local_path or not os.path.exists(local_path):
            job.status = JobStatus.ERROR
            job.error_message = "Arquivo de vídeo não encontrado após o upload."
            db.commit()
            return

        account = db.get(PlatformAccount, job.account_id) if job.account_id else None
        ctx = dict(job.video_context or {})
        hint = (ctx.get("hint") or "").strip()
        ready = _UploadContext(
            name=os.path.basename(local_path),
            folder_path=None,
            niche=getattr(account, "niche", None) if account else None,
        )

        analysis, seo = build_ready_video_package(
            job_id=job.id,
            local_path=local_path,
            ready=ready,
            account=account,
            content_type=job.content_type or "film_recap_ai_images",
            video_format=job.video_format or "long",
            title_seed=hint or job.title,
        )

        # A file ffprobe genuinely can't read is a different failure than "AI
        # analysis unavailable" — ready_video_seo's deterministic fallback would
        # still package SOMETHING, which would be actively misleading for a file
        # that isn't a readable video at all.
        if analysis.get("status") != "ok":
            job.status = JobStatus.ERROR
            job.error_message = "Arquivo de vídeo inválido ou corrompido."
            job.video_context = {**ctx, "content_analysis": analysis}
            db.commit()
            _emit(job.id, status="error", error=job.error_message)
            return

        if analysis.get("analysis_source") == "vision_llm" and _looks_hallucinated(
            analysis, os.path.basename(local_path)
        ):
            job.status = JobStatus.ERROR
            job.error_message = (
                "A IA não conseguiu identificar com segurança o conteúdo do vídeo "
                "(a descrição gerada não bate com nada conhecido sobre o arquivo). "
                "Revise manualmente antes de tentar de novo."
            )
            job.video_context = {**ctx, "content_analysis": analysis, "seo_source": "vision_llm_rejected"}
            db.commit()
            _emit(job.id, status="error", error=job.error_message)
            return

        job.seo_metadata = seo
        yt_title = ((seo.get("youtube") or {}).get("title") or job.title or "").strip()
        if yt_title:
            job.title = yt_title[:300]
            job.topic = yt_title

        if analysis.get("aspect_ratio") == "9:16":
            job.video_format = "short"
            job.shorts_paths = [local_path]
        elif analysis.get("aspect_ratio") == "16:9":
            job.video_format = "long"

        ctx["content_analysis"] = analysis
        ctx["seo_source"] = analysis.get("analysis_source") or "fallback"
        job.video_context = ctx
        job.status = JobStatus.AWAITING_APPROVAL
        job.approval_status = "pending"
        job.qc_status = "skipped_manual_upload"
        job.compliance_status = "needs_rights_review"
        db.commit()
        _emit(job.id, status="awaiting_approval")
    finally:
        db.close()
