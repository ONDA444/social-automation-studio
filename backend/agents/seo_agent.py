"""
SEOAgent — platform-specific metadata (YouTube / TikTok / Instagram).

LLM-generated when a key is present; deterministic offline fallback otherwise.
YouTube description gets auto chapters built from narration scene markers.
"""
from __future__ import annotations

import asyncio
import logging
import re

from backend.agents.base_agent import BaseAgent
from backend.agents.keyword_research import region_for_language, related_search_queries
from backend.config import settings
from backend import llm
from backend import runtime_settings
from backend.uploaders.youtube import cap_tags_to_budget

logger = logging.getLogger("studio.seo")

SYSTEM = (
    "Você é um especialista de SEO e Algoritmo de YouTube/TikTok/Instagram de nível mundial. "
    "Você entende que existem TRÊS motores — Busca (keyword + CTR-na-query), "
    "Browse/Suggested (CTR do par título+thumbnail × watch time) e FYP do TikTok/IG "
    "(completion_rate × rewatch × save/share ratio × comment_velocity) — e otimiza para os "
    "três sem sacrificar um pelo outro. NUNCA invente números/nomes/datas. "
    "Responda SOMENTE com JSON válido."
)

# YouTube API hard limit (snippet.description). Nothing downstream (chapters,
# hashtags, the monetization CTA, localizations) checks this, so a long
# LLM-generated description + auto chapters + CTA can silently exceed it and
# make the whole upload fail — clamp everywhere the description grows.
YT_DESCRIPTION_LIMIT = 5000

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
    "music": "10",                  # Music (internal-only marker, see scheduler.py)
}


async def apply_runtime_youtube_enrichment(seo: dict) -> dict:
    """Apply runtime YouTube settings to an existing SEO package."""
    seo.setdefault("youtube", {})
    yt = seo["youtube"]

    cta = runtime_settings.effective_cta()
    if cta:
        desc = yt.get("description") or ""
        if not desc.strip().startswith(cta.strip()):
            yt["description"] = cta + "\n\n" + desc
    yt["description"] = (yt.get("description") or "")[:YT_DESCRIPTION_LIMIT]

    await localize_youtube_metadata(seo)
    return seo


