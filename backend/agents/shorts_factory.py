"""
ShortsFactoryAgent — derive vertical Shorts (1080x1920) from the main video.

5 formats:
  1. Hook (8s)        - always the first seconds
  2. Standard (15s)   - window with the most [DESTAQUE] markers
  3. Medium (30s)     - densest continuous block
  4. Long (60s)       - top-scored 60s
  5. Mini-episode (90s) - most complete narrative window

Landscape -> portrait via centered 9:16 crop; a "Siga para mais" banner is
overlaid at the top (rendered as a PNG to avoid ffmpeg font-path escaping pain).
The main video's burned captions survive the centered crop.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from backend.agents.base_agent import BaseAgent
from backend.agents.video_editor import VENC, _run
from backend.config import settings

FORMATS = [
    {"num": 1, "name": "hook", "length": 8, "from_start": True},
    {"num": 2, "name": "standard", "length": 15, "from_start": False},
    {"num": 3, "name": "medium", "length": 30, "from_start": False},
    {"num": 4, "name": "long", "length": 60, "from_start": False},
    {"num": 5, "name": "mini", "length": 90, "from_start": False},
]


class ShortsFactoryAgent(BaseAgent):
    name = "shorts_factory"

    async def run(
        self,
        main_video: dict | None = None,
        narration: dict | None = None,
        formats: list[int] | None = None,
        **_,
    ) -> dict:
        main_video = main_video or self.ctx_get("main_video") or {}
        narration = narration or self.ctx_get("narration") or {}
        src = main_video.get("main_video_path")
        if not src or not Path(src).exists():
            raise FileNotFoundError("Vídeo principal não encontrado para gerar Shorts.")

        total = float(main_video.get("duration") or narration.get("total_duration") or 0)
        markers = [m["timestamp"] for m in narration.get("markers", [])]
        wanted = set(formats) if formats else {f["num"] for f in FORMATS}

        out_dir = self.job_dir(self.job_id, settings.abs_path(settings.output_dir))
        banner = self._banner(out_dir)

        jid = "adhoc" if self.job_id is None else self.job_id
        produced: list[dict] = []
        for fmt in FORMATS:
            if fmt["num"] not in wanted:
                continue
            length = fmt["length"]
            # Skip formats longer than the source (keep at least the hook).
            if total < length * 0.6 and fmt["num"] != 1:
                continue
            eff_len = min(length, total)
            start = 0.0 if fmt["from_start"] else self._best_window(markers, eff_len, total)
            dst = out_dir / f"video_{jid}_short_{fmt['num']}.mp4"
            await asyncio.to_thread(self._cut_vertical, src, dst, start, eff_len, banner)
            produced.append({
                "num": fmt["num"], "name": fmt["name"], "path": str(dst),
                "start": round(start, 2), "length": round(eff_len, 2),
            })
            self.emit("progress", f"Short {fmt['num']} ({fmt['name']}, {eff_len:.0f}s)", progress=80)

        self.ctx_set("shorts", produced)
        self.emit("progress", f"{len(produced)} Shorts gerados", progress=82)
        return {"shorts": produced}

    @staticmethod
    def _best_window(markers: list[float], length: float, total: float) -> float:
        """Slide a window of `length`; pick the start with the most markers."""
        if total <= length or not markers:
            return 0.0
        best_start, best_score = 0.0, -1
        step = max(1.0, length / 6)
        t = 0.0
        while t <= total - length:
            score = sum(1 for m in markers if t <= m < t + length)
            if score > best_score:
                best_score, best_start = score, t
            t += step
        return best_start

    def _cut_vertical(self, src: str, dst: Path, start: float, length: float, banner: Path) -> None:
        # crop centered 9:16, scale to 1080x1920, overlay banner at top.
        filt = (
            "[0:v]crop=ih*9/16:ih,scale=1080:1920,setsar=1[v];"
            "[v][1:v]overlay=(W-w)/2:70[vout]"
        )
        cmd = [
            "ffmpeg", "-y", "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", src,
            "-i", str(banner),
            "-filter_complex", filt, "-map", "[vout]", "-map", "0:a?",
            *VENC, "-c:a", "aac", "-b:a", "192k", "-shortest", str(dst),
        ]
        _run(cmd)

    @staticmethod
    def _banner(out_dir: Path) -> Path:
        path = out_dir / "_short_banner.png"
        if path.exists():
            return path
        w, h = 720, 110
        img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        try:
            font = ImageFont.truetype("arialbd.ttf", 46)
        except Exception:
            font = ImageFont.load_default()
        text = "↑ Siga para mais"
        tw = d.textlength(text, font=font)
        # rounded translucent pill
        d.rounded_rectangle([0, 0, w, h], radius=28, fill=(0, 0, 0, 150))
        d.text(((w - tw) / 2, (h - 52) / 2), text, font=font, fill=(255, 255, 255, 255),
               stroke_width=2, stroke_fill=(0, 0, 0, 255))
        img.save(path, "PNG")
        return path


# --- standalone test ---
if __name__ == "__main__":
    async def _demo():
        out = settings.abs_path(settings.output_dir) / "job_0"
        main = next(out.glob("video_*_main.mp4"), None)
        if not main:
            print("Run video_editor --test first.")
            return
        import json
        ts = out / "narration_timestamps.json"
        narration = json.loads(ts.read_text(encoding="utf-8")) if ts.exists() else {}
        ctx = {"main_video": {"main_video_path": str(main), "duration": narration.get("total_duration", 8)},
               "narration": narration}
        agent = ShortsFactoryAgent(job_id=0, context=ctx, emit=False)
        r = await agent.execute()
        for s in r["shorts"]:
            print(s)

    asyncio.run(_demo())
