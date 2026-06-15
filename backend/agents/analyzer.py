"""
AnalyzerAgent (Remix Engine) — extract a StyleDNA from a reference video.

The reference is used ONLY for style analysis. No frame, audio, or clip from it
ever enters the final video — the studio produces 100% original content.

Steps: yt-dlp download (480p, cached) -> ffprobe metadata -> 12 evenly spaced
frames -> Pillow colour/brightness/saturation -> pydub audio (voice vs music,
energy) -> StyleDNA JSON. Degrades to heuristics if a step is unavailable.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.config import settings


class AnalyzerAgent(BaseAgent):
    name = "analyzer"

    async def run(self, source: str = "", **_) -> dict:
        if not source:
            raise ValueError("source (URL ou caminho) é obrigatório")
        self.emit("progress", "Analisando referência (apenas estilo)", progress=10)
        dna = await asyncio.to_thread(self._analyze, source)
        # Auto-detect the reference's TOPIC (legal: from public metadata only) so the
        # Remix UI can pre-fill the theme — no frame/audio/clip is reused.
        dna["suggested_theme"] = await self._suggest_theme()
        dna["suggested_format"] = "short" if dna.get("aspect_ratio") in ("9:16", "1:1") else "long"
        self.ctx_set("style_dna", dna)
        self.emit("progress", f"StyleDNA pronto: {dna['content_type']}", progress=100)
        return dna

    async def _suggest_theme(self) -> str:
        """Turn the reference's title/description (yt-dlp metadata) into a clean PT
        theme for a NEW, original video on the same subject. Metadata only."""
        meta = getattr(self, "_ref_meta", {}) or {}
        title = (meta.get("title") or "").strip()
        desc = (meta.get("description") or "").strip()
        if not title and not desc:
            return ""
        try:
            from backend import llm

            prompt = (
                "Com base no título/descrição de um vídeo de referência abaixo, escreva em "
                "PORTUGUÊS um TEMA curto (máx. 12 palavras) para um NOVO vídeo original sobre o "
                "MESMO assunto. Responda só o tema, sem aspas.\n\n"
                f"Título: {title}\nDescrição: {desc[:500]}"
            )
            theme = await llm.complete(prompt, system="Responda apenas o tema, curto.", max_tokens=60)
            return (theme or "").strip().strip('"').splitlines()[0][:120]
        except Exception:
            return title[:120]

    def _analyze(self, source: str) -> dict:
        self._ref_meta = {}
        ref = self._resolve(source)
        if not ref or not Path(ref).exists():
            return self._heuristic_dna(reason="download_failed")

        meta = self._probe(ref)
        frames, frames_dir = self._extract_frames(ref, meta.get("duration", 0))
        try:
            visual = self._visual(frames)
        finally:
            shutil.rmtree(frames_dir, ignore_errors=True)  # isolate + clean per analysis
        audio = self._audio(ref)
        shot_rate = self._shot_rate(ref, meta.get("duration", 0))
        return self._build_dna(meta, visual, audio, measured_clip=shot_rate)

    @staticmethod
    def _shot_rate(path: str, duration: float) -> float | None:
        """Average shot length (s) via ffmpeg scene-cut detection on the first 90s.
        Captures the reference's CUTTING RHYTHM only — no frame is reused."""
        try:
            # -t before nothing-seek + downscale + -an keeps this cheap; timeout guards
            # pathological/streaming inputs. 60s of cuts is plenty to estimate rhythm.
            out = subprocess.run(
                ["ffmpeg", "-i", path, "-t", "60", "-an",
                 "-vf", "scale=480:-2,select='gt(scene,0.3)',showinfo", "-f", "null", "-"],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
            cuts = (out.stderr or "").count("pts_time:")
            window = min(60.0, duration or 60.0)
            if cuts >= 2 and window > 0:
                return round(max(1.2, min(8.0, window / cuts)), 2)
        except Exception:
            pass
        return None

    # ---- acquire ----
    def _resolve(self, source: str) -> str | None:
        if source.startswith("http"):
            return self._download(source)
        return source if Path(source).exists() else None

    def _download(self, url: str) -> str | None:
        cache = settings.abs_path(settings.cache_dir) / "remix_refs"
        cache.mkdir(parents=True, exist_ok=True)
        key = hashlib.md5(url.encode()).hexdigest()[:12]
        out_tmpl = str(cache / f"{key}.%(ext)s")
        meta_path = cache / f"{key}.meta.json"
        existing = [p for p in cache.glob(f"{key}.*") if p.suffix != ".json"]
        if existing:
            if meta_path.exists():
                try:
                    self._ref_meta = json.loads(meta_path.read_text(encoding="utf-8"))
                except Exception:
                    pass
            return str(existing[0])
        try:
            import yt_dlp  # type: ignore

            opts = {"format": "best[height<=480]/best", "outtmpl": out_tmpl,
                    "quiet": True, "no_warnings": True, "noplaylist": True}
            with yt_dlp.YoutubeDL(opts) as ydl:
                info = ydl.extract_info(url, download=True) or {}
            # Public metadata only (title/description) — used to suggest a theme.
            self._ref_meta = {"title": info.get("title", "") or "",
                              "description": (info.get("description") or "")[:1000]}
            try:
                meta_path.write_text(json.dumps(self._ref_meta, ensure_ascii=False), encoding="utf-8")
            except Exception:
                pass
            found = [p for p in cache.glob(f"{key}.*") if p.suffix != ".json"]
            return str(found[0]) if found else None
        except Exception as exc:  # noqa: BLE001
            self.emit("progress", f"yt-dlp indisponível/falhou: {exc}", progress=10)
            return None

    # ---- probe ----
    @staticmethod
    def _probe(path: str) -> dict:
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-print_format", "json",
                 "-show_streams", "-show_format", path],
                capture_output=True, text=True,
            )
            data = json.loads(out.stdout)
            v = next((s for s in data["streams"] if s["codec_type"] == "video"), {})
            num, den = (v.get("r_frame_rate", "30/1").split("/") + ["1"])[:2]
            fps = round(float(num) / float(den or 1), 2) if den != "0" else 30
            w, h = int(v.get("width", 1920)), int(v.get("height", 1080))
            return {"width": w, "height": h, "fps": fps,
                    "duration": float(data.get("format", {}).get("duration", 0) or 0),
                    "aspect_ratio": AnalyzerAgent._aspect(w, h)}
        except Exception:
            return {"width": 1920, "height": 1080, "fps": 30, "duration": 0, "aspect_ratio": "16:9"}

    @staticmethod
    def _aspect(w: int, h: int) -> str:
        if h == 0:
            return "16:9"
        r = w / h
        if r < 0.7:
            return "9:16"
        if 0.9 <= r <= 1.1:
            return "1:1"
        return "16:9"

    # ---- frames + colour ----
    def _extract_frames(self, path: str, duration: float) -> tuple[list[Path], Path]:
        # Unique per-call dir (md5(source)+pid) so concurrent /remix analyses can't
        # read each other's frames into one StyleDNA. Cleaned by the caller.
        key = hashlib.md5(f"{path}:{os.getpid()}".encode()).hexdigest()[:12]
        tmp = settings.abs_path(settings.temp_dir) / "remix_frames" / key
        tmp.mkdir(parents=True, exist_ok=True)
        frames = []
        n = 12
        dur = duration or 12
        for i in range(n):
            ts = max(0.1, dur * (i + 0.5) / n)
            dst = tmp / f"frame_{i:02d}.jpg"
            try:
                subprocess.run(["ffmpeg", "-y", "-ss", f"{ts:.2f}", "-i", path,
                                "-frames:v", "1", "-q:v", "3", str(dst)],
                               capture_output=True, timeout=30)
                if dst.exists():
                    frames.append(dst)
            except Exception:
                continue
        return frames, tmp

    def _visual(self, frames: list[Path]) -> dict:
        from PIL import Image, ImageStat

        if not frames:
            return {"dominant_colors": ["#222233"], "brightness": 0.4, "saturation": 0.5,
                    "has_vignette": False, "grading_style": "natural"}
        colors: list[str] = []
        bright_vals, sat_vals = [], []
        for f in frames:
            try:
                img = Image.open(f).convert("RGB").resize((160, 90))
                pal = img.convert("P", palette=Image.ADAPTIVE, colors=4).convert("RGB")
                top = sorted(pal.getcolors(160 * 90) or [], reverse=True)[:2]
                for _, rgb in top:
                    colors.append("#%02x%02x%02x" % rgb)
                bright_vals.append(sum(ImageStat.Stat(img.convert("L")).mean) / 255)
                hsv = img.convert("HSV")
                sat_vals.append(ImageStat.Stat(hsv).mean[1] / 255)
            except Exception:
                continue
        # most common colours
        freq: dict[str, int] = {}
        for c in colors:
            freq[c] = freq.get(c, 0) + 1
        dominant = [c for c, _ in sorted(freq.items(), key=lambda kv: kv[1], reverse=True)[:5]]
        brightness = round(sum(bright_vals) / len(bright_vals), 2) if bright_vals else 0.4
        saturation = round(sum(sat_vals) / len(sat_vals), 2) if sat_vals else 0.5
        return {
            "dominant_colors": dominant or ["#222233"],
            "brightness": brightness,
            "saturation": saturation,
            "has_vignette": brightness < 0.35,
            "grading_style": self._grade_from(brightness, saturation),
        }

    @staticmethod
    def _grade_from(brightness: float, saturation: float) -> str:
        if saturation > 0.6 and brightness > 0.5:
            return "vibrant_pop"
        if brightness < 0.35:
            return "noir" if saturation < 0.3 else "warm_golden"
        if saturation < 0.3:
            return "muted_tones"
        return "natural"

    # ---- audio ----
    def _audio(self, path: str) -> dict:
        try:
            from pydub import AudioSegment, silence

            seg = AudioSegment.from_file(path)
            if len(seg) == 0:
                raise ValueError("empty")
            loud = seg.dBFS
            silences = silence.detect_silence(seg[:60000], min_silence_len=300, silence_thresh=seg.dBFS - 16)
            silence_ratio = sum((b - a) for a, b in silences) / min(len(seg), 60000)
            # Speech tends to have frequent short pauses; music is more continuous.
            has_narration = 0.05 < silence_ratio < 0.45
            return {
                "has_narration": has_narration,
                "has_music": True,
                "bpm_estimate": None,
                "music_mood": "dramatic" if loud < -20 else "energetic",
                "energy": round(min(1.0, max(0.0, (loud + 40) / 40)), 2),
            }
        except Exception:
            return {"has_narration": True, "has_music": True, "bpm_estimate": None,
                    "music_mood": "dramatic", "energy": 0.5}

    # ---- assemble ----
    def _build_dna(self, meta: dict, visual: dict, audio: dict, measured_clip: float | None = None) -> dict:
        content_type = self._infer_type(meta, audio)
        avg_clip = measured_clip or (
            2.5 if content_type == "sports_highlights"
            else (4.5 if content_type == "film_recap_ai_images" else 5.0))
        return {
            "content_type": content_type,
            "aspect_ratio": meta["aspect_ratio"],
            "resolution": f"{meta['width']}x{meta['height']}",
            "fps": meta["fps"],
            "duration": round(meta["duration"], 1),
            "visual_style": {
                "dominant_colors": visual["dominant_colors"],
                "brightness": visual["brightness"],
                "saturation": visual["saturation"],
                "has_vignette": visual["has_vignette"],
                "grading_style": visual["grading_style"],
            },
            "text_overlay": {"present": True, "position": "bottom", "font_size_estimate": "large"},
            "pacing": {"avg_clip_duration": avg_clip,
                       "style": "fast" if avg_clip < 3 else ("medium" if avg_clip < 5 else "slow"),
                       "beat_sync": content_type != "quote_viral",
                       "shots_measured": bool(measured_clip)},
            "audio": {"has_narration": audio["has_narration"], "has_music": audio["has_music"],
                      "bpm_estimate": audio.get("bpm_estimate"), "music_mood": audio["music_mood"]},
            "effects": [visual["grading_style"]] + (["vignette"] if visual["has_vignette"] else []),
            "template_recommendation": content_type,
        }

    @staticmethod
    def _infer_type(meta: dict, audio: dict) -> str:
        dur = meta["duration"]
        if meta["aspect_ratio"] == "9:16" and dur and dur <= 20 and not audio["has_narration"]:
            return "quote_viral"
        if dur and dur <= 90 and audio.get("energy", 0.5) > 0.6:
            return "sports_highlights"
        return "film_recap_ai_images"

    def _heuristic_dna(self, reason: str = "") -> dict:
        meta = {"width": 1920, "height": 1080, "fps": 30, "duration": 0, "aspect_ratio": "16:9"}
        dna = self._build_dna(meta, {"dominant_colors": ["#1a1a26", "#6c5ce7"], "brightness": 0.4,
                                     "saturation": 0.5, "has_vignette": True, "grading_style": "warm_golden"},
                              {"has_narration": True, "has_music": True, "bpm_estimate": None,
                               "music_mood": "dramatic", "energy": 0.5})
        dna["_note"] = f"StyleDNA heurístico ({reason}) — referência não pôde ser baixada."
        return dna


if __name__ == "__main__":
    import sys

    async def _demo():
        src = sys.argv[1] if len(sys.argv) > 1 else "https://example.com/x"
        agent = AnalyzerAgent(job_id=None, emit=False)
        print(json.dumps(await agent.execute(source=src), ensure_ascii=False, indent=2))

    asyncio.run(_demo())