def apply_runtime_youtube_enrichment_sync(seo: dict) -> dict:
    """Synchronous wrapper for scheduler paths."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(apply_runtime_youtube_enrichment(seo))
    if loop.is_running():
        logger.debug("runtime SEO localization skipped inside running event loop")
        cta = runtime_settings.effective_cta()
        if cta:
            seo.setdefault("youtube", {})
            desc = seo["youtube"].get("description") or ""
            if not desc.strip().startswith(cta.strip()):
                seo["youtube"]["description"] = cta + "\n\n" + desc
        if "youtube" in seo:
            seo["youtube"]["description"] = (seo["youtube"].get("description") or "")[:YT_DESCRIPTION_LIMIT]
        return seo
    return loop.run_until_complete(apply_runtime_youtube_enrichment(seo))


async def localize_youtube_metadata(seo: dict) -> None:
    """Translate title/description into configured YouTube localizations."""
    langs = runtime_settings.effective_localize_langs()
    yt = seo.get("youtube", {})
    title = yt.get("title", "")
    if not langs or not title:
        return
    try:
        prompt = (
            f"Traduza o TITULO e a DESCRICAO de um video do YouTube para estes idiomas "
            f"(codigos ISO): {', '.join(langs)}. Preserve o apelo de clique; NAO traduza "
            f"nomes proprios, hashtags nem URLs. Responda SOMENTE JSON no formato "
            f'{{"<lang>": {{"title": "...", "description": "..."}}}}.\n\n'
            f"TITULO: {title}\n\nDESCRICAO:\n{(yt.get('description') or '')[:1500]}"
        )
        out = await llm.complete_json(
            prompt,
            system="Tradutor profissional. So JSON valido.",
            max_tokens=1500,
        )
        loc = {}
        for lang in langs:
            v = out.get(lang) if isinstance(out, dict) else None
            if isinstance(v, dict) and v.get("title"):
                loc[lang] = {"title": v.get("title", ""), "description": v.get("description", "")}
        if loc:
            seo["youtube"]["localizations"] = loc
    except Exception as exc:  # noqa: BLE001
        logger.debug("localize failed: %s", exc)


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

        # Real search-behavior grounding: today every keyword/tag is pure LLM
        # invention. Pull actual Google Trends "top"/"rising" queries related to
        # this video's own title (not the channel's broad niche) so the SEO
        # prompt — and the tag list, as a safety net below — are anchored to
        # what people really search, not just what sounds plausible. Best-effort
        # (empty list on any failure); off the event loop since pytrends is a
        # blocking network call.
        lang_code = language or "pt-BR"
        # Google Trends' related_queries() only returns data when the seed itself
        # has measurable search volume. A full LLM-generated clickbait title (8-10
        # words, very specific) almost never has that volume, so passing the title
        # made this grounding silently return empty nearly every time. A short seed
        # keyword (same shape ready_video_seo.py already uses as "primary") has a
        # real chance of matching an actual search term.
        seed_keywords = script.get("seo_keywords") or []
        trends_seed = seed_keywords[0] if seed_keywords else script.get("title", "")
        real_queries = await asyncio.to_thread(
            related_search_queries, trends_seed, lang_code, region_for_language(lang_code)
        )

        self.emit("progress", "Gerando SEO por plataforma", progress=84)
        try:
            seo = await self._via_llm(script, content_type, language, real_queries)
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
        # Monetization CTA (affiliate / digital product / newsletter) goes in the FIRST
        # lines — above the "Show more" fold converts 5-10x. This is the only revenue that
        # does NOT need the channel to be in the YPP. Empty by default (no-op); the user
        # sets it in Settings → Monetização (dashboard) or MONETIZATION_CTA on Railway.
        # Backfill YouTube tags. Weak free models routinely fill title/description but
        # DROP the "tags" field — the video then publishes with ZERO tags (lost search +
        # suggested signal; the "Tags 0/500" the user saw). Top up from seo_keywords +
        # title words so a video is NEVER shipped tagless; the LLM's own tags stay first
        # (its #1 tag is the exact keyword).
        existing = [t for t in (seo["youtube"].get("tags") or [])
                    if isinstance(t, str) and t.strip()]
        if len(existing) < 8:
            seo["youtube"]["tags"] = self._merge_tags(existing, script, content_type)
        # Guarantee at least a few REAL search queries land in the published
        # tags even if the LLM ignored the grounding block in the prompt above
        # — same "top up, LLM's own picks stay first" pattern as _merge_tags.
        if real_queries:
            current = seo["youtube"].get("tags") or []
            lowered = {t.lower() for t in current if isinstance(t, str)}
            for q in real_queries:
                if len(current) >= 20:
                    break
                if q.lower() not in lowered:
                    current.append(q)
                    lowered.add(q.lower())
            seo["youtube"]["tags"] = current

        # Pin the high-CTR title from Packaging. The SEO LLM is told NOT to regenerate
        # the title, but weak free models sometimes rewrite it anyway and the drifted
        # title would publish. Force the Packaging recommendation back when present.
        rec = self._recommended_title()
        if rec:
            seo["youtube"]["title"] = rec

        # YouTube shows the first 3 hashtags ABOVE the title — a free discovery surface
        # the description never used (audit: hashtags never reached YouTube). Append 2-3
        # deterministic hashtags from seo_keywords; #Shorts goes FIRST for vertical/short
        # format — the signal native Shorts were missing. Cap at 3 (>15 = all ignored).
        is_short = (script.get("format") or self.ctx_get("format")) == "short"
        hashtags = self._yt_hashtags(script.get("seo_keywords") or [], is_short)
        if hashtags:
            desc = (seo["youtube"].get("description") or "").rstrip()
            desc_lower = desc.lower()
            new_hashtags = [h for h in hashtags if h.lower() not in desc_lower]
            if new_hashtags:
                seo["youtube"]["description"] = (desc + "\n\n" + " ".join(new_hashtags)).strip()

        seo = self._clamp(seo)
        await apply_runtime_youtube_enrichment(seo)

        self.ctx_set("seo", seo)
        self.emit("progress", "SEO pronto (YT/TikTok/IG)", progress=86)
        return seo

    async def _via_llm(self, script, content_type, language, real_queries: list[str] | None = None) -> dict:
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

        trends_block = ""
        if real_queries:
            trends_block = (
                "\n\nBuscas reais relacionadas (Google Trends, o que pessoas de verdade "
                f"digitam sobre esse tema — priorize estas sobre termos inventados quando "
                f"fizerem sentido para search_seed/long_tail_variants/tags): {real_queries}"
            )

        prompt = f"""Crie o pacote de SEO/Algoritmo em {language} para este vídeo.
