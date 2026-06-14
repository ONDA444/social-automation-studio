"""
VisualsAgent — generate/obtain every visual asset.

  MODE 1 (film_recap_ai_images): Pollinations FLUX image per [CENA_IA] scene.
  MODE 2 (sports_highlights):     Pexels stock video per [LANCE]; Pollinations fallback.
  MODE 3 (quote_viral):           one dark background clip/image (blurred later).
  THUMBNAIL (all):                Pollinations base + Pillow text, A/B variants,
                                  landscape (1792x1024) + vertical (1080x1920).

Pollinations needs no API key. Every network fetch has a deterministic Pillow
placeholder fallback so the pipeline never hard-stops offline.
"""
from __future__ import annotations

import asyncio
import hashlib
import urllib.parse
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

from backend.agents.base_agent import BaseAgent
from backend.config import settings

POLLINATIONS = "https://image.pollinations.ai/prompt/{prompt}"
PEXELS_VIDEO = "https://api.pexels.com/videos/search"

# 16:9 working resolution for image-driven scenes (upscaled by the editor).
SCENE_W, SCENE_H = 1280, 720


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

        self.emit("progress", f"Obtendo {len(scenes)} asset(s) visual(is)", progress=58)
        for sc in scenes:
            idx = sc.get("index", len(scene_assets))
            dst = assets_dir / f"scene_{idx:03d}.jpg"
            if content_type == "sports_highlights" and settings.pexels_api_key and sc.get("visual_query"):
                ok = await self._pexels_video(sc["visual_query"], assets_dir, idx)
                if ok:
                    scene_assets.append({"index": idx, "path": str(ok), "type": "video", "source": "pexels"})
                    continue
            # Default: AI image via the configured provider chain.
            prompt = sc.get("visual_prompt") or sc.get("visual_query") or "cinematic abstract atmosphere"
            src = await self._generate_image(self._enhance(prompt), dst, SCENE_W, SCENE_H,
                                             label=sc.get("narration", ""))
            scene_assets.append({"index": idx, "path": str(dst), "type": "image", "source": src})
            self.emit("progress", f"Asset cena {idx} pronto ({src})", progress=58)

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

    # ---- Pexels stock video ----
    async def _pexels_video(self, query: str, assets_dir: Path, idx: int) -> Path | None:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.get(
                    PEXELS_VIDEO,
                    headers={"Authorization": settings.pexels_api_key},
                    params={"query": query, "per_page": 3, "min_width": 1080},
                )
                r.raise_for_status()
                vids = r.json().get("videos", [])
                if not vids:
                    return None
                files = sorted(vids[0]["video_files"], key=lambda f: f.get("width", 0), reverse=True)
                link = files[0]["link"]
                dst = assets_dir / f"scene_{idx:03d}.mp4"
                async with client.stream("GET", link) as resp:
                    resp.raise_for_status()
                    with open(dst, "wb") as f:
                        async for chunk in resp.aiter_bytes():
                            f.write(chunk)
                return dst
        except Exception:
            return None

    # ---- Thumbnails ----
    async def _thumbnails(self, title: str, script: dict, assets_dir: Path) -> dict:
        base = assets_dir / "thumb_base.jpg"
        # label="" so a placeholder fallback stays clean (title is drawn on top).
        await self._generate_image(
            self._enhance(f"{title}, bold poster, high contrast, eye-catching"),
            base, 1792, 1024, label="",
        )
        out = {}
        # Variant A: red/yellow bottom title.  Variant B: top, white-on-dark.
        for variant, cfg in {
            "A": {"pos": "bottom", "fill": (255, 221, 0), "stroke": (200, 0, 0)},
            "B": {"pos": "top", "fill": (255, 255, 255), "stroke": (10, 10, 10)},
        }.items():
            land = assets_dir / f"thumb_{variant}.png"
            vert = assets_dir / f"thumb_{variant}_vertical.png"
            self._compose_thumb(base, land, title, 1792, 1024, cfg)
            self._compose_thumb(base, vert, title, 1080, 1920, cfg)
            out[variant] = {"landscape": str(land), "vertical": str(vert)}
        return out

    def _compose_thumb(self, base: Path, dst: Path, title: str, w: int, h: int, cfg: dict) -> None:
        try:
            img = Image.open(base).convert("RGB")
        except Exception:
            img = Image.new("RGB", (w, h), (12, 12, 18))
        # Cover-crop to target aspect.
        img = self._cover(img, w, h)
        draw = ImageDraw.Draw(img)
        font = self._font(int(h * 0.085))
        text = title.upper()[:60]
        lines = self._wrap(text, font, draw, int(w * 0.9))
        line_h = int(h * 0.1)
        total = line_h * len(lines)
        y = int(h * 0.06) if cfg["pos"] == "top" else h - total - int(h * 0.08)
        for ln in lines:
            tw = draw.textlength(ln, font=font)
            x = (w - tw) / 2
            draw.text((x, y), ln, font=font, fill=cfg["fill"],
                      stroke_width=max(3, w // 300), stroke_fill=cfg["stroke"])
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

    @staticmethod
    def _font(size: int) -> ImageFont.FreeTypeFont:
        for name in ("arialbd.ttf", "Arial_Bold.ttf", "DejaVuSans-Bold.ttf", "arial.ttf"):
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
