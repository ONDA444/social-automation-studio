"""
SEOAgent — platform-specific metadata (YouTube / TikTok / Instagram).

LLM-generated when a key is present; deterministic offline fallback otherwise.
YouTube description gets auto chapters built from narration scene markers.
"""
from __future__ import annotations

import asyncio
import re

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend import llm

SYSTEM = (
    "Você é um especialista de SEO e Algoritmo de YouTube/TikTok/Instagram de nível mundial. "
    "Você entende que existem TRÊS motores — Busca (keyword + CTR-na-query), "
    "Browse/Suggested (CTR do par título+thumbnail × watch time) e FYP do TikTok/IG "
    "(completion_rate × rewatch × save/share ratio × comment_velocity) — e otimiza para os "
    "três sem sacrificar um pelo outro. NUNCA invente números/nomes/datas. "
    "Responda SOMENTE com JSON válido."
)

YT_CATEGORY = {
    "film_recap_ai_images": "24",   # Entertainment
    "sports_highlights": "17",      # Sports
    "quote_viral": "22",            # People & Blogs
    "top_list_ranking": "24",       # Entertainment
    "explainer_curiosity": "27",    # Education
    "true_crime_mystery": "24",     # Entertainment
    "reaction_commentary": "24",    # Entertainment
    "reddit_story": "24",           # Entertainment
    "motivational_speech": "22",    # People & Blogs
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

        # Normalise: ensure top-level "youtube" key (LLM may nest it under "feed" etc.)
        if "youtube" not in seo and isinstance(seo.get("feed"), dict):
            seo["youtube"] = {}
        seo.setdefault("youtube", {})
        # Always attach computed chapters + category, regardless of source.
        seo["youtube"]["chapters"] = self._chapters(script, narration)
        seo["youtube"].setdefault("category_id", YT_CATEGORY.get(content_type, "22"))
        seo["youtube"]["description"] = self._with_chapters(
            seo["youtube"].get("description", ""), seo["youtube"]["chapters"]
        )
        seo = self._clamp(seo)

        self.ctx_set("seo", seo)
        self.emit("progress", "SEO pronto (YT/TikTok/IG)", progress=86)
        return seo

    async def _via_llm(self, script, content_type, language) -> dict:
        title = script.get("title", "")
        keywords = script.get("seo_keywords", [])
        packaging = self.ctx_get("packaging") or {}
        recommended_title = title
        if isinstance(packaging.get("youtube_titles"), list) and packaging.get("recommended_index") is not None:
            idx = packaging["recommended_index"]
            titles = packaging["youtube_titles"]
            if isinstance(titles, list) and 0 <= idx < len(titles):
                t = titles[idx]
                recommended_title = t.get("text", t) if isinstance(t, dict) else t
        target_platforms = self.ctx_get("target_platforms") or ["youtube"]
        research = self.ctx_get("research") or {}
        facts_snippet = (research.get("facts") or "")[:400]
        # Live learning signal — titles/tags/types that performed on this channel.
        perf_block = self.ctx_get("performance_insights") or ""

        fyp_block = ""
        if any(p in target_platforms for p in ("tiktok", "instagram")):
            fyp_block = (
                "\n\nFYP (TikTok/IG): para cada plataforma não-YouTube, declare como o vídeo ataca "
                "completion_rate (gancho ≤2s + loop), rewatch (loop_seam), save (utilidade), "
                "share (identidade/moeda social) e comment_velocity (pergunta divisiva). "
                "Proponha first_comment (comentário-semente fixado que gera debate)."
            )

        prompt = f"""Crie o pacote de SEO/Algoritmo em {language} para este vídeo.
Título recomendado (Packaging — NÃO regere): "{recommended_title}"
Tipo: {content_type}
Keywords semente: {keywords}
Plataformas: {target_platforms}
Fatos verificados: {facts_snippet or 'N/A'}{perf_block}{fyp_block}

JSON EXATO (preencha todos os campos, não omita plataformas):
{{
  "search": {{
    "search_seed": "<termo que um humano DIGITARIA>",
    "long_tail_variants": ["<pergunta>", "<sinônimo>", "<grafia alternativa>"],
    "title_keyword": "<keyword principal no título>"
  }},
  "feed": {{
    "entities": ["<5-10 nomes/lugares/obras/eventos reais>"],
    "cluster_terms": ["<3-6 termos do nicho p/ suggested>"],
    "suggested_next_to": ["<tipo de vídeo ao lado do qual este deveria aparecer>"],
    "playlist_target": "<série/playlist do canal>"
  }},
  "fyp": {{
    "tiktok": {{
      "completion_play": "...", "rewatch_play": "...", "save_play": "...",
      "share_play": "...", "comment_play": "...", "first_comment": "..."
    }},
    "instagram": {{
      "completion_play": "...", "rewatch_play": "...", "save_play": "...",
      "share_play": "...", "comment_play": "...", "first_comment": "..."
    }}
  }},
  "youtube": {{
    "title": "{recommended_title}",
    "description": "<gancho 1-2 linhas com keyword → contexto+entidades → [CAPÍTULOS] → CTA>",
    "tags": ["<12-20 tags, 1ª = keyword exata>"],
    "category_id": "{YT_CATEGORY.get(content_type, '22')}",
    "thumbnail_text": "<2-4 PALAVRAS MAIÚSCULAS que complementam o título>"
  }},
  "tiktok": {{"caption": "<=150 chars com 3-5 hashtags de nicho + #fyp #foryou"}},
  "instagram": {{"caption": "storytelling <=2200 chars", "hashtags": ["20-30 mix nicho+alcance"]}},
  "seo_score": {{
    "value": 0,
    "breakdown": {{
      "title_ctr": 0, "keyword_match": 0, "entity_coverage": 0,
      "description_structure": 0, "tags_quality": 0, "feed_signals": 0,
      "fyp_signals": 0, "truth_safety": 0
    }},
    "verdict": "ok"
  }},
  "seo_notes": ["<o que falta para melhorar o score>"]
}}"""
        from backend.agents.style_guide import with_style
        return await llm.complete_json(prompt, system=with_style(SYSTEM), max_tokens=2000)

    def _offline(self, script: dict, content_type: str) -> dict:
        title = script.get("title", "Vídeo")
        kws = script.get("seo_keywords", []) or [title.lower()]
        tags = list(dict.fromkeys(kws + ["viral", "shorts", "brasil", content_type.split("_")[0]]))[:15]
        nicho_tags = " ".join(f"#{k.replace(' ', '')}" for k in kws[:4])
        return {
            "search": {"search_seed": kws[0] if kws else title.lower(),
                       "long_tail_variants": [], "title_keyword": kws[0] if kws else title.lower()},
            "feed": {"entities": kws[:5], "cluster_terms": kws[:3],
                     "suggested_next_to": [content_type], "playlist_target": ""},
            "fyp": {
                "tiktok": {"completion_play": "gancho no 1s", "rewatch_play": "loop fechado",
                           "save_play": "utilidade", "share_play": "identidade",
                           "comment_play": "pergunta divisiva", "first_comment": ""},
                "instagram": {"completion_play": "gancho no 1s", "rewatch_play": "loop fechado",
                              "save_play": "utilidade", "share_play": "identidade",
                              "comment_play": "pergunta divisiva", "first_comment": ""},
            },
            "youtube": {
                "title": f"{title}"[:70],
                "description": (
                    f"{title}\n\n"
                    f"Neste vídeo exploramos {title.lower()} em detalhes. "
                    "Conteúdo 100% original, narração por IA e imagens autorais.\n\n"
                    "👉 Inscreva-se para mais. Ative o sininho! 🔔"
                ),
                "tags": tags,
                "category_id": YT_CATEGORY.get(content_type, "22"),
                "thumbnail_text": " ".join(title.split()[:3]).upper()[:30],
            },
            "tiktok": {"caption": f"{title} {nicho_tags} #fyp #foryou"[:150]},
            "instagram": {
                "caption": f"{title}\n\nUma história que vale a pena assistir até o final. "
                           "Salve e compartilhe! 💬",
                "hashtags": [f"#{k.replace(' ', '')}" for k in kws[:6]]
                            + ["#viral", "#reels", "#fyp", "#brasil", "#explore", "#trending",
                               "#video", "#conteudo", "#shorts", "#instadaily"],
            },
            "seo_score": {"value": 50, "breakdown": {}, "verdict": "ok"},
            "seo_notes": [],
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

    _CHAPTERS_MARKER = re.compile(r"\[CAP[IÍ]TULOS\]")

    @staticmethod
    def _with_chapters(description: str, chapters: list[dict]) -> str:
        desc = description or ""
        if not chapters:
            # No chapters → strip the marker so the literal "[CAPÍTULOS]" never
            # leaks into the published description.
            return SEOAgent._CHAPTERS_MARKER.sub("", desc).strip()
        block = "⏱️ Capítulos:\n" + "\n".join(
            f"{divmod(int(ch['time']), 60)[0]}:{divmod(int(ch['time']), 60)[1]:02d} {ch['title']}"
            for ch in chapters
        )
        # Substitute the [CAPÍTULOS] marker IN PLACE (the LLM places it mid-description
        # for session-time); fall back to appending if the marker is absent.
        if SEOAgent._CHAPTERS_MARKER.search(desc):
            return SEOAgent._CHAPTERS_MARKER.sub(lambda _: block, desc, count=1).strip()
        return (desc + "\n\n" + block).strip()

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
        # Ensure new-schema keys exist at minimum (backward compat)
        seo.setdefault("search", {})
        seo.setdefault("feed", {})
        seo.setdefault("fyp", {"tiktok": {}, "instagram": {}})
        seo.setdefault("seo_score", {"value": 0, "breakdown": {}, "verdict": "ok"})
        seo.setdefault("seo_notes", [])
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
