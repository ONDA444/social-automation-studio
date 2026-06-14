"""
ScriptwriterAgent — original scripts via LLM (Groq -> Gemini -> Ollama),
with a deterministic offline fallback so the pipeline runs without API keys.

Output schema (stored on VideoContext['script']):
{
  "title_options": [str, str, str],
  "title": str,
  "tone": str,
  "content_type": str,
  "estimated_duration": int,          # seconds
  "narration_text": str,              # full narration (empty for quote_viral)
  "scenes": [
     {"index": int, "narration": str, "visual_prompt": str,
      "visual_query": str, "is_highlight": bool}
  ],
  "on_screen_text": [str],            # quote_viral: the text shown on screen
  "seo_keywords": [str]
}
"""
from __future__ import annotations

import asyncio

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend import llm

SYSTEM = (
    "Você é um roteirista profissional de conteúdo viral para YouTube, TikTok e "
    "Instagram. Escreve roteiros 100% originais, em {lang}. Responda SEMPRE apenas "
    "com JSON válido, sem comentários."
)

# Per-content-type instructions injected into the LLM prompt.
TEMPLATE_GUIDE = {
    "film_recap_ai_images": (
        "Tipo: recap dramático narrado (estilo documentário), com imagens geradas por IA.\n"
        "Estrutura: GANCHO (10%) -> CONTEXTO (15%) -> DESENVOLVIMENTO (60%) -> "
        "CLÍMAX (10%) -> CTA (5%).\n"
        "Duração alvo: 120-300s (400-700 palavras). 8-14 cenas.\n"
        "Cada cena tem narração (2-4 frases) e um 'visual_prompt' EM INGLÊS, detalhado, "
        "cinematográfico, para gerar imagem (sem citar filmes/marcas reais)."
    ),
    "sports_highlights": (
        "Tipo: melhores momentos esportivos, tom energético.\n"
        "Estrutura: FATO CHOCANTE -> CONTEXTO -> LANCES -> ANÁLISE -> CTA.\n"
        "Duração alvo: 45-120s. 5-10 cenas. Narrações curtas e impactantes.\n"
        "Cada cena tem narração e um 'visual_query' (palavras-chave EM INGLÊS para "
        "buscar stock footage, ex.: 'soccer stadium crowd night')."
    ),
    "quote_viral": (
        "Tipo: frase única reflexiva/provocativa (15-40 palavras). SEM narração.\n"
        "O TEXTO NA TELA é o conteúdo. Gere 3 variações em 'on_screen_text' para A/B.\n"
        "Duração alvo: 5-15s. 1-3 cenas de fundo com 'visual_query' EM INGLÊS "
        "(ex.: 'dark abstract atmosphere', 'city night rain')."
    ),
}


