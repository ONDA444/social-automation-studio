"""
SEOAgent — platform-specific metadata (YouTube / TikTok / Instagram).

LLM-generated when a key is present; deterministic offline fallback otherwise.
YouTube description gets auto chapters built from narration scene markers.
"""
from __future__ import annotations

import asyncio

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend import llm

SYSTEM = "Você é um especialista em SEO e growth para YouTube, TikTok e Instagram. Responda só com JSON."

YT_CATEGORY = {
    "film_recap_ai_images": "24",   # Entertainment
    "sports_highlights": "17",      # Sports
    "quote_viral": "22",            # People & Blogs
}


class SEOAgent(BaseAgent):
    name = "seo_agent"

    async def run(
        self,
        script: dict | None = None,
        narration: dict | None = None,
        content_type: str = "film_recap_ai_images",
        language: str | None = None,
        **_,
    ) -> dict:
        script = script or self.ctx_get("script") or {}
        narration = narration or self.ctx_get("narration") or {}
        content_type = script.get("content_type", content_type)
        language = language or settings.default_language

        self.emit("progress", "Gerando SEO por plataforma", progress=84)
        try:
            seo = await self._via_llm(script, content_type, language)
        except llm.LLMUnavailable:
            seo = self._offline(script, content_type)

        # Always attach computed chapters + category, regardless of source.
        seo.setdefault("youtube", {})
        seo["youtube"]["chapters"] = self._chapters(script, narration)
        seo["youtube"]["category_id"] = YT_CATEGORY.get(content_type, "22")
        seo["youtube"]["description"] = self._with_chapters(
            seo["youtube"].get("description", ""), seo["youtube"]["chapters"]
        )
        seo = self._clamp(seo)

        self.ctx_set("seo", seo)
        self.emit("progress", "SEO pronto (YT/TikTok/IG)", progress=86)
        return seo

    async def _via_llm(self, script, content_type, language) -> dict:
        prompt = f"""Crie metadados de SEO em {language} para este vídeo.
Título base: "{script.get('title')}"
Tipo: {content_type}
Palavras-chave: {script.get('seo_keywords', [])}

JSON EXATO:
{{
  "youtube": {{"title": "<=70 chars com keyword no início + 1 emoji", "description": "3 parágrafos", "tags": ["8-18 tags"]}},
  "tiktok": {{"caption": "<=150 chars com 3-5 hashtags de nicho + #fyp #foryou"}},
  "instagram": {{"caption": "storytelling <=2200 chars", "hashtags": ["25-30 hashtags"]}}
}}"""
        return await llm.complete_json(prompt, system=SYSTEM, max_tokens=1500)

    def _offline(self, script: dict, content_type: str) -> dict:
        title = script.get("title", "Vídeo")
        kws = script.get("seo_keywords", []) or [title.lower()]
        tags = list(dict.fromkeys(kws + ["viral", "shorts", "brasil", content_type.split("_")[0]]))[:15]
        nicho_tags = " ".join(f"#{k.replace(' ', '')}" for k in kws[:4])
        return {
            "youtube": {
                "title": f"{title} 🔥"[:70],
                "description": (
                    f"{title}\n\n"
                    f"Neste vídeo exploramos {title.lower()} em detalhes. "
                    "Conteúdo 100% original, narração por IA e imagens autorais.\n\n"
                    "👉 Inscreva-se para mais. Ative o sininho! 🔔"
                ),
                "tags": tags,
            },
            "tiktok": {"caption": f"{title} {nicho_tags} #fyp #foryou"[:150]},
            "instagram": {
                "caption": f"{title}\n\nUma história que vale a pena assistir até o final. "
                           "Salve e compartilhe! 💬",
                "hashtags": [f"#{k.replace(' ', '')}" for k in kws[:6]]
                            + ["#viral", "#reels", "#fyp", "#brasil", "#explore", "#trending",
                               "#video", "#conteudo", "#shorts", "#instadaily"],
            },
        }

    @staticmethod
    def _chapters(script: dict, narration: dict) -> list[dict]:
        """Build YouTube chapters from scene starts (needs >0:00 first marker)."""
        words = narration.get("words") or []
        scenes = script.get("scenes", [])
        if not words or not scenes:
            return []
        chapters = [{"time": 0.0, "title": "Início"}]
        cursor = 0
        for sc in scenes:
            n = len((sc.get("narration") or "").split())
            if n and cursor < len(words):
                t = words[cursor]["start"]
                label = (sc.get("narration") or "").split(".")[0][:40] or f"Parte {sc.get('index', 0) + 1}"
                if t > 5:  # YouTube needs distinct timestamps; skip near-zero
                    chapters.append({"time": round(t, 1), "title": label})
            cursor += n
        # YouTube requires >= 3 chapters and first at 0:00.
        return chapters if len(chapters) >= 3 else []

    @staticmethod
    def _with_chapters(description: str, chapters: list[dict]) -> str:
        if not chapters:
            return description
        lines = ["", "⏱️ Capítulos:"]
        for ch in chapters:
            m, s = divmod(int(ch["time"]), 60)
            lines.append(f"{m}:{s:02d} {ch['title']}")
        return description + "\n" + "\n".join(lines)

    @staticmethod
    def _clamp(seo: dict) -> dict:
        yt = seo.setdefault("youtube", {})
        yt["title"] = (yt.get("title") or "Vídeo")[:100]
        yt["tags"] = (yt.get("tags") or [])[:30]
        tk = seo.setdefault("tiktok", {})
        tk["caption"] = (tk.get("caption") or "")[:150]
        ig = seo.setdefault("instagram", {})
        ig["caption"] = (ig.get("caption") or "")[:2200]
        ig["hashtags"] = (ig.get("hashtags") or [])[:30]
        return seo


# --- standalone test ---
if __name__ == "__main__":
    import json

    async def _demo():
        script = {"title": "O farol abandonado", "content_type": "film_recap_ai_images",
                  "seo_keywords": ["farol", "mistério", "história"],
                  "scenes": [{"index": 0, "narration": "A névoa cobria o farol."},
                             {"index": 1, "narration": "Algo se movia lá dentro."},
                             {"index": 2, "narration": "O segredo veio à tona."}]}
        narration = {"words": [{"word": "w", "start": i * 1.0, "end": i * 1.0 + 0.5} for i in range(12)]}
        agent = SEOAgent(job_id=0, emit=False)
        print(json.dumps(await agent.execute(script=script, narration=narration), ensure_ascii=False, indent=2))

    asyncio.run(_demo())
