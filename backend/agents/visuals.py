"""
VisualsAgent — generate/obtain every visual asset.

Per scene, the agent prefers REAL motion footage so the video looks shot, not
slideshow'd:
  1. STOCK VIDEO (default, all content types): search Pexels -> Pixabay video by
     the scene's concrete keywords and download a clip (type="video").
  2. AI IMAGE (fallback): when no clip matches / no key, generate a still via the
     provider chain (Pollinations -> HF FLUX -> stock photo -> placeholder); the
     editor animates it with Ken Burns so even the fallback has movement.
  THUMBNAIL (all): AI base + Pillow text, A/B variants, landscape + vertical.

Set BROLL_ENABLED=false to force the old still-image behaviour. Every network
fetch degrades gracefully so the pipeline never hard-stops offline.
"""
from __future__ import annotations

import asyncio
import hashlib
import subprocess
import urllib.parse
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.agents.base_agent import BaseAgent
from backend.config import settings

POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}"
PEXELS_VIDEO = "https://api.pexels.com/videos/search"

# 16:9 source resolution for AI image scenes. 1600x900 upscales cleanly to a 1080p
# render (only ~1.2x) for near-Full-HD sharpness, while staying much lighter to
# generate than native 1920x1080 (honours the "don't make it heavy" constraint).
SCENE_W, SCENE_H = 1600, 900

# Pexels min_width filter for B-roll search: match the configured render
# resolution instead of a fixed 1080. Downloading Full-HD clips for a 720p
# (default) render wastes bandwidth/disk with no visible quality gain, since
# the editor downscales anyway.
_PEXELS_MIN_H = max(360, min(1080, settings.video_resolution))
PEXELS_MIN_WIDTH = (round(_PEXELS_MIN_H * 16 / 9)) & ~1


