"""Editorial curation layer for Drive-sourced ("ready video") publishes.

A Drive video publishes the source clip almost unmodified: ready_video_seo.py
wraps a new title/description/thumbnail around it, but the video STREAM
itself is a straight re-upload. That is exactly what YouTube's monetization
review flags as "conteudo reutilizado" -- confirmed against ONDA444's own
rejection screenshot ("a contestacao nao demonstrou uma edicao que agregasse
valor"). This module adds the missing piece: an ORIGINAL spoken take
(long-form) or on-screen commentary line (Shorts), grounded in the video's
own vision analysis (analyze_ready_video), so every publish carries real
editorial opinion/analysis instead of a bare re-upload.

Best-effort throughout: any failure anywhere in this module must never block
a publish. `apply_curation_layer` always returns a usable local path -- the
curated one on success, the untouched original on any failure.
"""
from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from pathlib import Path

from backend import llm
from backend.config import settings

logger = logging.getLogger("studio.ready_video_curation")

_FFMPEG_TIMEOUT = 480

_TAKE_SYSTEM = (
    "Voce e curador/comentarista de video. Sua unica funcao aqui e dar uma "
    "leitura ORIGINAL sobre um video que ja existe -- nunca descreva o que "
    "acontece de forma neutra. Essa opiniao/analise e o que torna o video um "
    "produto editorial novo, nao uma copia (o YouTube desmonetiza reuso sem "
    "valor agregado). Responda SOMENTE com JSON valido."
)


def apply_curation_layer(
    *, job_id: int, local_path: str, analysis: dict, context: dict, video_format: str,
    visual_theme: dict | None = None, voice: str | None = None,
) -> str:
    """Best-effort. Returns `local_path` unchanged on ANY failure -- curation
    is a value-add, never a publish blocker.

    `visual_theme`/`voice` are optional -- callers with no Channel (see
    backend/models/channel.py) simply omit them and get the previous fixed
    yellow-on-black look + the global default TTS voice, unchanged."""
    if not settings.ready_video_curation_enabled:
        return local_path
    try:
        take = asyncio.run(_generate_take(analysis or {}, context or {}, video_format))
    except Exception as exc:  # noqa: BLE001
        logger.info("commentary generation failed for job %s: %s", job_id, exc)
        return local_path
    if not take:
        return local_path
    try:
        if video_format == "short":
            out = _apply_short_overlay(job_id, local_path, take, analysis or {}, visual_theme)
        else:
            out = _apply_long_intro(job_id, local_path, take, analysis or {}, visual_theme, voice)
    except Exception as exc:  # noqa: BLE001
        logger.info("curation render failed for job %s (%s): %s", job_id, video_format, exc)
        return local_path
    if out:
        logger.info("ready_video_curation_applied job_id=%s format=%s", job_id, video_format)
        return out
    return local_path


# ---- commentary script (LLM) -------------------------------------------------

async def _generate_take(analysis: dict, context: dict, video_format: str) -> dict | None:
    if not llm.available():
        return None
    summary = (analysis.get("summary") or "").strip()
    hook = (analysis.get("hook") or "").strip()
    topics = ", ".join((analysis.get("topics") or [])[:5])
    entities = ", ".join((analysis.get("entities") or [])[:5])
    niche = (context.get("niche") or context.get("account_niche") or "").strip()
    grounding = summary or hook or "sem descricao disponivel"

    if video_format == "short":
        prompt = f"""
Video (contexto, nao repita literalmente): {grounding}
Temas: {topics or 'N/A'} | Entidades: {entities or 'N/A'} | Nicho: {niche or 'N/A'}

De uma frase curta (ate 8 palavras) com uma OPINIAO ou ANALISE real sobre esse
video -- uma leitura, julgamento ou conexao sua, nunca uma descricao neutra.
Vai aparecer como legenda na tela nos primeiros segundos.
Ruim (neutro, NAO faca): "video de jogo".
Bom (opiniao, faca assim): "isso aqui prova reflexo, nao sorte".

JSON: {{"line": "..."}}
""".strip()
        max_tokens = 100
    else:
        prompt = f"""
Video (contexto, nao repita literalmente): {grounding}
Temas: {topics or 'N/A'} | Entidades: {entities or 'N/A'} | Nicho: {niche or 'N/A'}

Escreva uma introducao FALADA curta (40-70 palavras) apresentando esse video
com uma ANALISE OU OPINIAO ORIGINAL sua -- por que esse momento importa, o
que ele revela, ou um contraponto seu. Nao narre o video como se fosse
spoiler completo e nao comece com "neste video" / "hoje eu vou mostrar".
Fale como alguem que ja viu o video e tem algo pra dizer sobre ele.

JSON: {{"line": "frase curta pra tela, ate 8 palavras", "spoken": "texto completo falado"}}
""".strip()
        max_tokens = 320

    try:
        data = await llm.complete_json(prompt, system=_TAKE_SYSTEM, max_tokens=max_tokens, fast=True)
    except Exception as exc:  # noqa: BLE001
        logger.info("commentary LLM call failed: %s", exc)
        return None
    if not isinstance(data, dict):
        return None

    line = _clean(str(data.get("line") or ""))[:60]
    if video_format == "short":
        return {"line": line} if line else None

    spoken = _clean(str(data.get("spoken") or ""))
    spoken = " ".join(spoken.split()[:120])
    if not spoken or len(spoken.split()) < 8:
        return None
    return {"line": line or spoken[:60], "spoken": spoken}


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# ---- Shorts: burned-in commentary line over the first ~3s -------------------

