"""
QualityControlAgent — technical + content checks BEFORE the approval queue.

A hard failure (resolution/duration/audio/corrupt/black) sends the job back to
the responsible stage. Low-bitrate or minor issues are warnings, not failures
(simple content legitimately compresses below 3 Mbps).

Status: qc_passed | qc_warning | qc_failed_resolution | qc_failed_duration
        | qc_failed_audio | qc_failed_blackframes | qc_failed_corrupt
        | qc_failed_thumbnail
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
        if not path or not Path(path).exists():
            return {"status": "qc_failed_corrupt", "warnings": ["arquivo ausente"]}

        meta = self._probe(path)
        if not meta:
            return {"status": "qc_failed_corrupt", "warnings": ["ffprobe não conseguiu ler o arquivo"]}

        w, h = meta["width"], meta["height"]
        if not ((w >= 1920 and h >= 1080) or (w == 1080 and h == 1080)):
            return {"status": "qc_failed_resolution", "warnings": [f"resolução {w}x{h}"], "meta": meta}

        # The video is built to the narration length, so that's the authoritative
        # "planned" baseline. Fall back to the script's estimate only if silent.
        planned = float(narration.get("total_duration") or script.get("estimated_duration") or 0)
        dur = meta["duration"]
        if planned:
            ratio = dur / planned if planned else 1
            if ratio < 0.5 or ratio > 1.6:
                return {"status": "qc_failed_duration",
                        "warnings": [f"duração {dur:.1f}s vs planejado {planned:.0f}s"], "meta": meta}
            if not (0.8 <= ratio <= 1.2):
                warnings.append(f"duração {dur:.1f}s fora de 80-120% do plano ({planned:.0f}s)")

        mean_db = self._mean_volume(path)
        if mean_db is not None and mean_db < -30:
            return {"status": "qc_failed_audio", "warnings": [f"áudio baixo ({mean_db:.1f} dB)"], "meta": meta}

        if self._has_long_blackframes(path):
            return {"status": "qc_failed_blackframes", "warnings": ["frames pretos > 3s"], "meta": meta}

        if meta["bitrate"] and meta["bitrate"] < 3_000_000:
            warnings.append(f"bitrate baixo ({meta['bitrate'] / 1e6:.1f} Mbps)")

        # Thumbnail present & not blank.
        thumbs = (visuals or {}).get("thumbnails", {})
        thumb_a = thumbs.get("A", {}).get("landscape")
        if not thumb_a or not Path(thumb_a).exists():
            return {"status": "qc_failed_thumbnail", "warnings": ["thumbnail ausente"], "meta": meta}
        if self._is_blank(thumb_a):
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