class VisualsAgent(BaseAgent):
    name = "visuals"

    async def run(
        self,
        script: dict | None = None,
        content_type: str = "film_recap_ai_images",
        **_,
    ) -> dict:
        script = script or self.ctx_get("script") or {}
        content_type = script.get("content_type", content_type)

        assets_dir = self.job_dir(self.job_id, settings.abs_path(settings.temp_dir)) / "assets"
        assets_dir.mkdir(parents=True, exist_ok=True)

        scenes = script.get("scenes", [])
        scene_assets: list[dict] = []

        # Prefer REAL motion footage (licensed stock video) for every scene; the
        # AI still is only a fallback when no clip matches. This is what makes the
        # output look like video instead of "still image + narration".
        use_broll = settings.broll_enabled and bool(settings.pexels_api_key or settings.pixabay_api_key)

        self.emit("progress", f"Obtendo {len(scenes)} asset(s) visual(is)", progress=58)

        # Each scene's network calls (stock-video search/download or AI-image
        # generation) are independent and write to their own file, so they can
        # safely run concurrently. A semaphore bounds parallelism to avoid
        # hammering the provider APIs / local bandwidth.
        sem = asyncio.Semaphore(settings.visuals_concurrency)

        async def _one_scene(sc: dict) -> dict:
            idx = sc.get("index", 0)
            query = self._search_terms(sc)
            async with sem:
                # Niche topics (gaming/brands/people) are flagged ai_image by the
                # scriptwriter: stock banks have no real match and would return a
                # fuzzy, off-theme clip, so skip stock and draw an ON-THEME AI
                # image instead.
                if use_broll and query and not sc.get("ai_image"):
                    clip = await self._broll_clip(query, assets_dir, idx)
                    if clip:
                        self.emit("progress", f"Cena {idx}: vídeo real ({query[:32]})", progress=58)
                        return {"index": idx, "path": str(clip), "type": "video",
                                "source": "stock_video", "query": query}

                # Fallback: AI image via the provider chain (animated with Ken Burns by the editor).
                dst = assets_dir / f"scene_{idx:03d}.jpg"
                prompt = sc.get("visual_prompt") or query or "cinematic abstract atmosphere"
                src = await self._generate_image(self._enhance(prompt), dst, SCENE_W, SCENE_H,
                                                 label=sc.get("narration", ""))
                self.emit("progress", f"Cena {idx}: imagem IA ({src})", progress=58)
                return {"index": idx, "path": str(dst), "type": "image", "source": src}

        # Preserve `index` fallback behaviour of the old sequential loop (defaults
        # to positional order) before dispatching concurrently.
        for i, sc in enumerate(scenes):
            sc.setdefault("index", i)

        results = await asyncio.gather(*(_one_scene(sc) for sc in scenes))
        scene_assets.extend(sorted(results, key=lambda a: a["index"]))

        # Thumbnails.
        self.emit("progress", "Gerando thumbnails A/B", progress=64)
        title = script.get("title", "Sem título")
        thumbs = await self._thumbnails(title, script, assets_dir)

        visuals = {"scene_assets": scene_assets, "thumbnails": thumbs, "assets_dir": str(assets_dir)}
        self.ctx_set("visuals", visuals)
        self.emit("progress", f"Visuais prontos: {len(scene_assets)} cenas + thumbs", progress=66)
        return visuals

    @staticmethod
    def _enhance(prompt: str) -> str:
        return f"{prompt}, cinematic lighting, photorealistic, dramatic atmosphere, high detail, 4k"

    # ---- Provider chain: Pollinations(token) -> HF FLUX -> Pexels/Pixabay photo -> placeholder ----
    async def _generate_image(self, prompt: str, dst: Path, w: int, h: int, label: str = "") -> str:
        providers = []
        if settings.pollinations_token:
            providers.append(("pollinations", self._p_pollinations))
        if settings.huggingface_token:
            providers.append(("huggingface", self._p_huggingface))
        if settings.pexels_api_key:
            providers.append(("pexels", self._p_pexels_photo))
        if settings.pixabay_api_key:
            providers.append(("pixabay", self._p_pixabay_photo))

        for name, fn in providers:
            try:
                if await fn(prompt, dst, w, h):
                    self._normalize(dst, w, h)
                    return name
            except Exception as exc:  # noqa: BLE001
                self.emit("progress", f"Provedor {name} falhou: {exc}", progress=58)
        # No key configured or all failed -> deterministic local placeholder.
        self._placeholder(dst, w, h, label or prompt[:40])
        return "placeholder"

    async def _p_pollinations(self, prompt, dst: Path, w, h) -> bool:
        seed = int(hashlib.md5(prompt.encode()).hexdigest()[:6], 16)
        url = POLLINATIONS.format(prompt=urllib.parse.quote(prompt))
        params = {"width": w, "height": h, "model": "flux", "nologo": "true",
                  "seed": seed, "token": settings.pollinations_token}
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:
            r = await client.get(url, params=params,
                                 headers={"Authorization": f"Bearer {settings.pollinations_token}"})
            r.raise_for_status()
            if r.headers.get("content-type", "").startswith("image") and len(r.content) > 1000:
                dst.write_bytes(r.content)
                return True
        return False

    async def _p_huggingface(self, prompt, dst: Path, w, h) -> bool:
        url = "https://router.huggingface.co/hf-inference/models/black-forest-labs/FLUX.1-schnell"
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(
                url,
                headers={"Authorization": f"Bearer {settings.huggingface_token}"},
                json={"inputs": prompt},
            )
            if r.status_code == 200 and r.headers.get("content-type", "").startswith("image"):
                dst.write_bytes(r.content)
                return True
        return False

    async def _p_pexels_photo(self, prompt, dst: Path, w, h) -> bool:
        query = " ".join(prompt.split(",")[0].split()[:6])  # first phrase as keywords
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://api.pexels.com/v1/search",
                                 headers={"Authorization": settings.pexels_api_key},
                                 params={"query": query, "per_page": 1, "orientation": "landscape"})
            r.raise_for_status()
            photos = r.json().get("photos", [])
            if not photos:
                return False
            img = await client.get(photos[0]["src"]["large2x"])
            img.raise_for_status()
            dst.write_bytes(img.content)
            return True

    async def _p_pixabay_photo(self, prompt, dst: Path, w, h) -> bool:
        query = " ".join(prompt.split(",")[0].split()[:6])
        async with httpx.AsyncClient(timeout=30) as client:
            r = await client.get("https://pixabay.com/api/",
                                 params={"key": settings.pixabay_api_key, "q": query,
                                         "image_type": "photo", "per_page": 3, "orientation": "horizontal"})
            r.raise_for_status()
            hits = r.json().get("hits", [])
            if not hits:
                return False
            img = await client.get(hits[0].get("largeImageURL") or hits[0]["webformatURL"])
            img.raise_for_status()
            dst.write_bytes(img.content)
            return True

    @staticmethod
    def _normalize(dst: Path, w: int, h: int) -> None:
        """Re-encode to a clean JPEG of exact target size."""
        img = Image.open(dst).convert("RGB")
        img = VisualsAgent._cover(img, w, h)
        img.save(dst, "JPEG", quality=88)

    # ---- Real motion footage (licensed stock video) ----
    # Generic style tokens stripped when deriving a stock-search query from an
    # AI image prompt (stock engines match concrete nouns, not these adjectives).
    _STYLE_STOP = {
        "cinematic", "photorealistic", "dramatic", "atmosphere", "atmospheric",
        "lighting", "high", "detail", "4k", "8k", "hd", "moody", "epic", "shot",
        "scene", "background", "abstract", "realistic", "render", "rendered",
        "style", "color", "grade", "vibrant", "ultra", "detailed", "footage",
    }

    def _search_terms(self, sc: dict) -> str:
        """Concrete English keywords used to search stock video for a scene."""
        q = (sc.get("visual_query") or "").strip()
        if q:
            return q
        return self._keywords_from_prompt(sc.get("visual_prompt") or "")

    def _keywords_from_prompt(self, prompt: str) -> str:
        def keep(s: str) -> list[str]:
            return [w for w in s.split() if w.lower().strip(".,") not in self._STYLE_STOP]
        first = (prompt or "").split(",")[0]
        words = keep(first)
        if not words:                                  # first phrase was all style words
            words = keep((prompt or "").replace(",", " "))
        if not words:                                  # still nothing -> raw leading words
            words = (prompt or "").replace(",", " ").split()
        return " ".join(words[:6]).strip()

    async def _broll_clip(self, query: str, assets_dir: Path, idx: int) -> Path | None:
        """Try each configured stock-video provider; return a SAVED, VALID .mp4 or None."""
        providers = []
        if settings.pexels_api_key:
            providers.append(self._pexels_video)
        if settings.pixabay_api_key:
            providers.append(self._pixabay_video)
        for fn in providers:
            try:
                p = await fn(query, assets_dir, idx)
                if p and p.exists() and p.stat().st_size > 50_000 and self._valid_video(p):
                    return p
                if p and p.exists():
                    p.unlink(missing_ok=True)          # truncated/corrupt -> don't feed ffmpeg
            except Exception as exc:  # noqa: BLE001
                self.emit("progress", f"B-roll {fn.__name__} falhou: {exc}", progress=58)
        return None

    @staticmethod
    def _valid_video(path: Path) -> bool:
        """ffprobe gate: a real video stream with positive duration (rejects truncated downloads)."""
        try:
            out = subprocess.run(
                ["ffprobe", "-v", "error", "-select_streams", "v:0",
                 "-show_entries", "stream=codec_name:format=duration",
                 "-of", "default=nw=1:nk=1", str(path)],
                capture_output=True, text=True, timeout=25,
            )
            has_codec = False
            dur = 0.0
            for tok in (out.stdout or "").split():
                try:
                    dur = max(dur, float(tok))
                except ValueError:
                    has_codec = True
            return out.returncode == 0 and has_codec and dur > 0.3
        except Exception:
            return False

    @staticmethod
    def _pick_video_file(files: list[dict], url_key: str = "link") -> str | None:
        """Largest clip whose width <= broll_max_width (else smallest). None/missing width -> 0."""
        cap = settings.broll_max_width
        width = lambda f: f.get("width") or 0  # noqa: E731  (treats None and absent alike)
        usable = [f for f in files if f.get(url_key)]
        if not usable:
            return None
        under = [f for f in usable if width(f) <= cap]
        chosen = max(under, key=width) if under else min(usable, key=width)
        return chosen.get(url_key)

    async def _download(self, client: httpx.AsyncClient, link: str, dst: Path) -> Path | None:
        async with client.stream("GET", link) as resp:
            resp.raise_for_status()
            with open(dst, "wb") as f:
                async for chunk in resp.aiter_bytes():
                    f.write(chunk)
        return dst

    async def _pexels_video(self, query: str, assets_dir: Path, idx: int) -> Path | None:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            r = await client.get(
                PEXELS_VIDEO,
                headers={"Authorization": settings.pexels_api_key},
                params={"query": query, "per_page": 5, "orientation": "landscape", "min_width": PEXELS_MIN_WIDTH},
            )
            r.raise_for_status()
            vids = r.json().get("videos", [])
            if not vids:
                return None
            link = self._pick_video_file(vids[0].get("video_files", []), "link")
            if not link:
                return None
            return await self._download(client, link, assets_dir / f"scene_{idx:03d}.mp4")

    async def _pixabay_video(self, query: str, assets_dir: Path, idx: int) -> Path | None:
        async with httpx.AsyncClient(timeout=45, follow_redirects=True) as client:
            r = await client.get(
                "https://pixabay.com/api/videos/",
                params={"key": settings.pixabay_api_key, "q": query, "per_page": 5},
            )
            r.raise_for_status()
            hits = r.json().get("hits", [])
            if not hits:
                return None
            # Pixabay nests sizes: hits[i]["videos"] = {large,medium,small,tiny:{url,width}}
            sizes = list((hits[0].get("videos") or {}).values())
            link = self._pick_video_file(sizes, "url")
            if not link:
                return None
            return await self._download(client, link, assets_dir / f"scene_{idx:03d}.mp4")

    # ---- Thumbnails ----
    async def _thumbnails(self, title: str, script: dict, assets_dir: Path) -> dict:
        base = assets_dir / "thumb_base.jpg"
        # Use the packaging strategist's hero-shot concept + punchy 2-4 word overlay
        # (packaging.thumbnail) instead of a generic poster with the whole title
        # stamped on top. Falls back to title-derived values when packaging is absent.
        pkg = (script.get("packaging") or {}).get("thumbnail") or {}
        visual = (pkg.get("visual") or "").strip()
        img_prompt = self._enhance(visual or f"{title}, bold poster, high contrast, eye-catching")
        overlay = (pkg.get("text") or "").strip() or " ".join(title.split()[:3])
        overlay = overlay.upper()[:24]
        # label="" so a placeholder fallback stays clean (text is drawn on top).
        await self._generate_image(img_prompt, base, 1792, 1024, label="")
        out = {}
        # Variant A: red/yellow bottom text.  Variant B: top, white-on-dark.
        for variant, cfg in {
            "A": {"pos": "bottom", "fill": (255, 221, 0), "stroke": (200, 0, 0)},
            "B": {"pos": "top", "fill": (255, 255, 255), "stroke": (10, 10, 10)},
        }.items():
            land = assets_dir / f"thumb_{variant}.png"
            vert = assets_dir / f"thumb_{variant}_vertical.png"
            self._compose_thumb(base, land, overlay, 1792, 1024, cfg)
            self._compose_thumb(base, vert, overlay, 1080, 1920, cfg)
            out[variant] = {"landscape": str(land), "vertical": str(vert)}
        return out

    def _compose_thumb(self, base: Path, dst: Path, text: str, w: int, h: int, cfg: dict) -> None:
        try:
            img = Image.open(base).convert("RGB")
        except Exception:
            img = Image.new("RGB", (w, h), (12, 12, 18))
        # Cover-crop to target aspect.
        img = self._cover(img, w, h)
        draw = ImageDraw.Draw(img)
        # Big, punchy overlay (2-4 words) — readable at feed thumbnail size, not the
        # tiny whole-title band the old code stamped.
        font = self._font(int(h * 0.14))
        text = (text or "").upper()[:24]
        lines = self._wrap(text, font, draw, int(w * 0.9))[:2]
        line_h = int(h * 0.155)
        total = line_h * len(lines)
        y = int(h * 0.06) if cfg["pos"] == "top" else h - total - int(h * 0.08)
        # Semi-transparent scrim behind the text so it reads over any image.
        try:
            band_top = max(0, y - int(h * 0.03))
            scrim = Image.new("RGBA", (w, total + int(h * 0.06)), (0, 0, 0, 130))
            img.paste(scrim, (0, band_top), scrim)
        except Exception:
            pass
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            x = (w - tw) / 2
            draw.text((x, y), ln, font=font, fill=cfg["fill"],
                      stroke_width=max(4, w // 220), stroke_fill=cfg["stroke"])
            y += line_h
        img.save(dst, "PNG")

    # ---- Pillow helpers ----
    @staticmethod
    def _cover(img: Image.Image, w: int, h: int) -> Image.Image:
        src_ratio = img.width / img.height
        dst_ratio = w / h
        if src_ratio > dst_ratio:
            nh = h
            nw = int(h * src_ratio)
        else:
            nw = w
            nh = int(w / src_ratio)
        img = img.resize((nw, nh))
        left = (nw - w) // 2
        top = (nh - h) // 2
        return img.crop((left, top, left + w, top + h))

    # Bundled fallback (Bitstream Vera Bold, freely redistributable — see
    # backend/assets/fonts/LICENSE-Bitstream-Vera.txt) so thumbnail text renders
    # at the intended large size on Linux/Railway too. The old lookup-by-bare-name
    # (arialbd.ttf/DejaVuSans-Bold.ttf/...) only resolves on Windows/Mac, where a
    # system font of that name happens to exist; the production container has no
    # fonts installed at all, so every attempt failed and silently fell back to
    # Pillow's fixed ~10px bitmap default — the "big legible thumbnail text" the
    # rest of this file computes a font size for was actually shipping tiny.
    _BUNDLED_FONT = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "VeraBd.ttf"

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont:
        for name in ("arialbd.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf",
                     str(VisualsAgent._BUNDLED_FONT)):
            try:
                return ImageFont.truetype(name, size)
            except Exception:
                continue
        return ImageFont.load_default()

    @staticmethod
    def _wrap(text: str, font, draw, max_w: int) -> list[str]:
        words = text.split()
        lines, cur = [], ""
        for wd in words:
            trial = f"{cur} {wd}".strip()
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                if cur:
                    lines.append(cur)
                cur = wd
        if cur:
            lines.append(cur)
        return lines[:3]

    @staticmethod
    def _placeholder(dst: Path, w: int, h: int, label: str) -> None:
        """
        Deterministic abstract gradient used when no image provider is configured.
        Intentionally text-free so it never collides with burned captions; the
        per-scene `source: "placeholder"` flag already records that it's a fallback.
        """
        seed = int(hashlib.md5((label or "x").encode()).hexdigest()[:6], 16)
        c1 = (30 + ((seed >> 16) & 0x5F), 24 + ((seed >> 8) & 0x4F), 40 + (seed & 0x6F))
        c2 = (min(255, c1[0] + 70), min(255, c1[1] + 45), min(255, c1[2] + 90))
        base = Image.new("RGB", (w, h), c1)
        top = Image.new("RGB", (w, h), c2)
        mask = Image.linear_gradient("L").rotate(20, expand=True).resize((w, h))
        img = Image.composite(top, base, mask).filter(ImageFilter.GaussianBlur(3))
        # subtle vignette for a more cinematic neutral background
        vig = Image.new("L", (w, h), 0)
        ImageDraw.Draw(vig).ellipse([-w // 4, -h // 4, w + w // 4, h + h // 4], fill=120)
        vig = vig.filter(ImageFilter.GaussianBlur(w // 8))
        dark = Image.new("RGB", (w, h), (0, 0, 0))
        img = Image.composite(img, dark, vig)
        img.save(dst, "JPEG", quality=85)


# --- standalone test: python -m backend.agents.visuals --test ---
if __name__ == "__main__":
    async def _demo():
        script = {
            "title": "O farol abandonado",
            "content_type": "film_recap_ai_images",
            "scenes": [
                {"index": 0, "narration": "A névoa cobria o farol.",
                 "visual_prompt": "abandoned lighthouse in fog at dusk, eerie", "is_highlight": True},
                {"index": 1, "narration": "Algo se movia lá dentro.",
                 "visual_prompt": "dark interior of lighthouse, single candle, shadows", "is_highlight": False},
            ],
        }
        agent = VisualsAgent(job_id=0, emit=False)
        result = await agent.execute(script=script)
        print("scene assets:", len(result["scene_assets"]))
        for a in result["scene_assets"]:
            print(" ", a["index"], a["source"], a["path"])
        print("thumbnails:", list(result["thumbnails"].keys()))

    asyncio.run(_demo())
