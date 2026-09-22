"""Drive remodel — reedita o vídeo do Drive para republicação transformada.

Por que existe: republicar o arquivo do Drive byte a byte é o padrão que o
YouTube desmonetiza como "conteúdo reutilizado" (e que confunde o Content ID).
Este módulo aplica, em todo vídeo do Drive antes de publicar, uma camada de
transformação determinística (seed = job_id):

1. Reframe: crop de 2-4.5% + scale de volta (muda o hash de cada pixel).
2. Grade: brilho/saturação/contraste sutis (textura diferente, mesma cena).
3. Bumper: cartela de 1.2s com a marca do canal na frente (sinal editorial).
4. Áudio: loudnorm (níveis consistentes + hash de áudio diferente).

Tudo em poucas passagens ffmpeg baratas (preset veryfast, threads do
container). Contrato best-effort idêntico ao da curadoria: qualquer falha,
arquivo ausente ou flag desligada devolve o caminho original — nunca trava um
publish. Saída marcada com o prefixo `remodeled_` para o compliance detectar.
"""
from __future__ import annotations

import json
import logging
import random
import shutil
import subprocess
from pathlib import Path

from backend.config import settings

logger = logging.getLogger("studio.drive_remodel")

REMODEL_PREFIX = "remodeled_"
BUMPER_SECONDS = 1.2
FFMPEG_TIMEOUT_S = 1800


def _ffmpeg() -> str | None:
    return shutil.which("ffmpeg") or shutil.which("ffmpeg.exe")


def _ffprobe() -> str | None:
    return shutil.which("ffprobe") or shutil.which("ffprobe.exe")


def _probe(path: str) -> dict | None:
    """Dims, fps, duração e presença de áudio. None se der qualquer erro."""
    fp = _ffprobe()
    if not fp:
        return None
    try:
        out = subprocess.run(
            [fp, "-v", "error", "-select_streams", "v:0",
             "-show_entries", "stream=width,height,avg_frame_rate",
             "-show_entries", "format=duration", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
        data = json.loads(out.stdout or "{}")
        streams = data.get("streams") or [{}]
        audio = subprocess.run(
            [fp, "-v", "error", "-select_streams", "a",
             "-show_entries", "stream=index", "-of", "json", path],
            capture_output=True, text=True, timeout=60,
        )
        audio_data = json.loads(audio.stdout or "{}")
        fps_raw = (streams[0].get("avg_frame_rate") or "30/1").strip()
        try:
            num, den = fps_raw.split("/")
            fps = round(float(num) / float(den)) if float(den) else 30
        except (ValueError, ZeroDivisionError):
            fps = 30
        return {
            "w": int(streams[0].get("width") or 0),
            "h": int(streams[0].get("height") or 0),
            "fps": min(max(fps, 15), 60),
            "duration": float((data.get("format") or {}).get("duration") or 0),
            "has_audio": bool(audio_data.get("streams")),
        }
    except Exception:  # noqa: BLE001
        return None


def _escape_drawtext(text: str) -> str:
    return (text or "").replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")


def _fontfile() -> str | None:
    from backend.config import ROOT_DIR

    cand = ROOT_DIR / "backend" / "assets" / "fonts" / "VeraBd.ttf"
    if not cand.is_file():
        return None
    # drawtext separa opções por ':' — path Windows precisa de '/' e do
    # dois-pontos do drive escapado, senão o filtro nem parseia.
    return str(cand).replace("\\", "/").replace(":", "\\:")


def _run(cmd: list[str]) -> bool:
    try:
        subprocess.run(cmd, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=True)
        return True
    except Exception as exc:  # noqa: BLE001
        logger.info("remodel ffmpeg falhou: %s", str(exc)[:150])
        return False


def apply_remodel(*, job_id: int, local_path: str, brand_text: str = "",
                  enabled: bool = True) -> str:
    """Reedita o vídeo do Drive. Devolve o novo caminho ou o original intacto."""
    if not enabled:
        return local_path
    src = Path(local_path or "")
    if not src.is_file():
        return local_path
    if not _ffmpeg():
        return local_path
    info = _probe(str(src))
    if not info or not info["w"] or not info["h"] or info["duration"] <= 0:
        return local_path

    rng = random.Random(job_id or 0)
    w, h, fps = info["w"], info["h"], info["fps"]
    crop_pct = rng.uniform(0.02, 0.045)
    bright = round(rng.uniform(-0.03, 0.03), 3)
    sat = round(rng.uniform(1.04, 1.12), 3)
    contrast = round(rng.uniform(1.0, 1.06), 3)
    cw, ch = max(2, int(w * (1 - 2 * crop_pct)) // 2 * 2), max(2, int(h * (1 - 2 * crop_pct)) // 2 * 2)

    brand = _escape_drawtext((brand_text or "ONDA")[:40])
    font = _fontfile()
    font_opt = f":fontfile='{font}'" if font else ""
    bumper_vf = (
        f"scale={w}:{h},setsar=1,"
        f"drawtext=text='{brand}'{font_opt}:fontsize={max(12, h // 10)}:"
        f"fontcolor=white:borderw=2:bordercolor=black:"
        f"x=(w-text_w)/2:y=(h-text_h)/2,format=yuv420p"
    )
    main_vf = (
        f"crop={cw}:{ch},scale={w}:{h}:flags=bilinear,"
        f"eq=brightness={bright}:saturation={sat}:contrast={contrast},"
        f"setsar=1,format=yuv420p"
    )
    threads = str(getattr(settings, "ffmpeg_threads", 2) or 2)
    out = src.parent / f"{REMODEL_PREFIX}{src.stem}.mp4"

    color_src = f"color=c=0x0E1022:s={w}x{h}:r={fps}:d={BUMPER_SECONDS}"
    if info["has_audio"]:
        filt = (
            f"[1:v]{bumper_vf}[bv];"
            f"[2:a]atrim=0:{BUMPER_SECONDS},asetpts=PTS-STARTPTS[ba];"
            f"[0:v]{main_vf},setpts=PTS-STARTPTS[mv];"
            f"[0:a]loudnorm=I=-16:TP=-1.5:LRA=11,asetpts=PTS-STARTPTS[ma];"
            f"[bv][ba][mv][ma]concat=n=2:v=1:a=1[outv][outa]"
        )
        cmd = [_ffmpeg(), "-y",
               "-i", str(src),
               "-f", "lavfi", "-i", color_src,
               "-f", "lavfi", "-i", f"anullsrc=r=48000:d={BUMPER_SECONDS}",
               "-filter_complex", filt,
               "-map", "[outv]", "-map", "[outa]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-threads", threads, "-c:a", "aac", "-b:a", "128k",
               "-movflags", "+faststart", str(out)]
    else:
        filt = (f"[1:v]{bumper_vf}[bv];[0:v]{main_vf},setpts=PTS-STARTPTS[mv];"
                f"[bv][mv]concat=n=2:v=1:a=0[outv]")
        cmd = [_ffmpeg(), "-y",
               "-i", str(src),
               "-f", "lavfi", "-i", color_src,
               "-filter_complex", filt,
               "-map", "[outv]",
               "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
               "-threads", threads,
               "-movflags", "+faststart", str(out)]
    if _run(cmd) and out.is_file() and out.stat().st_size > 0:
        logger.info("ready_video_remodel_applied job_id=%s", job_id)
        return str(out)
    try:
        out.unlink(missing_ok=True)
    except OSError:
        pass
    return local_path