def _apply_short_overlay(
    job_id: int, local_path: str, take: dict, analysis: dict, visual_theme: dict | None,
) -> str | None:
    line = take.get("line")
    if not line:
        return None
    src = Path(local_path)
    dims = _dims(src, analysis)
    if not dims:
        return None
    w, h = dims

    work = _work_dir(job_id)
    overlay_png = work / "overlay.png"
    _render_caption_png(line, w, h, overlay_png, visual_theme)

    dst = work / f"curated_{src.name}"
    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-i", str(overlay_png),
        "-filter_complex", "[0:v][1:v]overlay=0:0:enable='between(t,0,3.2)'[v]",
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "21",
        "-threads", str(max(1, settings.ffmpeg_threads)),
        "-c:a", "copy", "-movflags", "+faststart",
        str(dst),
    ]
    _run(cmd)
    if not dst.exists() or dst.stat().st_size == 0:
        return None
    return str(dst)


def _render_caption_png(text: str, w: int, h: int, dst: Path, visual_theme: dict | None = None) -> None:
    from PIL import Image, ImageDraw

    from backend.agents.visuals import VisualsAgent

    fill = _theme_rgba(visual_theme, "accent_color", (255, 221, 0))
    stroke = _theme_rgba(visual_theme, "stroke_color", (0, 0, 0))

    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    font = VisualsAgent._font(max(20, int(h * 0.05)))
    lines = VisualsAgent._wrap(text.upper()[:70], font, draw, int(w * 0.86))[:2]
    line_h = int(h * 0.065)
    total = line_h * len(lines)
    y = int(h * 0.09)
    band = Image.new("RGBA", (w, total + int(h * 0.04)), (0, 0, 0, 150))
    img.paste(band, (0, max(0, y - int(h * 0.02))), band)
    for ln in lines:
        tw = draw.textlength(ln, font=font)
        x = (w - tw) / 2
        draw.text((x, y), ln, font=font, fill=fill,
                   stroke_width=max(2, w // 340), stroke_fill=stroke)
        y += line_h
    img.save(dst, "PNG")


def _hex_to_rgb(value: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
    try:
        v = (value or "").lstrip("#")
        if len(v) != 6:
            return default
        return (int(v[0:2], 16), int(v[2:4], 16), int(v[4:6], 16))
    except Exception:
        return default


def _theme_rgba(visual_theme: dict | None, key: str, default_rgb: tuple[int, int, int]) -> tuple[int, int, int, int]:
    r, g, b = _hex_to_rgb((visual_theme or {}).get(key), default_rgb)
    return (r, g, b, 255)


def _theme_rgb(visual_theme: dict | None, key: str, default_rgb: tuple[int, int, int]) -> tuple[int, int, int]:
    return _hex_to_rgb((visual_theme or {}).get(key), default_rgb)


# ---- Long-form: narrated commentary intro prepended to the clip -------------

def _apply_long_intro(
    job_id: int, local_path: str, take: dict, analysis: dict,
    visual_theme: dict | None = None, voice: str | None = None,
) -> str | None:
    spoken = take.get("spoken")
    if not spoken:
        return None
    src = Path(local_path)
    dims = _dims(src, analysis)
    if not dims:
        return None
    w, h = dims
    work = _work_dir(job_id)

    narration_path = work / "intro_narration.mp3"
    resolved_voice = voice or settings.default_tts_voice or "pt-BR-AntonioNeural"
    asyncio.run(_synthesize(spoken, resolved_voice, narration_path))
    if not narration_path.exists() or narration_path.stat().st_size < 1024:
        return None
    narr_seconds = _probe_duration(narration_path)
    if narr_seconds <= 0:
        return None

    card = work / "intro_card.jpg"
    _render_intro_card(job_id, src, take.get("line") or spoken, w, h, card, visual_theme)

    intro_clip = work / "intro_clip.mp4"
    _render_intro_clip(card, narration_path, narr_seconds, w, h, intro_clip)
    if not intro_clip.exists() or intro_clip.stat().st_size == 0:
        return None

    has_audio = bool(analysis.get("has_audio"))
    orig_duration = float(analysis.get("duration") or 0) or _probe_duration(src)
    dst = work / f"curated_{src.name}"
    if not _concat(intro_clip, src, orig_duration, has_audio, w, h, dst):
        return None
    return str(dst)


async def _synthesize(text: str, voice: str, dst: Path) -> None:
    import edge_tts

    try:
        communicate = edge_tts.Communicate(text, voice, rate=settings.tts_rate or "+8%")
        await communicate.save(str(dst))
        if dst.exists() and dst.stat().st_size >= 1024:
            return
    except Exception as exc:  # noqa: BLE001
        logger.info("edge-tts intro synth failed: %s", exc)
    # gTTS fallback -- keyless, no gender control, but keeps the intro from
    # silently disappearing when edge-tts is throttled (same tradeoff narrator.py
    # already makes for the main narration).
    from gtts import gTTS

    def _write() -> None:
        gTTS(text=text, lang="pt", tld="com.br").save(str(dst))

    await asyncio.to_thread(_write)


def _render_intro_card(
    job_id: int, src_video: Path, text: str, w: int, h: int, dst: Path,
    visual_theme: dict | None = None,
) -> None:
    from PIL import Image

    from backend.agents.visuals import VisualsAgent

    frame = dst.with_name("intro_frame_raw.jpg")
    got_frame = False
    try:
        _extract_frame(src_video, frame, w, h)
        got_frame = frame.exists() and frame.stat().st_size > 0
    except Exception as exc:  # noqa: BLE001
        logger.info("intro frame extraction failed for job %s: %s", job_id, exc)
    if not got_frame:
        Image.new("RGB", (w, h), (15, 15, 22)).save(frame, "JPEG")

    agent = VisualsAgent(job_id=job_id, emit=False)
    cfg = {
        "pos": "bottom",
        "fill": _theme_rgb(visual_theme, "text_color", (255, 255, 255)),
        "stroke": _theme_rgb(visual_theme, "stroke_color", (10, 10, 10)),
    }
    agent._compose_thumb(frame, dst, text, w, h, cfg)


def _extract_frame(src: Path, dst: Path, w: int, h: int) -> None:
    cmd = [
        "ffmpeg", "-y", "-i", str(src), "-ss", "1", "-frames:v", "1",
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        str(dst),
    ]
    _run(cmd, timeout=30)


def _render_intro_clip(card: Path, narration: Path, narr_seconds: float, w: int, h: int, dst: Path) -> None:
    duration = narr_seconds + 0.4
    cmd = [
        "ffmpeg", "-y", "-loop", "1", "-i", str(card), "-i", str(narration),
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", "30",
        "-threads", str(max(1, settings.ffmpeg_threads)),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-c:a", "aac", "-b:a", "192k", "-shortest", "-t", f"{duration:.3f}",
        str(dst),
    ]
    _run(cmd)


def _concat(intro: Path, original: Path, orig_duration: float, has_audio: bool,
            w: int, h: int, dst: Path) -> bool:
    venc = ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "21",
            "-threads", str(max(1, settings.ffmpeg_threads))]
    scale = (f"scale={w}:{h}:force_original_aspect_ratio=decrease,"
             f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps=30")
    inputs = ["-i", str(intro), "-i", str(original)]
    if has_audio:
        filt = (
            f"[0:v]{scale}[v0];[1:v]{scale}[v1];"
            "[0:a]aresample=44100[a0];[1:a]aresample=44100[a1];"
            "[v0][a0][v1][a1]concat=n=2:v=1:a=1[v][a]"
        )
    else:
        # The original clip has no audio stream -- synthesize a silent one so
        # both concat segments have the same [v][a] layout (ffmpeg's concat
        # filter requires it); the audible narration still plays over the
        # intro, the original segment just stays silent as it always was.
        inputs += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={max(0.5, orig_duration):.3f}"]
        filt = (
            f"[0:v]{scale}[v0];[1:v]{scale}[v1];"
            "[0:a]aresample=44100[a0];"
            "[v0][a0][v1][2:a]concat=n=2:v=1:a=1[v][a]"
        )
    cmd = ["ffmpeg", "-y", *inputs, "-filter_complex", filt,
           "-map", "[v]", "-map", "[a]", *venc, "-c:a", "aac", "-b:a", "192k",
           "-movflags", "+faststart", str(dst)]
    _run(cmd)
    return dst.exists() and dst.stat().st_size > 0


# ---- design preview (no ffmpeg video render) ---------------------------------

def render_preview(
    job_id: int, video_format: str, line: str, *, visual_theme: dict | None = None,
    source_path: str | None = None, analysis: dict | None = None,
) -> str | None:
    """Renders ONLY the overlay.png (short) or intro_card.jpg (long) a real
    curation pass would produce -- no video encode, no TTS, no concat. Lets an
    operator check a channel's visual_theme fast before spending real render
    time. `source_path`/`analysis` are optional: without them the long-form
    card falls back to a solid background instead of a frame from the clip."""
    line = _clean(line or "")
    if not line:
        return None
    analysis = analysis or {}
    dims = None
    if source_path:
        dims = _dims(Path(source_path), analysis)
    if not dims:
        w, h = int(analysis.get("width") or 0), int(analysis.get("height") or 0)
        dims = (w, h) if w > 0 and h > 0 else (1280, 720)
    w, h = dims

    work = _work_dir(job_id)
    try:
        if video_format == "short":
            out = work / "preview_overlay.png"
            _render_caption_png(line, w, h, out, visual_theme)
        else:
            out = work / "preview_intro_card.jpg"
            if source_path:
                _render_intro_card(job_id, Path(source_path), line, w, h, out, visual_theme)
            else:
                from PIL import Image

                from backend.agents.visuals import VisualsAgent

                blank = work / "preview_blank.jpg"
                Image.new("RGB", (w, h), (15, 15, 22)).save(blank, "JPEG")
                agent = VisualsAgent(job_id=job_id, emit=False)
                cfg = {
                    "pos": "bottom",
                    "fill": _theme_rgb(visual_theme, "text_color", (255, 255, 255)),
                    "stroke": _theme_rgb(visual_theme, "stroke_color", (10, 10, 10)),
                }
                agent._compose_thumb(blank, out, line, w, h, cfg)
    except Exception as exc:  # noqa: BLE001
        logger.info("preview render failed for job %s: %s", job_id, exc)
        return None
    return str(out) if out.exists() and out.stat().st_size > 0 else None


# ---- shared helpers -----------------------------------------------------------

def _work_dir(job_id: int) -> Path:
    d = Path(settings.abs_path(settings.temp_dir)) / "ready_video_curation" / f"job_{job_id}"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _run(cmd: list[str], timeout: int = _FFMPEG_TIMEOUT) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=timeout)
    if proc.returncode != 0:
        tail = (proc.stderr or "")[-800:]
        raise RuntimeError(f"ffmpeg failed (rc={proc.returncode}): {tail}")


def _probe_duration(path: Path) -> float:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        return float(out.stdout.strip())
    except Exception:
        return 0.0


def _probe_dims(path: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
            capture_output=True, text=True, timeout=15,
        )
        w_str, h_str = out.stdout.strip().split("x")
        w, h = int(w_str), int(h_str)
        return (w, h) if w > 0 and h > 0 else None
    except Exception:
        return None


def _dims(path: Path, analysis: dict) -> tuple[int, int] | None:
    w, h = int(analysis.get("width") or 0), int(analysis.get("height") or 0)
    if w > 0 and h > 0:
        return w, h
    return _probe_dims(path)
