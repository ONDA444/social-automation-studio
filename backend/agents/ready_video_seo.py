"""SEO packaging for ready-to-post Drive videos.

This module is intentionally lighter than the full AI generation pipeline. It
probes the selected video after download, optionally asks Gemini to interpret a
few tiny frames, then builds platform metadata. If any AI step fails, it falls
back to deterministic editorial packaging so publishing keeps moving.
"""
from __future__ import annotations

import base64
import json
import logging
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx

from backend import llm
from backend.agents.seo_agent import YT_CATEGORY, apply_runtime_youtube_enrichment_sync
from backend.config import settings

logger = logging.getLogger("studio.ready_video_seo")

GEMINI_VISION_MODEL = "gemini-2.5-flash"
GEMINI_VISION_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)
OPERATIONAL_WORDS = {
    "video", "vid", "final", "edit", "edited", "export", "render", "upload",
    "pronto", "postar", "corte", "cortes", "parte", "part", "short", "shorts",
    "reels", "tiktok", "youtube", "yt", "fullhd", "hd", "fhd",
    "legendado", "legenda", "legendas", "subtitle", "subtitles",
    "monetize", "monetizacao", "monetização", "monetizar",
}
OPERATIONAL_WORDS.add("epic")
VIRAL_SHORTS_LEARNINGS = {
    "source": "user_top_short_2026_07",
    "views": 5_950_751,
    "subscribers": 5_400,
    "shorts_feed_share": 0.948,
    "retention_continued": 0.775,
    "duration_seconds": 18,
    "avg_view_seconds": 19,
}
VIRAL_TITLE_MARKERS = (
    "mas", "olha", "depois", "chocou", "final", "detalhe", "ninguém",
    "ninguem", "percebeu", "resolveu", "falhou", "mudou",
)
CONFLICT_WORDS = (
    "perde", "perdeu", "falha", "falhou", "cai", "caiu", "erra", "errou",
    "quebra", "quebrou", "foge", "fugiu", "surpreende", "surpreendeu",
    "descobre", "descobriu", "tenta", "tentou",
)


def build_ready_video_package(
    *,
    job_id: int,
    local_path: str,
    ready: Any,
    account: Any,
    content_type: str,
    video_format: str,
    title_seed: str | None = None,
) -> tuple[dict, dict, str | None]:
    """Return `(analysis, seo, thumbnail_path)` for a downloaded ready video.

    The function never raises for analysis/AI failures. A corrupt or missing file
    is reported in `analysis.warnings`, then the deterministic SEO path still
    produces metadata from filename/folder/channel context. `thumbnail_path` is
    None whenever generation fails or only produced the deterministic local
    placeholder — publishing must never be blocked or forced onto a bad cover;
    the caller just leaves YouTube's own auto-picked frame in that case.
    """
    context = _context_dict(ready, account, content_type, video_format, title_seed)
    analysis = analyze_ready_video(job_id=job_id, local_path=local_path, context=context)
    # Grep-able monitoring signal: how often does title/SEO generation actually
    # get real AI grounding (vision_llm/text_llm) vs falling back to the fixed
    # per-content_type template (probe_fallback/metadata_fallback)? A fallback
    # rate that's consistently high is exactly what produces the repetitive,
    # templated titles/descriptions that read as "reused content" to YouTube.
    logger.info(
        "ready_video_analysis_source job_id=%s account_id=%s content_type=%s source=%s",
        job_id, getattr(account, "id", None), content_type, analysis.get("analysis_source"),
    )
    if analysis.get("aspect_ratio") == "9:16":
        context["video_format"] = "short"
    elif analysis.get("aspect_ratio") == "16:9" and context.get("video_format") != "short":
        context["video_format"] = "long"
    seo = build_drive_seo(context=context, analysis=analysis)
    apply_runtime_youtube_enrichment_sync(seo)
    thumbnail_path = _generate_thumbnail(job_id, context, analysis)
    return analysis, seo, thumbnail_path


async def _craft_image_prompt(context: dict, analysis: dict) -> str | None:
    """Text-to-image models can't render abstract marketing/business language
    ("otimização de FPS", "ativação de licença") — they need a concrete VISUAL
    scene. Ask the LLM to translate the content analysis into one, instead of
    just concatenating the title/hook (which produced a generic, unrelated
    "adventurer in water" stock-photo-style image for a software-UI video)."""
    if not llm.available():
        return None
    title = context.get("title_seed") or analysis.get("summary") or context.get("drive_name") or ""
    summary = analysis.get("summary") or ""
    topics = ", ".join((analysis.get("topics") or [])[:5])
    niche = context.get("niche") or ""
    prompt = f"""
Escreva um prompt de imagem em ingles para um gerador texto-para-imagem (FLUX),
representando visualmente este video como capa de YouTube. Descreva uma CENA
CONCRETA (objetos, ambiente, cores, iluminacao, composicao) — NUNCA linguagem
de marketing ou conceitos abstratos (nao diga "optimization", "activation",
"promotional"). Se o video for sobre software/tela de computador, descreva a
TELA/interface visivel, nao uma metafora. Maximo 35 palavras, termine com
"youtube thumbnail, high contrast, dramatic lighting".

Titulo: {title}
Resumo: {summary}
Temas: {topics}
Nicho: {niche}
""".strip()
    try:
        text = await llm.complete(
            prompt,
            system="Voce e um diretor de arte especialista em prompts para geracao de imagem.",
            max_tokens=150,
            fast=True,
        )
        return text.strip().strip('"') or None
    except Exception as exc:  # noqa: BLE001
        logger.info("Image-prompt crafting failed: %s", exc)
        return None


def _generate_thumbnail(job_id: int, context: dict, analysis: dict) -> str | None:
    """Best-effort AI-generated cover image, reusing the same multi-provider
    engine (Pollinations/FLUX -> HF FLUX -> Pexels/Pixabay photo -> local
    placeholder) the full AI-generation pipeline already uses for its own
    thumbnails (backend/agents/visuals.py). Never raises; returns None on any
    failure OR when every provider failed down to the local placeholder, since
    a generic placeholder image looks worse than YouTube's own auto-picked
    frame from the actual video."""
    import asyncio

    from backend.agents.visuals import VisualsAgent

    title = (context.get("title_seed") or analysis.get("summary") or context.get("drive_name") or "").strip()
    if not title:
        return None
    hook = (analysis.get("hook") or "").strip()
    topics = ", ".join((analysis.get("topics") or [])[:4])
    fallback_prompt = f"{title}. {hook}. Temas: {topics}".strip(". ") or title
    fallback_prompt = f"{fallback_prompt}, capa de video do YouTube, chamativa, alto contraste"

    dst = Path(settings.abs_path(settings.temp_dir)) / "thumbnails" / f"job_{job_id}.jpg"
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        agent = VisualsAgent(job_id=job_id, emit=False)

        async def _run() -> str:
            crafted = await _craft_image_prompt(context, analysis)
            return await agent._generate_image(crafted or fallback_prompt, dst, 1280, 720)

        provider = asyncio.run(_run())
    except Exception as exc:  # noqa: BLE001
        logger.info("Thumbnail generation failed for job %s: %s", job_id, exc)
        return None
    if provider == "placeholder":
        return None

    # Compose the same big-text/scrim overlay every AI-generation thumbnail gets
    # (VisualsAgent._compose_thumb) on top of the raw generated image. Without
    # this, Drive-sourced videos — the bulk of the catalog — published with a
    # bare AI image and zero text overlay, skipping one of the strongest known
    # CTR levers. Falls back to the un-composed image if this best-effort step
    # fails for any reason.
    final = dst.with_name(f"job_{job_id}_thumb.jpg")
    try:
        cfg = {"pos": "bottom", "fill": (255, 221, 0), "stroke": (200, 0, 0)}
        agent._compose_thumb(dst, final, _thumbnail_text(title), 1280, 720, cfg)
        return str(final)
    except Exception as exc:  # noqa: BLE001
        logger.info("Thumbnail composition failed for job %s: %s", job_id, exc)
        return str(dst)


