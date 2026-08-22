"""
QualityControlAgent — technical + content checks BEFORE the approval queue.

Only *fatal* defects fail the job (status qc_failed_*, which aborts the pipeline
in the orchestrator): the video file is missing, the file is corrupt/unreadable
by ffprobe, there is no video stream, or the duration is zero/invalid. Those make
the artifact unusable, so there is nothing for a human to approve.

Everything else is a *quality* concern, not a fatal one. Black frames (legit for
dark content like quote_viral), low bitrate, low audio, off-resolution,
off-duration, missing/blank thumbnail, off-spec shorts — these accumulate in
warnings[] and the status becomes qc_warning. The orchestrator treats qc_warning
like qc_passed and lets the job proceed to compliance -> AWAITING_APPROVAL, so a
human decides on the approval card (which shows meta + warnings).

Status: qc_passed | qc_warning | qc_failed_corrupt | qc_failed_duration
        (qc_failed_* are the only statuses that abort; qc_failed_duration is
         reserved for a zero/invalid duration, i.e. a broken file)
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.agents.video_editor import H as _LANDSCAPE_H, W as _LANDSCAPE_W

logger = logging.getLogger("studio.quality_control")

# ffprobe is a cheap metadata read; a hung/oversized ffmpeg analysis pass
# (volumedetect/blackdetect decode the whole file) gets a longer but still
# bounded budget so a malformed input can never stall the QC thread forever.
_FFPROBE_TIMEOUT = 15
_FFMPEG_ANALYSIS_TIMEOUT = 300


class QualityControlAgent(BaseAgent):
    name = "quality_control"

    async def run(
        self,
        main_video: dict | None = None,
        visuals: dict | None = None,
        shorts: list | None = None,
        script: dict | None = None,
        narration: dict | None = None,
        **_,
    ) -> dict:
        main_video = main_video or self.ctx_get("main_video") or {}
        visuals = visuals or self.ctx_get("visuals") or {}
        shorts = shorts if shorts is not None else (self.ctx_get("shorts") or [])
        script = script or self.ctx_get("script") or {}
        narration = narration or self.ctx_get("narration") or {}
        video_format = self.ctx_get("format") or script.get("format") or "long"

        path = main_video.get("main_video_path")
        report = await asyncio.to_thread(
            self._check, path, visuals, shorts, script, narration, video_format
        )
        self.ctx_set("qc", report)
        status = report["status"]
        self.emit("progress", f"QC: {status}", progress=88, qc=report)
        return report

    def _check(self, path, visuals, shorts, script, narration=None, video_format="long") -> dict:
        warnings: list[str] = []
        narration = narration or {}

        # ---- FATAL checks: the artifact is unusable, nothing to approve. ----
        # These keep a qc_failed_* status, which the orchestrator treats as an
        # abort (job -> ERROR). Limited to genuinely broken outputs.
        if not path or not Path(path).exists():
            return {"status": "qc_failed_corrupt", "warnings": ["arquivo de vídeo ausente"]}

        # _probe returns None when ffprobe cannot read the file OR when there is
        # no video stream — both are fatal.
        meta = self._probe(path)
        if not meta:
            return {"status": "qc_failed_corrupt",
                    "warnings": ["arquivo corrompido / sem stream de vídeo / ilegível pelo ffprobe"]}

        # Zero/invalid duration means there is effectively no video to review.
        dur = meta["duration"]
        if not dur or dur <= 0:
            return {"status": "qc_failed_duration",
                    "warnings": [f"duração inválida ({dur:.1f}s)"], "meta": meta}

        # ---- QUALITY checks: accumulate as warnings, let the human decide. ----
        # Status becomes qc_warning (not qc_failed_*), so the orchestrator lets
        # the job continue to compliance -> AWAITING_APPROVAL.
        # Compared against video_editor's actual render target (settings.video_resolution),
        # not a fixed 1920x1080 — a native short swaps W/H just like the renderer does.
        w, h = meta["width"], meta["height"]
        expected_w, expected_h = (
            (_LANDSCAPE_H, _LANDSCAPE_W) if video_format == "short" else (_LANDSCAPE_W, _LANDSCAPE_H)
        )
        if (w, h) != (expected_w, expected_h):
            warnings.append(f"resolução fora do padrão ({w}x{h}, esperado {expected_w}x{expected_h})")

        # The video is built to the narration length, so that's the authoritative
        # "planned" baseline. Fall back to the script's estimate only if silent.
        planned = float(narration.get("total_duration") or script.get("estimated_duration") or 0)
        if planned:
            ratio = dur / planned
            if ratio < 0.5 or ratio > 1.6:
                warnings.append(f"duração {dur:.1f}s muito fora do plano ({planned:.0f}s)")
            elif not (0.8 <= ratio <= 1.2):
                warnings.append(f"duração {dur:.1f}s fora de 80-120% do plano ({planned:.0f}s)")

        mean_db, has_blackframes = self._analyze_audio_video(path)
        if mean_db is not None and mean_db < -30:
            warnings.append(f"áudio baixo ({mean_db:.1f} dB)")

        # Black frames are EXPECTED for dark content (quote_viral, dark
        # intros/outros). Flag for review, never abort. A failed probe (None)
        # fails safe as a warning too, instead of silently reading as "clean".
        if has_blackframes is None:
            warnings.append("não foi possível verificar frames pretos (análise falhou)")
        elif has_blackframes:
            warnings.append("frames pretos > 3s (pode ser intencional em conteúdo escuro)")

        if meta["bitrate"] and meta["bitrate"] < 3_000_000:
            warnings.append(f"bitrate baixo ({meta['bitrate'] / 1e6:.1f} Mbps)")

        # Thumbnail present & not blank.
        thumbs = (visuals or {}).get("thumbnails", {})
        thumb_a = thumbs.get("A", {}).get("landscape")
        if not thumb_a or not Path(thumb_a).exists():
            warnings.append("thumbnail ausente")
        elif self._is_blank(thumb_a):
            warnings.append("thumbnail com baixa variação visual")

        # Shorts vertical resolution — always 1080x1920, per shorts_factory.py's
        # fixed output. A native short's "shorts" entry IS the main video file
        # (see orchestrator.py), already checked above at its own render
        # resolution; re-checking it here against the factory's spec would
        # double-penalize the same file under conflicting expectations.
        for s in shorts or []:
            s_path = s.get("path")
            if path and s_path and str(s_path) == str(path):
                continue
            sm = self._probe(s_path)
            if sm is None:
                warnings.append(f"short {s.get('num')} ausente ou corrompido")
            elif not (sm["width"] == 1080 and sm["height"] == 1920):
                warnings.append(f"short {s.get('num')} não é 1080x1920")

        status = "qc_warning" if warnings else "qc_passed"
        return {"status": status, "warnings": warnings, "meta": meta,
                "score": _quality_score(status, warnings)}


# Peso de cada categoria de warning no score de qualidade (0–100, determinístico).
# Casado pelo prefixo da mensagem gerada acima — warnings novos sem peso custam 10.
_WARNING_WEIGHTS = [
    ("resolução fora do padrão", 15),
    ("muito fora do plano", 15),
    ("fora de 80-120% do plano", 8),
    ("áudio baixo", 20),
    ("frames pretos", 8),
    ("não foi possível verificar frames", 5),
    ("bitrate baixo", 10),
    ("thumbnail ausente", 15),
    ("thumbnail com baixa variação", 10),
    ("ausente ou corrompido", 12),
    ("não é 1080x1920", 8),
]


def _quality_score(status: str, warnings: list[str]) -> int:
    """Score 0-100 derivado SOMENTE das checagens reais acima: 100 sem warnings;
    cada warning desconta seu peso. qc_failed_* (defeito fatal) zera."""
    if status.startswith("qc_failed"):
        return 0
    total = 100
    for w in warnings:
        weight = next((wt for prefix, wt in _WARNING_WEIGHTS if w.startswith(prefix)), 10)
        total -= weight
    return max(5, total)

    # ---- ffprobe helpers ----
    @staticmethod
    def _probe(path) -> dict | None:
        if not path or not Path(path).exists():
            return None
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", "-show_format", str(path)],
                capture_output=True, text=True, timeout=_FFPROBE_TIMEOUT,
            )
            data = json.loads(out.stdout)
            vstream = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
            if not vstream:
                return None
            fmt = data.get("format", {})
            br = fmt.get("bit_rate") or vstream.get("bit_rate")
            return {
                "width": int(vstream["width"]),
                "height": int(vstream["height"]),
                "duration": float(fmt.get("duration", 0) or 0),
                "bitrate": int(br) if br else 0,
                "vcodec": vstream.get("codec_name"),
                "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
            }
        except Exception as exc:
            logger.warning("QC ffprobe failed for %s: %s", path, exc)
            return None

    @staticmethod
    def _analyze_audio_video(path) -> tuple[float | None, bool | None]:
        """Mean volume + black-frame detection in a single decode pass (one
        ffmpeg call reading the file once, instead of two separate full decodes).

        Returns (mean_db, has_blackframes). has_blackframes is None (not False)
        when the analysis itself couldn't run, so a broken probe fails safe as
        a warning instead of silently reading as "no black frames found".
        """
        try:
            out = subprocess.run(
                ["ffmpeg", "-i", str(path), "-vf", "blackdetect=d=3:pic_th=0.98",
                 "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=_FFMPEG_ANALYSIS_TIMEOUT,
            )
        except Exception as exc:
            logger.warning("QC audio/black-frame analysis failed for %s: %s", path, exc)
            return None, None
        m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", out.stderr)
        mean_db = float(m.group(1)) if m else None
        return mean_db, "black_start" in out.stderr

    @staticmethod
    def _is_blank(image_path) -> bool:
        try:
            from PIL import Image, ImageStat

            stat = ImageStat.Stat(Image.open(image_path).convert("L"))
            return stat.stddev[0] < 8  # near-uniform image
        except Exception as exc:
            logger.warning("QC thumbnail blank-check failed for %s: %s", image_path, exc)
            return False


if __name__ == "__main__":
    import sys

    async def _demo():
        path = sys.argv[1] if len(sys.argv) > 1 else None
        agent = QualityControlAgent(job_id=0, context={
            "main_video": {"main_video_path": path},
            "visuals": {"thumbnails": {}},
        }, emit=False)
        print(await agent.execute())

    asyncio.run(_demo())
