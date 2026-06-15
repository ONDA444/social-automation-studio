"""
Growth agents — engineer every video to win the algorithm (YouTube/TikTok/IG).

Three specialists run AFTER the script is written and BEFORE narration, so their
rewrites become part of the spoken video and its packaging:

  1. HookOptimizerAgent     — rewrites the first ~3s into a scroll-stopping hook
                              (pattern interrupt / curiosity gap / bold claim).
  2. RetentionEngineerAgent — adds a mid-video re-hook (open loop) and a strong
                              loop-back CTA so viewers watch to the end.
  3. PackagingStrategistAgent — high-CTR titles per platform + a thumbnail concept
                              (big punchy text + emotion), feeding thumbnails/SEO.

Each is LLM-driven (Groq->Gemini) with a deterministic offline fallback, and only
ever edits NARRATION TEXT / metadata — scene count and visual_query are preserved,
so the visuals/editor contract is untouched. Facts come from ctx['research'] so a
punchier hook never becomes a false claim.
"""
from __future__ import annotations

import asyncio

from backend.agents.base_agent import BaseAgent
from backend.agents.style_guide import with_style
from backend import llm

# Purely-creative types have no spoken narration to re-hook (quote_viral) — hook/
# retention skip them; packaging still runs for everyone.
_NO_NARRATION = {"quote_viral"}


def _recompute_narration(script: dict) -> None:
    """Keep narration_text in sync after a scene-narration rewrite."""
    if script.get("content_type") in _NO_NARRATION:
        return
    script["narration_text"] = " ".join(
        s.get("narration", "") for s in script.get("scenes", []) if s.get("narration")
    ).strip()


class HookOptimizerAgent(BaseAgent):
    name = "hook_optimizer"

    async def run(self, script: dict | None = None, **_) -> dict:
        script = script or self.ctx_get("script") or {}
        scenes = script.get("scenes", [])
        if not scenes or script.get("content_type") in _NO_NARRATION:
            return script
        self.emit("progress", "Otimizando gancho (primeiros 3s)", progress=22)

        original = scenes[0].get("narration", "")
        facts = (self.ctx_get("research") or {}).get("facts", "")
        try:
            data = await self._via_llm(script, original, facts)
            hook = (data.get("hook") or "").strip()
            overlay = (data.get("overlay") or "").strip()
        except llm.LLMUnavailable:
            hook, overlay = self._offline(script, original)

        if hook:
            scenes[0]["narration"] = hook
            scenes[0]["is_highlight"] = True
            script["hook_text"] = hook
            script["hook_overlay"] = (overlay or hook[:40]).upper()
            _recompute_narration(script)
            self.emit("progress", "Gancho reescrito para retenção", progress=24)
        self.ctx_set("script", script)
        return script

    async def _via_llm(self, script, original, facts) -> dict:
        fact_note = f"\nFATOS REAIS (não contradiga, não invente):\n{facts[:800]}" if facts else ""
        prompt = f"""Você é um editor viral (estilo MrBeast/criadores de topo). Reescreva o
GANCHO de abertura deste vídeo do tipo "{script.get('content_type')}", título
"{script.get('title')}".

Gancho atual: "{original}"

Regras do gancho (primeiros 3 segundos decidem tudo):
- 1 a 2 frases, faladas em até ~10s, em português coloquial e ENÉRGICO.
- Use quebra de padrão, lacuna de curiosidade ou afirmação ousada/contraintuitiva.
- Crie tensão/promessa que só se resolve assistindo. NUNCA seja genérico ("hoje vamos falar...").
- Verdadeiro: não invente fatos.{fact_note}

Responda SÓ JSON: {{"hook": "<nova narração da 1a cena>", "overlay": "<texto curto p/ tela, 2-5 palavras MAIÚSCULAS>"}}"""
        return await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=400)

    @staticmethod
    def _offline(script: dict, original: str) -> tuple[str, str]:
        title = script.get("title", "isso")
        hook = f"Espera — você precisa ver o que rolou em {title} antes de qualquer coisa."
        return hook, "VOCÊ VIU ISSO?"


