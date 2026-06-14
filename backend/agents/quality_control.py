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
import re
import subprocess
from pathlib import Path

from backend.agents.base_agent import BaseAgent


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

        path = main_video.get("main_video_path")
        report = await asyncio.to_thread(self._check, path, visuals, shorts, script, narration)
        self.ctx_set("qc", report)
        status = report["status"]
        self.emit("progress", f"QC: {status}", progress=88, qc=report)
        return report

    def _check(self, path, visuals, shorts, script, narration=None) -> dict:
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
        w, h = meta["width"], meta["height"]
        if not ((w >= 1920 and h >= 1080) or (w == 1080 and h == 1080)):
            warnings.append(f"resolução fora do padrão ({w}x{h})")

        # The video is built to the narration length, so that's the authoritative
        # "planned" baseline. Fall back to the script's estimate only if silent.
        planned = float(narration.get("total_duration") or script.get("estimated_duration") or 0)
        if planned:
            ratio = dur / planned
            if ratio < 0.5 or ratio > 1.6:
                warnings.append(f"duração {dur:.1f}s muito fora do plano ({planned:.0f}s)")
            elif not (0.8 <= ratio <= 1.2):
                warnings.append(f"duração {dur:.1f}s fora de 80-120% do plano ({planned:.0f}s)")

        mean_db = self._mean_volume(path)
        if mean_db is not None and mean_db < -30:
            warnings.append(f"áudio baixo ({mean_db:.1f} dB)")

        # Black frames are EXPECTED for dark content (quote_viral, dark
        # intros/outros). Flag for review, never abort.
        if self._has_long_blackframes(path):
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

        # Shorts vertical resolution.
        for s in shorts or []:
            sm = self._probe(s.get("path"))
            if sm and not (sm["width"] == 1080 and sm["height"] == 1920):
                warnings.append(f"short {s.get('num')} não é 1080x1920")

        status = "qc_warning" if warnings else "qc_passed"
        return {"status": status, "warnings": warnings, "meta": meta}

    # ---- ffprobe helpers ----
    @staticmethod
    def _probe(path) -> dict | None:
        if not path or not Path(path).exists():
            return None
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", "-show_format", str(path)],
                capture_output=True, text=True,
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
        except Exception:
            return None

    @staticmethod
    def _mean_volume(path) -> float | None:
        try:
            out = subprocess.run(
                ["ffmpeg", "-i", str(path), "-af", "volumedetect", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            m = re.search(r"mean_volume:\s*(-?\d+\.?\d*)\s*dB", out.stderr)
            return float(m.group(1)) if m else None
        except Exception:
            return None

    @staticmethod
    def _has_long_blackframes(path) -> bool:
        try:
            out = subprocess.run(
                ["ffmpeg", "-i", str(path), "-vf", "blackdetect=d=3:pic_th=0.98",
                 "-an", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
            )
            return "black_start" in out.stderr
        except Exception:
            return False

    @staticmethod
    def _is_blank(image_path) -> bool:
        try:
            from PIL import Image, ImageStat

            stat = ImageStat.Stat(Image.open(image_path).convert("L"))
            return stat.stddev[0] < 8  # near-uniform image
        except Exception:
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
