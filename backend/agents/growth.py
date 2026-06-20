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
    """Keep narration_text and tts_text in sync after a scene-narration rewrite."""
    if script.get("content_type") in _NO_NARRATION:
        return
    script["narration_text"] = " ".join(
        s.get("narration", "") for s in script.get("scenes", []) if s.get("narration")
    ).strip()
    from backend.agents.scriptwriter import build_tts_text
    script["tts_text"] = build_tts_text(script.get("scenes", []))


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
        video_format = script.get("format", "long")
        word_limit = "até 8 palavras" if video_format == "short" else "8-14 palavras"
        prompt = f"""Você é um editor viral especialista em gancho de abertura.
Refine o GANCHO da 1ª cena do vídeo abaixo — SOMENTE se melhorar; preserve o mecanismo original.

Tipo: "{script.get('content_type')}"  Título: "{script.get('title')}"
Gancho atual: "{original}"

REGRAS (não viole — são duras):
- 1ª frase falável: {word_limit}, ZERO aquecimento, ZERO saudação.
- >=1 elemento CONCRETO (número/nome/data/valor). NUNCA invente fatos.
- Use UM mecanismo (curiosity_gap|bold_claim|high_stakes|negation|numbered|in_medias_res).
- O overlay NUNCA repete a fala; é 2-5 PALAVRAS MAIÚSCULAS com dado/curiosidade.
- PROIBIDO: 'olá pessoal', 'você não vai acreditar', 'prepare-se', 'segura essa',
  'presta atenção', 'hoje eu vou te mostrar', 'você já parou para pensar'.{fact_note}

Responda SÓ JSON: {{"hook": "<narração nova da 1ª cena>", "overlay": "<2-5 PALAVRAS MAIÚSCULAS>"}}"""
        # Context-dependent rewrite (needs the channel's facts/identity) — use the
        # stronger tier, not the 3B 'fast' models that emit generic/invalid JSON.
        return await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=400)

    @staticmethod
    def _offline(script: dict, original: str) -> tuple[str, str]:
        title = script.get("title", "isso")
        hook = f"{title} — tem um detalhe que quase ninguém percebeu nisso."
        return hook, title.split()[0].upper()[:20] if title.split() else "REVELADO"


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
            # Publish the title the strategist flagged as highest expected CTR in the
            # feed — not just the first one. The index was computed and then thrown
            # away; clamp it to the available range as a safety net.
            idx = pkg.get("recommended_index", 0)
            if not isinstance(idx, int) or not (0 <= idx < len(yt_titles)):
                idx = 0
            script["title"] = yt_titles[idx]
            script["title_options"] = yt_titles
        script["packaging"] = pkg
        self.ctx_set("packaging", pkg)
        self.ctx_set("script", script)
        self.emit("progress", "Pacote pronto (títulos + conceito de thumbnail)", progress=28)
        return pkg

    async def _via_llm(self, script, platforms, facts) -> dict:
        fact_note = f"\nFATOS REAIS (base p/ títulos verdadeiros — não contradiga):\n{facts[:600]}" if facts else ""
        hook_spoken = ""
        scenes = script.get("scenes", [])
        if scenes:
            hook_spoken = scenes[0].get("narration", "")[:200]
        prompt = f"""Você é o PACKAGING STRATEGIST — fonte única de verdade do título e thumbnail.
Crie o pacote de alto CTR para "{script.get('title')}" ({script.get('content_type')}),
plataformas: {platforms}.
Gancho falado (scenes[0]): "{hook_spoken}"{fact_note}

7 FÓRMULAS (use fórmulas DIFERENTES nos 3 títulos):
F1 curiosity_gap : nomeia resultado, esconde causa. ("O detalhe que mudou tudo em X")
F2 number+stakes : número concreto + o que está em jogo. ("3 erros que custaram X")
F3 contrarian    : quebra senso comum com afirmação específica. ("X não é o que te contaram")
F4 detail_hook   : detalhe inquietante e concreto vira título.
F5 question_open : pergunta que SÓ o vídeo responde (sem ser vaga).
F6 negation      : "quase ninguém percebeu [detalhe]" / "ninguém te contou [fato]".
F7 listicle_rank : ranking/numerado com gap na ponta.

REGRAS: <=60 chars cada; sem clickbait falso (a promessa PRECISA ser verdadeira);
sem caps-lock integral; sem mais de 1 emoji; recommended_index = o de maior CTR esperado
no FEED deste público (não o mais completo).

JSON EXATO:
{{
  "youtube_titles": [
    {{"text": "título 1", "formula": "F1", "char_count": 0}},
    {{"text": "título 2", "formula": "F3", "char_count": 0}},
    {{"text": "título 3", "formula": "F5", "char_count": 0}}
  ],
  "recommended_index": 0,
  "why_recommended": "1-2 frases: público + mecanismo + por que ganha CTR no feed",
  "tiktok_title": "<curto, gancho + 2-3 hashtags de nicho>",
  "thumbnail": {{
    "text": "2-4 PALAVRAS MAIÚSCULAS",
    "emotion": "curiosidade|choque|medo|euforia|indignacao",
    "visual": "EN: cena/objeto herói da capa, sem texto/logo",
    "composition": "posição do sujeito + lado do texto + contraste"
  }},
  "coherence_check": "1 frase: título+thumb+gancho prometem a MESMA recompensa, paga em X"
}}"""
        result = await llm.complete_json(prompt, system=with_style("Responda só com JSON válido."), max_tokens=800)
        # Flatten youtube_titles to strings for backward compat
        raw_titles = result.get("youtube_titles", [])
        flat = []
        for t in raw_titles:
            if isinstance(t, dict):
                flat.append(t.get("text", ""))
            elif isinstance(t, str):
                flat.append(t)
        if flat:
            result["youtube_titles"] = [t for t in flat if t]
        return result

    @staticmethod
    def _offline(script: dict) -> dict:
        title = script.get("title", "Vídeo")
        words = title.split()
        thumb_text = " ".join(words[:3]).upper() if words else "REVELADO"
        return {
            "youtube_titles": [
                f"{title}: o que ninguém te contou"[:60],
                f"A verdade sobre {title}"[:60],
                f"Quase ninguém percebeu isso em {title}"[:60],
            ],
            "recommended_index": 0,
            "why_recommended": "curiosity_gap com fato não revelado funciona bem no feed.",
            "tiktok_title": f"{title} 👀 #fyp #viral",
            "thumbnail": {"text": thumb_text, "emotion": "curiosidade",
                          "visual": "dramatic close up scene", "composition": "subject left, text right"},
            "coherence_check": "título e gancho prometem o fato principal do vídeo.",
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