class RetentionEngineerAgent(BaseAgent):
    name = "retention_engineer"

    async def run(self, script: dict | None = None, **_) -> dict:
        script = script or self.ctx_get("script") or {}
        scenes = script.get("scenes", [])
        if len(scenes) < 3 or script.get("content_type") in _NO_NARRATION:
            return script
        self.emit("progress", "Engenharia de retenção (re-gancho + CTA)", progress=25)

        try:
            data = await self._via_llm(script)
            rehook = (data.get("rehook") or "").strip()
            cta = (data.get("cta") or "").strip()
        except llm.LLMUnavailable:
            rehook, cta = self._offline(script)

        mid = len(scenes) // 2
        if rehook:
            scenes[mid]["narration"] = f"{rehook} {scenes[mid].get('narration', '')}".strip()
        if cta:
            scenes[-1]["narration"] = cta
        script["retention_notes"] = {"rehook_at_scene": mid, "cta": cta}
        _recompute_narration(script)
        self.ctx_set("script", script)
        self.emit("progress", "Retenção: open loop + CTA aplicados", progress=26)
        return script

    async def _via_llm(self, script) -> dict:
        prompt = f"""Vídeo "{script.get('title')}" ({script.get('content_type')}), {len(script.get('scenes', []))} cenas.
Para MAXIMIZAR RETENÇÃO e watch time:
1) "rehook": uma frase curta de RE-GANCHO (open loop) p/ injetar no meio do vídeo,
   prometendo algo que vem a seguir ("mas o que vem agora muda tudo...").
2) "cta": a fala FINAL — chamada à ação forte + gancho de loop (faz querer rever/seguir),
   em português, 1 frase.
Responda SÓ JSON: {{"rehook": "...", "cta": "..."}}"""
        return await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=300)

    @staticmethod
    def _offline(script: dict) -> tuple[str, str]:
        return ("Mas o que vem agora muda completamente a história.",
                "Se você chegou até aqui, segue o canal — o próximo vídeo vai te surpreender ainda mais.")


class PackagingStrategistAgent(BaseAgent):
    name = "packaging_strategist"

    async def run(self, script: dict | None = None, target_platforms: list | None = None, **_) -> dict:
        script = script or self.ctx_get("script") or {}
        platforms = target_platforms or self.ctx_get("target_platforms") or ["youtube"]
        self.emit("progress", "Empacotamento de alto CTR (título + thumb)", progress=27)

        facts = (self.ctx_get("research") or {}).get("facts", "")
        try:
            pkg = await self._via_llm(script, platforms, facts)
        except llm.LLMUnavailable:
            pkg = self._offline(script)

        yt_titles = [t for t in (pkg.get("youtube_titles") or []) if t][:3]
        if yt_titles:
            script["title"] = yt_titles[0]
            script["title_options"] = yt_titles
        script["packaging"] = pkg
        self.ctx_set("packaging", pkg)
        self.ctx_set("script", script)
        self.emit("progress", "Pacote pronto (títulos + conceito de thumbnail)", progress=28)
        return pkg

    async def _via_llm(self, script, platforms, facts) -> dict:
        fact_note = f"\nFATOS REAIS (base p/ títulos verdadeiros):\n{facts[:600]}" if facts else ""
        prompt = f"""Você é estrategista de crescimento (YouTube/TikTok/Instagram). Crie o
PACOTE de alto CTR para "{script.get('title')}" ({script.get('content_type')}),
plataformas: {platforms}.{fact_note}

JSON EXATO:
{{
  "youtube_titles": ["<=60 chars, curiosidade/emoção, sem clickbait falso", "...", "..."],
  "tiktok_title": "<curto, gancho + 2-3 hashtags de nicho>",
  "thumbnail": {{"text": "2-4 PALAVRAS gigantes", "emotion": "<choque/curiosidade/raiva/euforia>", "visual": "<elemento visual principal em inglês p/ imagem>"}}
}}"""
        return await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=600)

    @staticmethod
    def _offline(script: dict) -> dict:
        title = script.get("title", "Vídeo")
        return {
            "youtube_titles": [f"{title}: o que ninguém te contou"[:60],
                               f"A verdade sobre {title}"[:60],
                               f"{title} mudou tudo 😱"[:60]],
            "tiktok_title": f"{title} 👀 #fyp #viral",
            "thumbnail": {"text": title.split()[0:3] and " ".join(title.split()[:3]).upper() or "VEJA ISSO",
                          "emotion": "curiosidade", "visual": "dramatic close up face"},
        }