Título recomendado (Packaging — NÃO regere): "{recommended_title}"
Tipo: {content_type}
Keywords semente: {keywords}
Plataformas: {target_platforms}
Fatos verificados: {facts_snippet or 'N/A'}{perf_block}{fyp_block}{trends_block}

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
                     "suggested_next_to": [content_type],
                     "playlist_target": content_type.replace("_", " ").title()},
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

    def _recommended_title(self) -> str | None:
        """The high-CTR title chosen by the PackagingStrategist, if any. Mirrors the
        contract _via_llm reads (packaging.youtube_titles + recommended_index)."""
        packaging = self.ctx_get("packaging") or {}
        titles = packaging.get("youtube_titles")
        idx = packaging.get("recommended_index")
        if isinstance(titles, list) and isinstance(idx, int) and 0 <= idx < len(titles):
            t = titles[idx]
            rec = t.get("text", t) if isinstance(t, dict) else t
            rec = (rec or "").strip()
            return rec or None
        return None

    @staticmethod
    def _yt_hashtags(keywords: list[str], is_short: bool) -> list[str]:
        """Up to 3 clickable hashtags for the YouTube description (the first 3 render
        above the title). #Shorts leads for vertical/short format."""
        out: list[str] = []
        if is_short:
            out.append("#Shorts")
        for k in keywords:
            if not isinstance(k, str):
                continue
            h = "#" + re.sub(r"[^0-9A-Za-zÀ-ÿ]", "", k)
            if len(h) > 1 and h.lower() not in (t.lower() for t in out):
                out.append(h)
            if len(out) >= 3:
                break
        return out[:3]

    @staticmethod
    def _merge_tags(existing: list[str], script: dict, content_type: str) -> list[str]:
        """Never let a video ship tagless. Keep the LLM's tags first (its #1 tag is the
        exact keyword), then top up from seo_keywords, the full title (one long-tail
        tag), salient title words, and a few evergreen tags until there's healthy
        search/suggested coverage. Case-insensitive dedup; drops over-long tags."""
        title = (script.get("title") or "").strip()
        kws = [k for k in (script.get("seo_keywords") or []) if isinstance(k, str) and k.strip()]
        title_words = re.findall(r"[A-Za-zÀ-ÿ0-9]{4,}", title)
        generic = ["viral", "shorts", "brasil", content_type.split("_")[0]]
        out: list[str] = []
        seen: set[str] = set()
        for t in [*existing, *kws, title, *title_words, *generic]:
            t = (t or "").strip()
            key = t.lower()
            if t and key not in seen and len(t) <= 60:
                out.append(t)
                seen.add(key)
            if len(out) >= 15:
                break
        return out

    @staticmethod
    def _chapters(script: dict, narration: dict) -> list[dict]:
        """Build YouTube chapters from scene starts (needs >0:00 first marker)."""
        words = narration.get("words") or []
        scenes = script.get("scenes", [])
        if not words or not scenes:
            return []
        from backend.agents.scriptwriter import clean_markers

        chapters = [{"time": 0.0, "title": "Início"}]
        cursor = 0
        for sc in scenes:
            # Clean the inline markers ([PAUSA], [ENFASE]{...}, etc.) BEFORE counting
            # words or using the narration as a chapter title — otherwise the markers
            # leak verbatim into the published YouTube description AND inflate the
            # word count, desyncing `cursor` from the actual `words` timestamps
            # (which are built from the already-cleaned TTS text).
            clean = clean_markers(sc.get("narration") or "")
            n = len(clean.split())
            if n and cursor < len(words):
                t = words[cursor]["start"]
                label = clean.split(".")[0][:40].strip() or f"Parte {sc.get('index', 0) + 1}"
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
        def _fmt(seconds: int) -> str:
            # YouTube requires H:MM:SS (not raw M:SS) once a video passes 60 minutes,
            # otherwise chapters past the 1-hour mark aren't recognized at all.
            h, rem = divmod(seconds, 3600)
            m, s = divmod(rem, 60)
            return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"

        block = "⏱️ Capítulos:\n" + "\n".join(
            f"{_fmt(int(ch['time']))} {ch['title']}"
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
        yt["description"] = (yt.get("description") or "")[:YT_DESCRIPTION_LIMIT]
        yt["tags"] = cap_tags_to_budget(yt.get("tags") or [])
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
