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
from backend.intro_modes import INTRO_MODES

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
    intro_mode: str | None = None, channel_id: int | None = None,
) -> str:
    """Best-effort. Returns `local_path` unchanged on ANY failure -- curation
    is a value-add, never a publish blocker.

    `visual_theme`/`voice` are optional -- callers with no Channel (see
    backend/models/channel.py) simply omit them and get the previous fixed
    yellow-on-black look + the global default TTS voice, unchanged.

    `intro_mode`/`channel_id` (long-form only) pick how the intro is
    delivered -- see _resolve_intro_mode. Preventive risk mitigation: a
    tutorial video claiming YouTube derates channels using 100% synthetic
    narration (an unconfirmed creator claim, not documented policy) is what
    motivates this -- the intro is a small fraction of the video, but the
    SAME TTS voice in the SAME format on every video is still a detectable
    pattern worth breaking up regardless of whether that specific claim is true."""
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
            out = _apply_long_intro(
                job_id, local_path, take, analysis or {}, visual_theme, voice,
                intro_mode, channel_id,
            )
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

# "tts" is the only mode that existed before this. Left as the fallback for
# an unset/unrecognised/empty-bank mode -- every other mode degrades to it.
# Canonical list lives in backend/intro_modes.py (shared with the router's
# request validation).
_INTRO_MODES = INTRO_MODES
# Default spread for "mixed": mostly TTS (cheap, always available), a
# meaningful chunk of pure on-screen text (breaks the "always narrated
# intro" pattern outright), and voice_bank whenever the channel has real
# human recordings banked -- see _resolve_intro_mode for the empty-bank
# fallback. Tune per channel later if needed; this is just the shared default.
_MIXED_WEIGHTS = {"tts": 0.60, "text_only": 0.25, "voice_bank": 0.15}
_TEXT_ONLY_CARD_SECONDS = 4.5

# Fixed target params for the intro clip's own encode (see _render_intro_clip)
# -- also what `_can_stream_copy` compares the ORIGINAL clip against to decide
# whether it can be concat-demuxed with `-c copy` instead of fully re-encoded.
_INTRO_FPS = 30
_INTRO_AUDIO_RATE = 44100
_INTRO_AUDIO_CHANNELS = 2


def _pick_weighted(weights: dict) -> str:
    import random

    items = list(weights.items())
    total = sum(w for _, w in items) or 1.0
    r = random.uniform(0, total)
    upto = 0.0
    for key, w in items:
        upto += w
        if r <= upto:
            return key
    return items[-1][0]


def _voice_bank_dir(channel_id: int | None) -> Path | None:
    if not channel_id:
        return None
    return Path(settings.abs_path(settings.assets_dir)) / "voice_bank" / str(channel_id)


def _voice_bank_files(channel_id: int | None) -> list[Path]:
    """Operator-recorded modular phrases ("Aqui vai minha visao sobre isso:"
    etc.), recorded once per channel and committed under
    assets/voice_bank/<channel_id>/ -- NOT generated, NOT per-video. Empty
    (or missing channel_id) just means this channel hasn't recorded any yet."""
    d = _voice_bank_dir(channel_id)
    if not d or not d.exists():
        return []
    return sorted(p for p in d.glob("*.mp3") if p.is_file())


def _resolve_intro_mode(intro_mode: str | None, channel_id: int | None) -> str:
    mode = (intro_mode or "tts").strip().lower()
    if mode not in _INTRO_MODES:
        mode = "tts"
    if mode == "mixed":
        mode = _pick_weighted(_MIXED_WEIGHTS)
    if mode == "voice_bank" and not _voice_bank_files(channel_id):
        mode = "tts"
    return mode


def _apply_long_intro(
    job_id: int, local_path: str, take: dict, analysis: dict,
    visual_theme: dict | None = None, voice: str | None = None,
    intro_mode: str | None = None, channel_id: int | None = None,
) -> str | None:
    src = Path(local_path)
    dims = _dims(src, analysis)
    if not dims:
        return None
    w, h = dims
    work = _work_dir(job_id)

    mode = _resolve_intro_mode(intro_mode, channel_id)
    line = (take.get("line") or take.get("spoken") or "").strip()
    if not line:
        return None

    narration_path: Path | None
    if mode == "text_only":
        narration_path = None
        narr_seconds = _TEXT_ONLY_CARD_SECONDS
    elif mode == "voice_bank":
        bank = _voice_bank_files(channel_id)
        if not bank:  # bank emptied between _resolve_intro_mode and here (race) -- bail cleanly
            return None
        import random

        narration_path = random.choice(bank)
        narr_seconds = _probe_duration(narration_path)
        if narr_seconds <= 0:
            return None
    else:  # tts
        spoken = take.get("spoken")
        if not spoken:
            return None
        narration_path = work / "intro_narration.mp3"
        resolved_voice = voice or settings.default_tts_voice or "pt-BR-AntonioNeural"
        asyncio.run(_synthesize(spoken, resolved_voice, narration_path))
        if not narration_path.exists() or narration_path.stat().st_size < 1024:
            return None
        narr_seconds = _probe_duration(narration_path)
        if narr_seconds <= 0:
            return None

    card = work / "intro_card.jpg"
    _render_intro_card(job_id, src, line, w, h, card, visual_theme)

    intro_clip = work / "intro_clip.mp4"
    _render_intro_clip(card, narration_path, narr_seconds, w, h, intro_clip)
    if not intro_clip.exists() or intro_clip.stat().st_size == 0:
        return None

    has_audio = bool(analysis.get("has_audio"))
    orig_duration = float(analysis.get("duration") or 0) or _probe_duration(src)
    dst = work / f"curated_{src.name}"
    if not _concat(intro_clip, src, orig_duration, has_audio, w, h, dst):
        return None
    logger.info("ready_video_curation_intro_mode job_id=%s mode=%s", job_id, mode)
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