def is_audio_ready(name: str | None, mime_type: str | None) -> bool:
    """Same check drive_library.py uses to spot music-only Drive folders."""
    from backend.agents.drive_library import is_audio_file

    return is_audio_file(name or "", mime_type)


def render_audio_track_as_video(
    job_id: int, audio_path: str, title_seed: str, niche: str, video_format: str
) -> str:
    """Wrap a music track (a bare Drive audio niche has nothing else to
    publish — see drive_library.py's is_audio_file scan) into a real MP4:
    a generated still cover image held for the whole track length, muxed
    with the original audio. YouTube has no "audio-only upload"; every
    publish needs a video stream, so this is the minimum viable one —
    matches the "static cover" music-channel format the user asked for.
    Raises on failure (unlike the best-effort thumbnail helpers above) —
    without a video there is nothing to publish, so the caller must see it."""
    import asyncio

    from backend.agents.visuals import VisualsAgent

    out_dir = Path(settings.abs_path(settings.temp_dir)) / "ready_videos" / f"job_{job_id}"
    out_dir.mkdir(parents=True, exist_ok=True)
    cover_raw = out_dir / "cover_raw.jpg"
    cover_final = out_dir / "cover.jpg"
    video_out = out_dir / "audio_as_video.mp4"

    is_short = video_format == "short"
    h = max(360, min(1080, settings.video_resolution))
    w = (round(h * 16 / 9)) & ~1
    if is_short:
        w, h = h, (round(h * 16 / 9)) & ~1  # portrait: swap so height is the long side

    title = (title_seed or Path(audio_path).stem or "Musica").strip()
    prompt = (
        f"capa de album para a musica '{title}', estilo {niche or 'ambiente'}, "
        "arte abstrata, cores suaves, alto contraste, sem texto, sem rosto"
    )
    agent = VisualsAgent(job_id=job_id, emit=False)
    asyncio.run(agent._generate_image(prompt, cover_raw, w, h))
    try:
        cfg = {"pos": "bottom", "fill": (255, 255, 255), "stroke": (10, 10, 10)}
        agent._compose_thumb(cover_raw, cover_final, _thumbnail_text(title), w, h, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.info("Music cover composition failed for job %s, using raw image: %s", job_id, exc)
        cover_final = cover_raw

    # -threads is NOT optional here: left to auto, x264 spawns one thread per
    # HOST core (60+ on Railway) and the per-thread buffers blow the container
    # memory limit -> SIGKILL (rc=-9) a few seconds into the encode, with no
    # error text in stderr at all. video_editor.py's VENC learned this the hard
    # way; the first version of this function didn't reuse that cap and died in
    # production for exactly that reason. -r 10 also matters: at the default
    # 25/30fps a 6-minute track is 9-10k frames of an unchanging image, which
    # is pure wasted encode time (and the 600s timeout below would trip on
    # longer tracks). A still cover at 10fps is well within what YouTube accepts.
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(cover_final), "-i", audio_path,
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", "10",
        "-threads", str(max(1, settings.ffmpeg_threads)),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-c:a", "aac", "-b:a", "192k", "-shortest", str(video_out),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=900)
    if proc.returncode != 0 or not video_out.exists() or video_out.stat().st_size == 0:
        # Keep BOTH ends of stderr: ffmpeg's real error is on the LAST line, but
        # callers truncate the composed message to 500 chars, which used to cut
        # exactly that line off and leave only useless progress spam. Also
        # surface returncode — a negative value (e.g. -9 = SIGKILL) is the only
        # way to tell an OOM kill apart from a genuine ffmpeg error.
        err = (proc.stderr or "").strip()
        tail = err[-260:] if len(err) > 260 else err
        raise RuntimeError(
            f"ffmpeg falhou (rc={proc.returncode}) ao gerar video a partir do audio: ...{tail}"
        )
    return str(video_out)


def analyze_ready_video(job_id: int, local_path: str, context: dict) -> dict:
    probe = _probe(local_path)
    analysis: dict = {
        "status": "ok" if probe else "fallback",
        "analysis_source": "probe",
        "warnings": [] if probe else ["ffprobe unavailable or unreadable video"],
        "analyzed_at": datetime.utcnow().isoformat() + "Z",
        **(probe or {}),
    }
    if probe:
        analysis["aspect_ratio"] = _aspect_ratio(probe.get("width"), probe.get("height"))

    frames: list[str] = []
    if probe and probe.get("duration", 0) > 0:
        frames = _extract_frames(local_path, job_id, float(probe.get("duration") or 0))
        if frames:
            analysis["frames_sampled"] = len(frames)

    enriched = _describe_with_gemini(frames, context, analysis) if frames else None
    if enriched is None:
        enriched = _describe_with_text_llm(context, analysis)
    if isinstance(enriched, dict):
        analysis.update(_normalise_analysis(enriched))
        analysis["analysis_source"] = "vision_llm" if frames else "text_llm"
    else:
        analysis.update(_fallback_analysis(context))
        analysis["analysis_source"] = "probe_fallback" if probe else "metadata_fallback"

    return analysis


def build_drive_seo(context: dict, analysis: dict | None = None) -> dict:
    analysis = analysis or {}
    video_format = context.get("video_format") or "long"
    content_type = context.get("content_type") or "film_recap_ai_images"
    topic = _best_topic(context, analysis)
    entities = _clean_terms(analysis.get("entities") or [])
    topics = _clean_terms(analysis.get("topics") or [])
    niche = _clean_text(context.get("niche") or context.get("account_niche") or "")

    title = _title_from_analysis(topic, analysis, content_type, video_format)
    title = _apply_viral_shorts_title(title, topic, analysis, content_type, video_format)
    title = title.rstrip(" .")
    if video_format == "short" and "#short" not in title.lower():
        title = f"{title} #Shorts"
    title = title[:100]

    primary = _primary_keyword(topic, entities, topics, niche)
    tags = _tags(primary, topic, entities, topics, niche, content_type, video_format)
    # Real search-behavior grounding (see keyword_research.py docstring): top up
    # with actual Google Trends queries related to this video's own primary
    # keyword, same "LLM/analysis picks stay first" top-up pattern as the rest
    # of _tags — best-effort, never blocks SEO on failure.
    from backend.agents.keyword_research import region_for_language, related_search_queries

    lang_code = context.get("language") or "pt-BR"
    real_queries = related_search_queries(primary or topic, lang_code, region_for_language(lang_code))
    if real_queries:
        lowered = {t.lower() for t in tags}
        for q in real_queries:
            if len(tags) >= 18:
                break
            if q.lower() not in lowered:
                tags.append(q)
                lowered.add(q.lower())
    yt_hashtags = _yt_hashtags(tags, video_format)
    social_hashtags = _social_hashtags(tags, video_format)
    hook = _clean_text(analysis.get("hook") or _hook(topic, content_type))
    summary = _clean_text(analysis.get("summary") or "")
    if not summary:
        summary = _summary(topic, niche, content_type)
    viral_profile = _viral_shorts_profile(title, topic, hook, summary, tags, video_format)
    description = _description(hook, summary, yt_hashtags, viral_profile)
    score = _score(title, description, tags, yt_hashtags, analysis)

    return {
        "search": {
            "search_seed": primary,
            "long_tail_variants": _long_tail(primary, topic),
            "title_keyword": primary,
        },
        "feed": {
            "entities": entities[:8] or tags[:5],
            "cluster_terms": topics[:6] or tags[:5],
            "suggested_next_to": _suggested_next_to(niche, content_type),
            "playlist_target": _playlist_target(niche, content_type),
            "viral_shorts_profile": viral_profile,
        },
        "fyp": {
            "tiktok": {
                "completion_play": "titulo direto e promessa clara",
                "rewatch_play": "gancho curto para rever o detalhe",
                "save_play": "valor de nostalgia ou utilidade do nicho",
                "share_play": "identificacao com o tema",
                "comment_play": _first_comment(topic, content_type),
                "first_comment": _first_comment(topic, content_type),
            },
            "instagram": {
                "completion_play": "caption curta com contexto",
                "rewatch_play": "chamada para perceber o detalhe",
                "save_play": "contexto do nicho",
                "share_play": "nostalgia e identificacao",
                "comment_play": _first_comment(topic, content_type),
                "first_comment": _first_comment(topic, content_type),
            },
        },
        "youtube": {
            "title": title,
            "description": description,
            "tags": tags,
            "category_id": YT_CATEGORY.get(content_type, "22"),
            "thumbnail_text": _thumbnail_text(title),
            "pinned_comment": viral_profile["comment_prompt"],
        },
        "tiktok": {
            "caption": f"{_strip_shorts(title)} {' '.join(social_hashtags[:5])} #fyp"[:150],
        },
        "instagram": {
            "caption": f"{_strip_shorts(title)}\n\n{summary}\n\n{_first_comment(topic, content_type)}"[:2200],
            "hashtags": social_hashtags[:12],
        },
        "seo_score": score,
        "seo_notes": _notes(score, analysis),
    }


def _context_dict(ready: Any, account: Any, content_type: str, video_format: str, title_seed: str | None) -> dict:
    return {
        "title_seed": title_seed or "",
        "drive_name": getattr(ready, "name", "") or "",
        "folder_path": getattr(ready, "folder_path", "") or "",
        "niche": getattr(ready, "niche", None) or getattr(account, "drive_niche", None) or getattr(account, "niche", None) or "",
        "account_niche": getattr(account, "niche", "") or "",
        "display_name": getattr(account, "display_name", "") or "",
        "target_audience": getattr(account, "target_audience", "") or "",
        "tone": getattr(account, "content_tone", "") or "",
        "language": getattr(account, "content_language", None) or settings.default_language,
        "content_type": content_type,
        "video_format": video_format,
    }


def _probe(path: str) -> dict | None:
    try:
        out = subprocess.run(
            [
                "ffprobe", "-v", "error", "-print_format", "json",
                "-show_streams", "-show_format", str(path),
            ],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=15,
        )
        if out.returncode != 0 or not out.stdout:
            return None
        data = json.loads(out.stdout)
        streams = data.get("streams") or []
        vstream = next((s for s in streams if s.get("codec_type") == "video"), None)
        if not vstream:
            return None
        fmt = data.get("format") or {}
        br = fmt.get("bit_rate") or vstream.get("bit_rate")
        fps = _fps(vstream.get("avg_frame_rate") or vstream.get("r_frame_rate"))
        return {
            "width": int(vstream.get("width") or 0),
            "height": int(vstream.get("height") or 0),
            "duration": round(float(fmt.get("duration") or 0), 2),
            "bitrate": int(br) if br else 0,
            "fps": fps,
            "vcodec": vstream.get("codec_name"),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
        }
    except Exception as exc:  # noqa: BLE001
        logger.debug("ready video probe failed: %s", exc)
        return None


def _extract_frames(path: str, job_id: int, duration: float) -> list[str]:
    if duration <= 0:
        return []
    base = Path(settings.abs_path(settings.temp_dir)) / "ready_video_frames" / f"job_{job_id}"
    base.mkdir(parents=True, exist_ok=True)
    # 6 points instead of 3, and higher res/quality: 3 sparse, tiny (360px,
    # heavily compressed) frames landed too often on a loading screen, static
    # HUD, or otherwise uninformative moment for content whose interesting
    # parts are brief (e.g. gameplay) — the vision model then fabricated a
    # plausible-sounding but unrelated story instead of admitting uncertainty.
    # More/better samples raise the odds at least one frame is legible.
    points = [
        min(max(duration * p, 0.4), max(duration - 0.2, 0.4))
        for p in (0.08, 0.22, 0.38, 0.55, 0.7, 0.88)
    ]
    frames: list[str] = []
    for idx, ts in enumerate(points, start=1):
        dst = base / f"frame_{idx}.jpg"
        try:
            proc = subprocess.run(
                [
                    "ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", str(path),
                    "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "3",
                    str(dst),
                ],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=18,
            )
            if proc.returncode == 0 and dst.exists() and dst.stat().st_size > 1000:
                frames.append(str(dst))
        except Exception as exc:  # noqa: BLE001
            logger.debug("ready video frame extraction failed: %s", exc)
    return frames


def _describe_with_gemini(frames: list[str], context: dict, analysis: dict) -> dict | None:
    if not settings.gemini_api_key or not frames:
        return None
    prompt = _analysis_prompt(context, analysis)
    parts: list[dict] = [{"text": prompt}]
    for frame in frames[:6]:
        try:
            data = base64.b64encode(Path(frame).read_bytes()).decode("ascii")
            parts.append({"inline_data": {"mime_type": "image/jpeg", "data": data}})
        except Exception:
            continue
    if len(parts) == 1:
        return None
    payload = {
        "contents": [{"parts": parts}],
        "generationConfig": {
            "maxOutputTokens": 900,
            # Lower than before (was 0.35) — this call must describe what's
            # actually visible, not creatively improvise; less randomness means
            # a genuinely ambiguous video is more likely to get a cautious,
            # honest read (or trip the hallucination guard consistently)
            # instead of a different fabricated story on every retry.
            "temperature": 0.15,
            "responseMimeType": "application/json",
        },
    }
    # 429 (rate limit/quota) is transient — several channels can hit the same
    # Gemini quota in the same scheduler tick. Retrying with backoff instead of
    # giving up immediately is what keeps a title from falling back to the
    # fixed per-content_type template on every single busy tick (the fallback
    # was found to dominate almost all Drive video titles in production).
    # 400/401/403/404 are permanent (bad key/request) — no point retrying those.
    backoff_seconds = (0, 2, 5)
    for attempt, delay in enumerate(backoff_seconds, start=1):
        if delay:
            time.sleep(delay)
        try:
            with httpx.Client(timeout=60) as client:
                response = client.post(
                    GEMINI_VISION_URL.format(model=GEMINI_VISION_MODEL),
                    params={"key": settings.gemini_api_key},
                    json=payload,
                )
                if response.status_code == 429:
                    logger.info("Gemini vision rate-limited (tentativa %d/%d)", attempt, len(backoff_seconds))
                    continue
                if response.status_code in (400, 401, 403, 404):
                    logger.info("Gemini vision skipped with status %s", response.status_code)
                    return None
                response.raise_for_status()
                data = response.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                return llm.extract_json(text)
        except Exception as exc:  # noqa: BLE001
            logger.info("Gemini vision ready-video analysis failed (tentativa %d/%d): %s", attempt, len(backoff_seconds), exc)
            continue
    return None


def _describe_with_text_llm(context: dict, analysis: dict) -> dict | None:
    if not llm.available():
        return None
    try:
        import asyncio

        return asyncio.run(
            llm.complete_json(
                _analysis_prompt(context, analysis, text_only=True),
                system="Especialista em embalagem de videos curtos para YouTube. Responda somente JSON valido.",
                max_tokens=800,
                fast=True,
            )
        )
    except Exception as exc:  # noqa: BLE001
        logger.info("text ready-video analysis failed: %s", exc)
        return None


def _analysis_prompt(context: dict, analysis: dict, text_only: bool = False) -> str:
    frame_note = "Use os frames anexados para inferir apenas o que estiver visivel." if not text_only else (
        "Sem frames. Use apenas nome, pasta, nicho e metadados; nao invente fatos especificos."
    )
    return f"""
Voce vai empacotar um video pronto para publicar. {frame_note}
Nao diga que e automacao, Drive, biblioteca, arquivo ou video pronto.
NUNCA invente nomes de personagens, dialogos ou uma historia ficticia que voce
nao consegue confirmar nos frames. O campo "tipo" abaixo e so um rotulo de
fluxo interno, NAO uma garantia de genero — o video pode ser gameplay,
tutorial, screen recording ou qualquer outra coisa mesmo que o rotulo diga
"recap". Se os frames nao deixarem claro do que se trata, descreva apenas o
que e literalmente visivel (ex.: "tela de jogo com HUD", "captura de tela com
texto sobreposto") em vez de fabricar um enredo com personagens.

Contexto:
- arquivo: {context.get('drive_name')}
- pasta: {context.get('folder_path')}
- nicho: {context.get('niche')}
- canal: {context.get('display_name')}
- publico: {context.get('target_audience') or 'N/A'}
- tipo (rotulo de fluxo, nao confie cegamente): {context.get('content_type')}
- formato: {context.get('video_format')}
- probe: {json.dumps(analysis, ensure_ascii=False)[:900]}

Responda JSON:
{{
  "summary": "1 frase especifica do conteudo percebido",
  "topics": ["3-6 temas"],
  "entities": ["nomes/personagens/objetos se visiveis ou inferiveis com seguranca"],
  "hook": "gancho curto e honesto para titulo/descricao",
  "title_options": ["3 titulos de alta curiosidade, no padrao: situacao falhou/virou + mas/depois/olha + consequencia honesta"],
  "keywords": ["8-12 keywords de busca/sugeridos"],
  "warnings": []
}}
"""


def _normalise_analysis(data: dict) -> dict:
    return {
        "summary": _clean_text(data.get("summary") or ""),
        "topics": _clean_terms(data.get("topics") or data.get("keywords") or []),
        "entities": _clean_terms(data.get("entities") or []),
        "hook": _clean_text(data.get("hook") or ""),
        "title_options": [_clean_text(t) for t in (data.get("title_options") or []) if _clean_text(t)][:3],
        "keywords": _clean_terms(data.get("keywords") or []),
        "warnings": [str(w)[:120] for w in (data.get("warnings") or []) if w],
    }


def _fallback_analysis(context: dict) -> dict:
    topic = _best_topic(context, {})
    return {
        "summary": _summary(topic, context.get("niche") or "", context.get("content_type") or ""),
        "topics": _clean_terms([context.get("niche"), context.get("account_niche"), *_folder_terms(context.get("folder_path"))]),
        "entities": _clean_terms(_title_terms(topic)),
        "hook": _hook(topic, context.get("content_type") or ""),
        "title_options": [],
        "keywords": [],
    }


def _best_topic(context: dict, analysis: dict) -> str:
    for value in (
        # title_seed/drive_name must outrank hook/summary: the deterministic
        # fallback path derives analysis["hook"] from title_seed and writes it
        # back into `analysis`, so checking hook first would feed that
        # generated hook back in as the topic on the next _best_topic call.
        context.get("title_seed"),
        _clean_filename(context.get("drive_name")),
        analysis.get("hook"),
        (analysis.get("title_options") or [None])[0],
        analysis.get("summary"),
        context.get("niche"),
        context.get("account_niche"),
    ):
        cleaned = _clean_text(value or "")
        cleaned = _strip_shorts(cleaned)
        if cleaned and not _is_operational(cleaned):
            return cleaned[:90]
    return "Conteudo em destaque"


def _truncate_title_safely(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` chars without splitting a word or leaving a
    dangling preposition/article at the end (see _DANGLING_TRAILERS)."""
    text = text.strip()
    if len(text) <= limit:
        return text
    cut = text[:limit]
    cut = re.sub(r"\s+\S*$", "", cut).rstrip(" ,:;")
    cut = _repair_dangling_trailer(cut)
    return cut or text[:limit]


def _title_from_analysis(topic: str, analysis: dict, content_type: str, video_format: str) -> str:
    for option in analysis.get("title_options") or []:
        option = _clean_text(option)
        if option and not _bad_title(option):
            return _truncate_title_safely(option, 82 if video_format == "short" else 95)
    base = _compact_topic(topic)
    templates = {
        "sports_highlights": (
            f"{base}: o lance que mudou o jogo",
            f"{base}: o lance decisivo da partida",
            f"{base}: a jogada que ninguem esperava",
            f"{base}: o momento que definiu tudo",
        ),
        "motivational_speech": (
            f"{base}: a virada comeca aqui",
            f"{base}: o ponto de virada",
            f"{base}: a mensagem que muda o dia",
            f"{base}: comeca a mudanca agora",
        ),
        "reddit_story": (
            f"{base}: eu devia ter percebido antes",
            f"{base}: o relato completo",
            f"{base}: ninguem esperava esse desfecho",
            f"{base}: a historia que todo mundo comenta",
        ),
        "true_crime_mystery": (
            f"{base}: o detalhe que ninguem explicou",
            f"{base}: o detalhe do caso",
            f"{base}: a parte que intriga todo mundo",
            f"{base}: o que ainda intriga",
        ),
        "explainer_curiosity": (
            f"{base}: por que isso acontece?",
            f"{base}: o que aconteceu",
            f"{base}: a explicacao que faltava",
            f"{base}: entenda o motivo",
        ),
        "reaction_commentary": (
            f"{base}: o detalhe que chamou atencao",
            f"{base}: o momento da cena",
            f"{base}: a reacao que ninguem esperava",
            f"{base}: o que chamou atencao",
        ),
        "film_recap_ai_images": (
            f"{base}: o resumo que voce precisa ver",
            f"{base}: o resumo direto do que aconteceu",
            f"{base}: veja o que rolou",
            f"{base}: o corte que resume tudo",
        ),
        # "music" is the internal marker scheduler.py sets on jobs whose
        # reserved Drive file is audio-only (see _finalize_ready_video_job) —
        # without a dedicated entry here, music tracks inherited
        # "recap de filme" wording and got published under category 24
        # (Entertainment) instead of 10 (Music); confirmed in production.
        "music": (
            base,
            f"{base} (audio)",
            f"{base} | trilha sonora",
            f"{base} - musica completa",
        ),
        "quote_viral": (base,),
        "top_list_ranking": (
            f"{base}: o numero 1 vai te surpreender",
            f"{base}: o top que vale a pena ver",
            f"{base}: o ranking completo",
            f"{base}: qual ficou em primeiro?",
        ),
    }
    variants = templates.get(content_type)
    title = _pick_variant(topic or base, variants) if variants else _varied_fallback_title(base, topic)
    if video_format == "short":
        return _shorten_title(title)
    return title[:95]


# `_generic_viral_title` below already blocklists a handful of literal phrases
# ("espera o final", "detalhe que prende ate o fim") — those USED to be this
# function's own hardcoded fallback, which meant every Drive video whose
# content_type fell through (the common case: default is
# "film_recap_ai_images", not covered by `templates` above) got the exact
# same generic title/hook, and the description then echoed the title verbatim
# (see `_viral_shorts_profile`'s first_line logic) — a templated, repetitive
# pattern across the whole channel that reads as low-effort/spam to viewers
# and to YouTube's own distribution signals. Rotate through distinct,
# non-blocklisted phrasings instead, keyed off the topic so it stays stable
# per video without repeating the same literal on every upload.
_FALLBACK_TITLE_ENDINGS = (
    "voce nao vai acreditar no que acontece a seguir",
    "isso muda tudo bem no final",
    "ninguem esperava por esse desfecho",
    "presta atencao no que rola depois disso",
    "o que aconteceu na sequencia surpreendeu todo mundo",
)


def _varied_fallback_title(base: str, topic: str) -> str:
    idx = sum(ord(c) for c in (topic or base)) % len(_FALLBACK_TITLE_ENDINGS)
    return f"{base}: {_FALLBACK_TITLE_ENDINGS[idx]}"


def _hash_index(seed: str, n: int) -> int:
    """Stable per-topic index into an n-option sequence: the SAME video always
    lands on the same variant (stable across retries/re-renders), but
    different topics spread across different variants instead of every video
    of a content_type sharing the exact same literal title/hook/tags — the
    pattern that reads as mass-produced/templated content to viewers and to
    YouTube's own distribution signals."""
    if n <= 0:
        return 0
    return sum(ord(c) for c in (seed or "")) % n


def _pick_variant(seed: str, options: tuple[str, ...]) -> str:
    if not options:
        return ""
    return options[_hash_index(seed, len(options))]


def _apply_viral_shorts_title(title: str, topic: str, analysis: dict, content_type: str, video_format: str) -> str:
    """Shape Drive Shorts without making them sound AI-written.

    The user's winner had a clear human micro-story, but forcing that formula on
    every Drive video made titles long and fake. Prefer specific, natural titles;
    only repair titles that are too thin or obviously generic.
    """
    title = _strip_shorts(_clean_text(title))
    title = re.sub(r"^(epic|new|novo|nova)\s+", "", title, flags=re.I).strip()
    if video_format != "short":
        return title
    low = title.lower()
    if _is_too_thin_title(title):
        title = _repair_thin_title(title, topic, analysis)
        low = title.lower()
    if (
        any(marker in low for marker in VIRAL_TITLE_MARKERS)
        and not _generic_viral_title(low)
        and 18 <= len(title) <= 72
    ):
        return _shorten_title(title)

    base = _compact_topic(topic)
    # text_llm has no visual grounding (bare filename/folder/niche metadata), so an
    # AI-authored title from that path is not trusted to stand unmodified like vision_llm.
    if analysis.get("analysis_source") == "vision_llm" and len(_title_terms(title)) >= 3:
        return _shorten_title(title)

    templates = {
        "sports_highlights": (
            f"{base}: o lance decisivo",
            f"{base}: o momento chave",
            f"{base}: a jogada que decidiu",
            f"{base}: o lance que viralizou",
        ),
        "motivational_speech": (
            f"{base}: a virada de chave",
            f"{base}: o momento decisivo",
            f"{base}: a mensagem que fica",
            f"{base}: o instante que muda tudo",
        ),
        "reddit_story": (
            f"{base}: o relato completo",
            f"{base}: a historia real",
            f"{base}: o que aconteceu de verdade",
            f"{base}: o relato direto",
        ),
        "true_crime_mystery": (
            f"{base}: o detalhe do caso",
            f"{base}: o que ainda intriga",
            f"{base}: a parte que ninguem viu",
            f"{base}: o misterio por tras disso",
        ),
        "explainer_curiosity": (
            f"{base}: o que aconteceu",
            f"{base}: a explicacao direta",
            f"{base}: entenda em poucos segundos",
            f"{base}: o motivo por tras disso",
        ),
        "reaction_commentary": (
            f"{base}: o momento da cena",
            f"{base}: a reacao ao vivo",
            f"{base}: o que rolou na hora",
            f"{base}: o detalhe da cena",
        ),
        "music": (
            f"{base}: ouca agora",
            f"{base}: essa e boa",
            f"{base} pra hoje",
            f"{base}: bate essa",
        ),
    }
    default_variants = (
        f"{base}: o momento principal",
        f"{base}: o ponto alto",
        f"{base}: o que voce precisa ver",
        f"{base}: o destaque do video",
    )
    variants = templates.get(content_type, default_variants)
    return _shorten_title(_pick_variant(topic or base, variants))


def _is_too_thin_title(title: str) -> bool:
    terms = _title_terms(title)
    return len(terms) < 3 or len(_clean_text(title)) < 18


def _repair_thin_title(title: str, topic: str, analysis: dict) -> str:
    hook = _clean_text(analysis.get("hook") or "")
    if hook and len(_title_terms(hook)) >= 3:
        return hook
    summary = _clean_text(analysis.get("summary") or "")
    entity = _clean_terms(analysis.get("entities") or [])
    base = _compact_topic(summary or topic or title)
    title_terms = _title_terms(title)
    if entity and title_terms and entity[0].lower() not in base.lower():
        base = f"{entity[0]}: {base}"
    return base

def _clean_filename(name: str | None) -> str:
    value = re.sub(r"\.[A-Za-z0-9]{2,5}$", "", name or "")
    value = re.sub(r"[_\-+.]+", " ", value)
    value = re.sub(r"\(\s*\d+\s*\)", " ", value)
    value = re.sub(r"\b(19|20)\d{2}[01]\d[0-3]\d\b", " ", value)
    value = re.sub(r"\b\d{4,}\b", " ", value)
    value = re.sub(r"\b(9x16|16x9|1080p|720p|4k|fullhd)\b", " ", value, flags=re.I)
    pieces = [p for p in value.split() if p.lower() not in OPERATIONAL_WORDS]
    return _clean_text(" ".join(pieces))


def _clean_text(value: str | None) -> str:
    value = re.sub(r"\s+", " ", str(value or "")).strip()
    value = re.sub(r"[<>]+", "", value)
    return value


def _clean_terms(values: list | tuple | set) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in values:
        for piece in re.split(r"[,/|;]+", str(item or "")):
            piece = _clean_text(piece).strip("#")
            if not piece:
                continue
            key = piece.lower()
            if key not in seen and len(piece) <= 60 and not _is_operational(piece):
                out.append(piece)
                seen.add(key)
    return out


def _tags(primary: str, topic: str, entities: list[str], topics: list[str], niche: str, content_type: str, video_format: str) -> list[str]:
    generic_by_type = {
        "sports_highlights": (
            ["melhores momentos", "lance decisivo", "futebol"],
            ["compilado de jogadas", "melhores lances", "futebol brasileiro"],
        ),
        "motivational_speech": (
            ["motivacao", "reflexao", "virada de chave"],
            ["mensagem motivacional", "superacao", "mentalidade forte"],
        ),
        "reddit_story": (
            ["historia reddit", "relato", "storytime"],
            ["relato real", "historia verdadeira", "confissao"],
        ),
        "true_crime_mystery": (
            ["misterio", "caso real", "investigacao"],
            ["crime real", "caso resolvido", "detetive"],
        ),
        "explainer_curiosity": (
            ["curiosidades", "explicacao", "voce sabia"],
            ["fatos curiosos", "voce nao sabia", "explicado"],
        ),
        "reaction_commentary": (
            ["reacao", "comentario", "cultura pop"],
            ["reagindo", "comentando", "cena marcante"],
        ),
        # film_recap_ai_images is content_types.py's default and the biggest
        # single volume in the system (scheduler.py's fallback for from-Drive
        # jobs) — it used to fall through to the generic default_variants below
        # even though title/hook already have dedicated, specific entries.
        "film_recap_ai_images": (
            ["recap de filme", "resumo do filme", "cenas marcantes"],
            ["recap dublado", "melhor cena", "resumo completo"],
        ),
        "music": (
            ["musica", "playlist", "sem direitos autorais"],
            ["trilha sonora", "musica relaxante", "audio"],
        ),
        "quote_viral": (
            ["frase motivacional", "citacao", "reflexao do dia"],
            ["frases de impacto", "pensamento profundo", "citacao viral"],
        ),
        "top_list_ranking": (
            ["top 5", "ranking", "os melhores"],
            ["lista definitiva", "classificacao", "os piores"],
        ),
    }
    default_variants = (
        ["cortes", "humor", "entretenimento"],
        ["destaque", "video viral", "cena marcante"],
    )
    type_variants = generic_by_type.get(content_type, default_variants)
    generic_tags = type_variants[_hash_index(topic or primary, len(type_variants))]
    raw = [
        primary,
        topic,
        *entities,
        *topics,
        niche,
        *(_folder_terms(niche)),
        *generic_tags,
    ]
    if video_format == "short":
        raw.extend(["shorts", "cortes virais"])
    out = _clean_terms(raw)
    if len(out) >= 8:
        return out[:18]
    # Guarantee >=8 tags (tags_quality scoring assumes it): keep adding filler
    # until the threshold is met. Rotated by topic hash instead of always
    # starting from the same first term, so different videos don't all end up
    # with the exact same filler tags in the exact same order.
    seen = {t.lower() for t in out}
    filler_pool = [
        "cortes", "entretenimento", "viral", "video", "conteudo", "destaque",
        "melhores momentos", "assista", "em alta", "recomendado", "para voce", "trending",
    ]
    start = _hash_index(topic or primary, len(filler_pool))
    rotated_filler = filler_pool[start:] + filler_pool[:start]
    for term in rotated_filler:
        if len(out) >= 8:
            break
        if term.lower() not in seen:
            out.append(term)
            seen.add(term.lower())
    return out[:12]


def _yt_hashtags(tags: list[str], video_format: str) -> list[str]:
    out = ["#Shorts"] if video_format == "short" else []
    for tag in tags:
        h = "#" + re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", tag.title())
        if len(h) > 1 and h.lower() not in {x.lower() for x in out}:
            out.append(h)
        if len(out) >= 3:
            break
    return out


def _social_hashtags(tags: list[str], video_format: str) -> list[str]:
    base = ["shorts", "reels"] if video_format == "short" else ["youtube"]
    out: list[str] = []
    for tag in [*tags, *base]:
        h = "#" + re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", tag.lower())
        if len(h) > 1 and h not in out:
            out.append(h)
    return out[:12]


def _viral_shorts_profile(title: str, topic: str, hook: str, summary: str, tags: list[str], video_format: str) -> dict:
    short = video_format == "short"
    curiosity = any(marker in title.lower() for marker in VIRAL_TITLE_MARKERS)
    clean_hook = _clean_text(hook)
    first_line = _strip_shorts(title) if (_generic_viral_title(clean_hook.lower()) or _is_too_thin_title(clean_hook)) else clean_hook
    return {
        "source": VIRAL_SHORTS_LEARNINGS["source"],
        "target": "feed_dos_shorts" if short else "browse_suggested",
        "retention_target": "75%+ continuam assistindo" if short else "boa duracao media",
        "title_pattern": "quebra de expectativa + consequencia honesta" if curiosity else "curiosidade direta",
        "first_line": first_line,
        "comment_prompt": _first_comment(topic, "reaction_commentary"),
        "signals": {
            "shorts_feed_share_reference": VIRAL_SHORTS_LEARNINGS["shorts_feed_share"],
            "retention_reference": VIRAL_SHORTS_LEARNINGS["retention_continued"],
            "avg_view_vs_duration": "maior que 100%",
            "tag_specificity": min(len(tags), 18),
        },
        "anti_patterns": [
            "descricao generica de biblioteca",
            "titulo so com nome do arquivo",
            "tags amplas antes da keyword especifica",
        ],
        "summary": _clean_text(summary),
    }


def _description(hook: str, summary: str, hashtags: list[str], viral_profile: dict | None = None) -> str:
    first_line = _clean_text((viral_profile or {}).get("first_line") or hook)
    desc = (
        f"{first_line}\n\n"
        f"{summary}\n\n"
        f"{(viral_profile or {}).get('comment_prompt') or 'Comenta qual detalhe voce percebeu primeiro.'}\n\n"
        f"{' '.join(hashtags[:3])}"
    )
    return _remove_internal_words(desc).strip()


def _remove_internal_words(text: str) -> str:
    patterns = [
        r"\bgoogle drive\b", r"\bdrive\b", r"\bbiblioteca\b", r"\bautom[aá]tic[oa]\b",
        r"\barquivo\b", r"\bvideo pronto\b", r"\bvídeo pronto\b",
    ]
    cleaned = text
    for pattern in patterns:
        cleaned = re.sub(pattern, "", cleaned, flags=re.I)
    lines = [re.sub(r"[ \t]{2,}", " ", line).strip() for line in cleaned.splitlines()]
    return "\n".join(lines)


def _score(title: str, description: str, tags: list[str], hashtags: list[str], analysis: dict) -> dict:
    # text_llm is unverified (no frames to ground it, just filename/folder/niche),
    # so it earns partial credit between genuine vision_llm grounding and the bare
    # deterministic fallback, instead of being trusted as much as vision analysis.
    analysis_source = analysis.get("analysis_source")
    if analysis_source == "vision_llm":
        analysis_points = 20
    elif analysis_source == "text_llm":
        analysis_points = 12
    else:
        analysis_points = 8
    breakdown = {
        "analysis": analysis_points,
        "title_specificity": 18 if len(_title_terms(title)) >= 3 else 10,
        "description_quality": 16 if len(description) >= 120 and "biblioteca" not in description.lower() else 8,
        "tags_quality": 16 if len(tags) >= 8 else 8,
        "hashtag_quality": 10 if len(hashtags) >= 2 else 5,
        # NOTE: hashtags only ever contains "#Shorts" when video_format == "short",
        # and in that same case the title always gets "#Shorts" appended too
        # (see build_drive_seo), so this condition is always true. Kept as a
        # fixed contribution instead of a fake conditional to avoid implying
        # the breakdown reflects a real format-fit check.
        "format_fit": 10,
        "shorts_feed_packaging": 12 if _has_viral_title_shape(title) else 6,
    }
    value = min(95, sum(breakdown.values()))
    return {
        "value": value,
        "breakdown": breakdown,
        "verdict": "drive_video_strong" if value >= 75 else "drive_video_fallback",
    }


def _notes(score: dict, analysis: dict) -> list[str]:
    notes: list[str] = []
    if analysis.get("analysis_source") not in {"vision_llm", "text_llm"}:
        notes.append("SEO gerado por fallback deterministico; IA de analise indisponivel.")
    elif analysis.get("analysis_source") == "text_llm":
        notes.append("Analise sem frames de video; titulo/resumo podem nao refletir o conteudo real.")
    if score.get("value", 0) < 75:
        notes.append("Score abaixo do ideal porque o conteudo real do video foi inferido com sinais limitados.")
    return notes


def _has_viral_title_shape(title: str) -> bool:
    clean = _strip_shorts(title).lower()
    return (
        18 <= len(clean) <= 72
        and (any(marker in clean for marker in VIRAL_TITLE_MARKERS)
             or any(word in clean for word in CONFLICT_WORDS))
        and not _generic_viral_title(clean)
    )


def _generic_viral_title(clean_lower_title: str) -> bool:
    weak = (
        "detalhe que chamou atencao",
        "detalhe que chamou atenção",
        "detalhe que prende ate o fim",
        "detalhe que prende até o fim",
        "espera o final",
    )
    return any(piece in clean_lower_title for piece in weak)


def _summary(topic: str, niche: str, content_type: str) -> str:
    topic_l = topic.lower()
    seed = f"{topic}{niche}"
    if niche:
        niche_l = niche.lower()
        variants = (
            f"Um corte direto sobre {topic_l} para quem acompanha {niche_l}.",
            f"Separei esse momento sobre {topic_l} pra quem curte {niche_l}.",
            f"Direto ao ponto: {topic_l}, pensado pra quem gosta de {niche_l}.",
            f"Esse corte sobre {topic_l} e pra quem acompanha {niche_l} de perto.",
        )
        return _pick_variant(seed, variants)
    if content_type == "reaction_commentary":
        variants = (
            f"Um corte curto com humor e contexto sobre {topic_l}.",
            f"Rapidinho, com humor, sobre {topic_l}.",
            f"Um recorte leve e direto sobre {topic_l}.",
        )
        return _pick_variant(seed, variants)
    variants = (
        f"Um corte direto ao ponto sobre {topic_l}.",
        f"Separei esse momento sobre {topic_l}.",
        f"Direto ao ponto: {topic_l}.",
    )
    return _pick_variant(seed, variants)


_FALLBACK_HOOK_ENDINGS = (
    "tem uma parte que muita gente perde de primeira.",
    "o desfecho pega quem nao esperava.",
    "vale assistir ate o fim para entender.",
    "poucos reparam nesse detalhe na primeira vez.",
    "a reacao mudou assim que isso aconteceu.",
)


def _hook(topic: str, content_type: str) -> str:
    base = _compact_topic(topic)
    hooks = {
        "sports_highlights": (
            "O detalhe desse lance passou batido por muita gente.",
            "Poucos repararam nesse detalhe do lance.",
            "Esse lance tem um detalhe que quase ninguem viu.",
            "A jogada esconde um detalhe que vale a pena ver de novo.",
        ),
        "motivational_speech": (
            "Essa mensagem pode virar a chave hoje.",
            "Essa frase pode mudar o seu dia.",
            "Vale guardar essa mensagem pra hoje.",
            "Um lembrete que chega na hora certa.",
        ),
        "reddit_story": (
            "Essa historia muda quando voce percebe o detalhe.",
            "O relato tem uma virada que pega todo mundo.",
            "Tem um detalhe nessa historia que muda tudo.",
            "Essa historia real surpreende no final.",
        ),
        "true_crime_mystery": (
            "O detalhe desse caso ainda intriga muita gente.",
            "Esse caso tem uma parte que ninguem explica direito.",
            "Ainda tem gente tentando entender esse detalhe do caso.",
            "O misterio por tras desse caso segue intrigando.",
        ),
        "explainer_curiosity": (
            f"{base}: tem um detalhe que pouca gente percebe.",
            f"{base}: a explicacao e mais simples do que parece.",
            f"{base}: poucos sabem o motivo real disso.",
            f"{base}: entenda o que faz isso acontecer.",
        ),
        "reaction_commentary": (
            f"{base}: o detalhe que chamou atencao de quem assistiu.",
            f"{base}: a reacao de quem viu isso ao vivo.",
            f"{base}: o momento que rendeu comentario.",
            f"{base}: o detalhe que ninguem esperava ver.",
        ),
        "film_recap_ai_images": (
            f"{base}: o resumo direto do que aconteceu.",
            f"{base}: veja o que rolou em poucos segundos.",
            f"{base}: o corte que resume a cena.",
            f"{base}: direto ao ponto sobre o que aconteceu.",
        ),
        "music": (
            f"{base}: pra ouvir sem parar.",
            f"{base}: separei essa pra voce.",
            f"{base}: bora ouvir.",
            f"{base}: essa entra no repeat.",
        ),
        "quote_viral": (base,),
        "top_list_ranking": (
            f"{base}: veja como cada posicao se destacou.",
            f"{base}: cada posicao tem seu motivo.",
            f"{base}: o ranking completo, ponto a ponto.",
            f"{base}: veja o que definiu cada posicao.",
        ),
    }
    variants = hooks.get(content_type)
    if variants:
        return _pick_variant(topic or base, variants)
    idx = sum(ord(c) for c in (topic or base)) % len(_FALLBACK_HOOK_ENDINGS)
    return f"{base}: {_FALLBACK_HOOK_ENDINGS[idx]}"


def _first_comment(topic: str, content_type: str) -> str:
    if content_type == "sports_highlights":
        return "Voce viu esse detalhe na primeira vez ou so no replay?"
    if content_type == "motivational_speech":
        return "Essa frase fez sentido para voce hoje?"
    return "Voce percebeu esse detalhe de primeira?"


def _long_tail(primary: str, topic: str) -> list[str]:
    return [f"{primary} explicado", f"{primary} melhores momentos", f"{topic} completo"][:3]


def _suggested_next_to(niche: str, content_type: str) -> list[str]:
    return [niche or content_type.replace("_", " "), "cortes virais", "shorts populares"]


def _playlist_target(niche: str, content_type: str) -> str:
    if niche:
        return f"{niche.title()}: melhores cortes"
    return content_type.replace("_", " ").title()


def _primary_keyword(topic: str, entities: list[str], topics: list[str], niche: str) -> str:
    return _clean_text((entities or topics or [niche, topic])[0] or topic).lower()[:60]


def _thumbnail_text(title: str) -> str:
    clean = _strip_shorts(title)
    words = [w for w in re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", clean) if w.lower() not in OPERATIONAL_WORDS]
    return " ".join(words[:3]).upper()[:30] or "VEJA ISSO"


def _strip_shorts(text: str) -> str:
    return re.sub(r"#shorts?\b", "", text or "", flags=re.I).strip()


# Trailing connectors that read as a dangling, unfinished sentence when a
# template later appends ": <hook>" right after them (e.g. truncating "...as
# funcionalidades do software" at 55 chars can leave "...funcionalidades do",
# which then becomes the broken "...funcionalidades do: espera o final").
_DANGLING_TRAILERS = {
    "de", "do", "da", "dos", "das", "em", "no", "na", "nos", "nas",
    "com", "para", "por", "que", "e", "ou", "a", "o", "as", "os",
    "um", "uma", "uns", "umas", "seu", "sua", "usando", "atraves",
    "através", "sobre", "ate", "até",
}


def _repair_dangling_trailer(cut: str) -> str:
    """Strip trailing words that leave a truncated title reading as an
    unfinished sentence (e.g. "...funcionalidades do" -> "...funcionalidades").
    Shared by every title-truncation path so the fix can't regress in one
    path while staying fixed in another."""
    while True:
        words = cut.split(" ")
        if len(words) <= 1 or words[-1].lower() not in _DANGLING_TRAILERS:
            break
        cut = " ".join(words[:-1]).rstrip(" ,:;")
    return cut


def _shorten_title(title: str) -> str:
    title = re.sub(r"\s+", " ", title).strip()
    if len(title) <= 72:
        return title
    cut = title[:69].rstrip(" ,:;")
    cut = re.sub(r"\s+\S*$", "", cut).rstrip(" ,:;")
    cut = _repair_dangling_trailer(cut)
    return cut or title[:69].rstrip(" ,:;")


def _compact_topic(topic: str) -> str:
    topic = _remove_internal_words(_clean_text(topic))
    if len(topic) <= 55:
        cut = topic
    else:
        cut = topic[:55]
        cut = re.sub(r"\s+\S*$", "", cut)
    cut = cut.rstrip(" ,:;")
    cut = _repair_dangling_trailer(cut)
    return cut or "Esse corte"


def _title_terms(text: str) -> list[str]:
    return [w for w in re.findall(r"[A-Za-zÀ-ÿ0-9]{3,}", text or "") if w.lower() not in OPERATIONAL_WORDS]


def _folder_terms(path_or_niche: str | None) -> list[str]:
    return [p for p in re.split(r"[/\\>|-]+", path_or_niche or "") if _clean_text(p)]


def _is_operational(text: str) -> bool:
    terms = [t.lower() for t in _title_terms(text)]
    if not terms:
        return True
    return bool(terms) and sum(1 for t in terms if t in OPERATIONAL_WORDS) >= max(1, len(terms) - 1)


def _bad_title(title: str) -> bool:
    low = title.lower()
    return any(word in low for word in ("google drive", "biblioteca", "arquivo", "video pronto", "vídeo pronto"))


def _aspect_ratio(width: int | None, height: int | None) -> str:
    if not width or not height:
        return "unknown"
    ratio = width / height
    if 0.52 <= ratio <= 0.60:
        return "9:16"
    if 1.70 <= ratio <= 1.85:
        return "16:9"
    if 0.95 <= ratio <= 1.05:
        return "1:1"
    return f"{width}:{height}"


def _fps(value: str | None) -> float | None:
    if not value or value == "0/0":
        return None
    try:
        if "/" in value:
            n, d = value.split("/", 1)
            return round(float(n) / max(float(d), 1.0), 2)
        return round(float(value), 2)
    except Exception:
        return None


if __name__ == "__main__":
    sample_context = {
        "drive_name": "Legendado (1).mp4",
        "folder_path": "VIDEOS MEMES / DESENHOS ANIMADOS",
        "niche": "Desenhos animados",
        "content_type": "reaction_commentary",
        "video_format": "short",
    }
    print(json.dumps(build_drive_seo(context=sample_context, analysis={}), indent=2, ensure_ascii=False))