# ============================================================================
# Shorts specialists — run AFTER the ShortsFactory, tuned for vertical <60s on
# TikTok / Reels / YouTube Shorts (fast hook, loop-friendly, platform captions).
# They annotate each short dict in ctx['shorts'] (no re-render) so the approval
# UI and publisher get a ready-to-post package per short.
# ============================================================================
class ShortsHookAgent(BaseAgent):
    name = "shorts_hook"

    async def run(self, shorts: list | None = None, script: dict | None = None, **_) -> dict:
        shorts = shorts if shorts is not None else (self.ctx_get("shorts") or [])
        script = script or self.ctx_get("script") or {}
        if not shorts:
            return {"shorts": shorts}
        self.emit("progress", "Gancho dos Shorts (primeiro frame)", progress=83)

        try:
            data = await self._via_llm(script)
            overlay = (data.get("overlay") or "").strip().upper()
            best = data.get("best_format")
        except llm.LLMUnavailable:
            overlay = (script.get("hook_overlay") or "VOCÊ PRECISA VER ISSO").upper()[:40]
            best = None

        for s in shorts:
            s["hook_overlay"] = overlay
        # Recommend the best format to publish first (default: 'standard' 15s).
        rec = best if best in {f.get("name") for f in shorts} else "standard"
        for s in shorts:
            s["recommended"] = (s.get("name") == rec)
        self.ctx_set("shorts", shorts)
        self.emit("progress", "Shorts: gancho e formato recomendado", progress=84)
        return {"shorts": shorts}

    async def _via_llm(self, script) -> dict:
        prompt = f"""Vídeo vertical (Short/TikTok/Reels) sobre "{script.get('title')}".
Os 3 primeiros segundos definem se a pessoa para de rolar.
Responda SÓ JSON:
{{"overlay": "<2-5 PALAVRAS gigantes p/ o 1o frame, em MAIÚSCULAS>",
  "best_format": "<hook|standard|medium|long|mini — o tamanho que mais viraliza p/ este tema>"}}"""
        return await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=200)


class ShortsStrategistAgent(BaseAgent):
    name = "shorts_strategist"

    async def run(self, shorts: list | None = None, script: dict | None = None,
                  target_platforms: list | None = None, **_) -> dict:
        shorts = shorts if shorts is not None else (self.ctx_get("shorts") or [])
        script = script or self.ctx_get("script") or {}
        platforms = target_platforms or self.ctx_get("target_platforms") or ["youtube", "tiktok", "instagram"]
        if not shorts:
            return {"shorts_strategy": {}}
        self.emit("progress", "Estratégia de Shorts (captions por plataforma)", progress=85)

        try:
            strat = await self._via_llm(script, platforms)
        except llm.LLMUnavailable:
            strat = self._offline(script)

        for s in shorts:
            s["captions"] = strat.get("captions", {})
            s["hashtags"] = strat.get("hashtags", [])
        self.ctx_set("shorts", shorts)
        self.ctx_set("shorts_strategy", strat)
        self.emit("progress", "Shorts prontos p/ postar (TikTok/Reels/Shorts)", progress=86)
        return {"shorts_strategy": strat}

    async def _via_llm(self, script, platforms) -> dict:
        prompt = f"""Estrategista de Shorts virais. Crie o pacote de publicação para um
Short vertical sobre "{script.get('title')}" ({script.get('content_type')}),
plataformas: {platforms}.
JSON EXATO:
{{
  "captions": {{
    "tiktok": "<gancho + CTA curto, <=150 chars>",
    "instagram": "<caption envolvente <=300 chars>",
    "youtube_shorts": "<título <=80 chars com #Shorts>"
  }},
  "hashtags": ["8-12 hashtags de nicho + alcance (sem espaços)"]
}}"""
        return await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=500)

    @staticmethod
    def _offline(script: dict) -> dict:
        t = script.get("title", "isso")
        return {
            "captions": {
                "tiktok": f"{t} 👀 assiste até o fim! #fyp",
                "instagram": f"{t} — salva e compartilha! 🔥",
                "youtube_shorts": f"{t} #Shorts"[:80],
            },
            "hashtags": ["#fyp", "#viral", "#shorts", "#reels", "#foryou", "#brasil",
                         "#trending", "#tiktok", "#explore", "#viralvideo"],
        }