class ScriptwriterAgent(BaseAgent):
    name = "scriptwriter"

    async def run(
        self,
        title: str = "",
        topic: str | None = None,
        mode: str = "from_title",
        content_type: str = "film_recap_ai_images",
        style_dna: dict | None = None,
        language: str | None = None,
        **_,
    ) -> dict:
        language = language or settings.default_language
        content_type = content_type if content_type in TEMPLATE_GUIDE else "film_recap_ai_images"
        theme = topic or title

        self.emit("progress", f"Gerando roteiro ({content_type}, modo={mode})", progress=20)

        try:
            script = await self._via_llm(theme, title, mode, content_type, style_dna, language)
        except llm.LLMUnavailable:
            self.emit("progress", "Sem LLM disponível — usando roteiro offline (placeholder)", progress=30)
            script = self._offline(theme, title, content_type, language)

        # Normalise / derive fields.
        script.setdefault("content_type", content_type)
        script.setdefault("tone", "dramatic" if content_type == "film_recap_ai_images" else "energetic")
        scenes = script.get("scenes", [])
        for i, sc in enumerate(scenes):
            sc.setdefault("index", i)
            sc.setdefault("narration", "")
            sc.setdefault("visual_prompt", "")
            sc.setdefault("visual_query", "")
            sc.setdefault("is_highlight", False)
        if content_type != "quote_viral":
            script["narration_text"] = " ".join(s["narration"] for s in scenes if s.get("narration")).strip()
        else:
            script["narration_text"] = ""
        script.setdefault("on_screen_text", script.get("on_screen_text", []))
        titles = script.get("title_options") or [title or theme]
        script["title_options"] = titles[:3]
        script["title"] = script.get("title") or titles[0]
        script.setdefault("estimated_duration", self._estimate_duration(script, content_type))
        script.setdefault("seo_keywords", script.get("seo_keywords", []))

        self.ctx_set("script", script)
        self.emit("progress", f"Roteiro pronto: {len(scenes)} cena(s)", progress=40)
        return script

    async def _via_llm(self, theme, title, mode, content_type, style_dna, language) -> dict:
        guide = TEMPLATE_GUIDE[content_type]
        style_hint = ""
        if mode == "from_remix" and style_dna:
            style_hint = (
                f"\nAdapte ao estilo de referência (StyleDNA): "
                f"pacing={style_dna.get('pacing', {}).get('style')}, "
                f"mood={style_dna.get('audio', {}).get('music_mood')}, "
                f"content_type={style_dna.get('content_type')}."
            )
        prompt = f"""Tema: "{theme}"
Modo: {mode}

{guide}{style_hint}

Responda com JSON neste formato EXATO:
{{
  "title_options": ["...", "...", "..."],
  "tone": "...",
  "estimated_duration": <segundos>,
  "scenes": [
    {{"narration": "...", "visual_prompt": "...", "visual_query": "...", "is_highlight": false}}
  ],
  "on_screen_text": ["..."],
  "seo_keywords": ["...", "..."]
}}
Para film_recap_ai_images preencha visual_prompt; para sports_highlights e quote_viral
preencha visual_query; quote_viral deixa narration vazio e usa on_screen_text."""
        system = SYSTEM.format(lang=language)
        return await llm.complete_json(prompt, system=system, max_tokens=3000)

    # ---- Offline deterministic fallback (no API keys needed) ----
    def _offline(self, theme: str, title: str, content_type: str, language: str) -> dict:
        theme = theme or "História impressionante"
        if content_type == "quote_viral":
            quotes = [
                f"{theme}: o que ninguém te conta muda tudo.",
                f"Pense nisto sobre {theme.lower()} — e nada será igual.",
                f"{theme} não é sorte. É escolha.",
            ]
            return {
                "title_options": [theme, f"{theme} (reflexão)", f"A verdade sobre {theme}"],
                "tone": "reflective",
                "estimated_duration": 10,
                "scenes": [{"narration": "", "visual_prompt": "",
                            "visual_query": "dark abstract atmosphere cinematic", "is_highlight": True}],
                "on_screen_text": quotes,
                "seo_keywords": [theme.lower(), "reflexão", "motivação"],
            }

        is_sport = content_type == "sports_highlights"
        n = 6 if is_sport else 9
        scenes = []
        beats = [
            ("Você não vai acreditar no que aconteceu com {t}.", True),
            ("Tudo começou de um jeito que ninguém esperava.", False),
            ("O contexto por trás de {t} é mais profundo do que parece.", False),
            ("E então veio o momento que mudou tudo.", True),
            ("As consequências foram imediatas e impactantes.", False),
            ("Cada detalhe revela uma camada nova de {t}.", False),
            ("O clímax deixou todos sem palavras.", True),
            ("No fim, a lição que fica é poderosa.", False),
            ("Se curtiu, segue o canal para mais sobre {t}.", False),
        ]
        for i in range(n):
            text, hi = beats[i % len(beats)]
            narration = text.format(t=theme)
            if is_sport:
                scenes.append({
                    "narration": narration,
                    "visual_prompt": "",
                    "visual_query": f"{theme} sports action stadium",
                    "is_highlight": hi,
                })
            else:
                scenes.append({
                    "narration": narration,
                    "visual_prompt": f"cinematic dramatic scene about {theme}, photorealistic, 4k, moody lighting",
                    "visual_query": "",
                    "is_highlight": hi,
                })
        return {
            "title_options": [
                f"{theme}: a história completa",
                f"O que aconteceu com {theme}",
                f"{theme} como você nunca viu",
            ],
            "tone": "energetic" if is_sport else "dramatic",
            # estimated_duration intentionally omitted -> computed from narration_text
            "scenes": scenes,
            "on_screen_text": [],
            "seo_keywords": [theme.lower(), "história", "viral"],
        }

    @staticmethod
    def _estimate_duration(script: dict, content_type: str) -> int:
        if content_type == "quote_viral":
            return 10
        words = len((script.get("narration_text") or "").split())
        # ~2.5 words/second narration.
        return max(15, round(words / 2.5)) if words else 60


# --- standalone test: python -m backend.agents.scriptwriter --test ---
if __name__ == "__main__":
    import json
    import sys

    ct = "film_recap_ai_images"
    if "--sport" in sys.argv:
        ct = "sports_highlights"
    if "--quote" in sys.argv:
        ct = "quote_viral"

    async def _demo():
        agent = ScriptwriterAgent(job_id=0, emit=False)
        result = await agent.execute(
            title="O mistério do farol abandonado", mode="from_title", content_type=ct
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))

    asyncio.run(_demo())