def _render_intro_clip(card: Path, narration: Path | None, narr_seconds: float, w: int, h: int, dst: Path) -> None:
    """narration=None (text_only mode) still produces a clip with exactly one
    (silent) audio stream -- via anullsrc, not `-an` -- so downstream _concat
    never needs a third has-audio combination to handle."""
    duration = narr_seconds if narration is None else narr_seconds + 0.4
    inputs = ["-loop", "1", "-i", str(card)]
    if narration is not None:
        inputs += ["-i", str(narration)]
    else:
        inputs += ["-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo:d={duration:.3f}"]
    cmd = [
        "ffmpeg", "-y", *inputs,
        "-c:v", "libx264", "-tune", "stillimage", "-preset", "veryfast",
        "-pix_fmt", "yuv420p", "-r", str(_INTRO_FPS),
        "-threads", str(max(1, settings.ffmpeg_threads)),
        "-vf", f"scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h}",
        "-c:a", "aac", "-b:a", "192k", "-ar", str(_INTRO_AUDIO_RATE), "-ac", str(_INTRO_AUDIO_CHANNELS),
        "-shortest", "-t", f"{duration:.3f}",
        str(dst),
    ]
    _run(cmd)


def _concat(intro: Path, original: Path, orig_duration: float, has_audio: bool,
            w: int, h: int, dst: Path) -> bool:
    # Fast path: the ORIGINAL clip can run several minutes -- if it already
    # matches the intro clip's own encode params (h264/yuv420p/same WxH/30fps,
    # aac/44100/stereo audio), the concat DEMUXER can stream-copy it untouched
    # and only the few-second intro needs a real encode. Falls through to the
    # filter-based re-encode below on any mismatch or ffmpeg failure.
    if has_audio and _can_stream_copy(original, w, h) and _concat_copy(intro, original, dst):
        return True

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


def _can_stream_copy(original: Path, w: int, h: int) -> bool:
    vinfo = _probe_video_stream_info(original)
    if not vinfo:
        return False
    if vinfo["codec_name"] != "h264" or vinfo["pix_fmt"] != "yuv420p":
        return False
    if vinfo["width"] != w or vinfo["height"] != h:
        return False
    if abs(vinfo["fps"] - _INTRO_FPS) > 0.05:
        return False
    ainfo = _probe_audio_stream_info(original)
    if not ainfo:
        return False
    if ainfo["codec_name"] != "aac":
        return False
    if ainfo["sample_rate"] != _INTRO_AUDIO_RATE or ainfo["channels"] != _INTRO_AUDIO_CHANNELS:
        return False
    return True


def _probe_entries(path: Path, select_stream: str, entries: str) -> dict[str, str]:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", select_stream,
         "-show_entries", f"stream={entries}",
         "-of", "default=noprint_wrappers=1", str(path)],
        capture_output=True, text=True, timeout=15,
    )
    fields: dict[str, str] = {}
    for line in out.stdout.splitlines():
        key, sep, value = line.partition("=")
        if sep:
            fields[key] = value
    return fields


def _probe_video_stream_info(path: Path) -> dict | None:
    try:
        fields = _probe_entries(path, "v:0", "codec_name,pix_fmt,width,height,r_frame_rate")
        return {
            "codec_name": fields["codec_name"],
            "pix_fmt": fields["pix_fmt"],
            "width": int(fields["width"]),
            "height": int(fields["height"]),
            "fps": _parse_frame_rate(fields["r_frame_rate"]),
        }
    except Exception:
        return None


def _probe_audio_stream_info(path: Path) -> dict | None:
    try:
        fields = _probe_entries(path, "a:0", "codec_name,sample_rate,channels")
        return {
            "codec_name": fields["codec_name"],
            "sample_rate": int(fields["sample_rate"]),
            "channels": int(fields["channels"]),
        }
    except Exception:
        return None


def _parse_frame_rate(value: str) -> float:
    try:
        if "/" in value:
            num, den = value.split("/", 1)
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        return float(value)
    except Exception:
        return 0.0


def _concat_copy(intro: Path, original: Path, dst: Path) -> bool:
    list_path = dst.with_name(f"{dst.stem}_concat_list.txt")
    entries = "\n".join(_concat_list_entry(p) for p in (intro, original))
    list_path.write_text(entries + "\n", encoding="utf-8")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
           "-c", "copy", "-movflags", "+faststart", str(dst)]
    try:
        _run(cmd)
    except Exception as exc:  # noqa: BLE001
        logger.info("concat stream-copy fast path failed, falling back to re-encode: %s", exc)
        return False
    return dst.exists() and dst.stat().st_size > 0


def _concat_list_entry(path: Path) -> str:
    escaped = str(path).replace("\\", "/").replace("'", "'\\''")
    return f"file '{escaped}'"


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
