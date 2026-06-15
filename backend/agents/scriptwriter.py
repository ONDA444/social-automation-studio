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
    "Você é um roteirista de ELITE de YouTube/TikTok/Instagram — referência em "
    "RETENÇÃO de audiência, no nível dos canais que seguram o espectador até o fim. "
    "Você pensa em retenção a cada frase: os 3 primeiros segundos decidem tudo, cada "
    "frase existe pra fazer a próxima ser assistida, e o vídeo entrega no fim a "
    "recompensa prometida no gancho. Escreve em {lang}, conteúdo 100% original. "
    "Responda SEMPRE apenas com JSON válido, sem comentários."
)

# Injected into every narrated script (not quote_viral). This is the "cérebro" —
# the retention discipline that separates a script people finish from filler.
RETENTION_RULES = (
    "\n=== REGRAS DE RETENÇÃO (OBRIGATÓRIAS) ===\n"
    "1. GANCHO (1ª frase): ZERO aquecimento. Abra com a informação mais surpreendente/"
    "específica OU uma lacuna de curiosidade concreta (um número, um nome, uma aposta "
    "clara). O espectador tem que PRECISAR saber o que vem.\n"
    "2. SEM ENROLAÇÃO: toda frase entrega informação, tensão ou avanço da história. "
    "Nada de frase de encheção de linguiça.\n"
    "3. ESPECÍFICO > genérico: use nomes, lugares e números CONCRETOS (somente os fatos "
    "verificados — NUNCA invente). Detalhe concreto prende; vago faz pular o vídeo.\n"
    "4. CICLOS ABERTOS: levante uma pergunta no começo e só responda mais pra frente; "
    "encadeie com 'mas', 'então', 'até que' pra puxar a próxima cena.\n"
    "5. RITMO: alterne frases curtas e médias, com viradas. Tom de quem conversa, não "
    "de narração de enciclopédia.\n"
    "6. RECOMPENSA + CTA: entregue o que o gancho prometeu e feche com um CTA ligado à "
    "curiosidade do tema — nunca um 'segue o canal' solto.\n"
    "7. PROIBIDO começar ou rechear com clichês vazios como: 'Espera, você precisa ver', "
    "'Tudo começou de um jeito que ninguém esperava', 'isso é mais profundo do que "
    "parece', 'as consequências foram imediatas', 'o que vem agora muda tudo', "
    "'prepare-se', 'você não vai acreditar'.\n"
    "8. TÍTULO: específico + lacuna de curiosidade (com número/aposta quando couber). "
    "Nada de título genérico.\n"
    "=== FIM DAS REGRAS ===\n"
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
    "top_list_ranking": (
        "Tipo: ranking 'Top 5' ou 'Top 10' com contagem regressiva, tom empolgado.\n"
        "Estrutura: GANCHO ('o nº1 vai te chocar') -> ITENS do Nº N ao Nº1 (suspense "
        "crescente) -> REVELAÇÃO do nº1 -> CTA.\n"
        "Duração alvo: 60-180s. 7-12 cenas (uma por item + gancho/CTA). Narrações "
        "curtas e punchy; anuncie sempre o número do item.\n"
        "Cada cena tem narração e um 'visual_query' (palavras-chave EM INGLÊS para "
        "buscar stock footage do item, ex.: 'fastest car on track')."
    ),
    "explainer_curiosity": (
        "Tipo: explicação de curiosidade científica/histórica no formato 'por que X?'.\n"
        "Estrutura: PERGUNTA INTRIGANTE -> CONTEXTO -> EXPLICAÇÃO (passo a passo) -> "
        "FATO SURPRESA -> CTA.\n"
        "Duração alvo: 90-240s (350-650 palavras). 7-12 cenas. Tom curioso e didático, "
        "ritmo médio.\n"
        "Cada cena tem narração (2-4 frases) e um 'visual_prompt' EM INGLÊS, detalhado, "
        "para gerar imagem ilustrativa do conceito (sem marcas reais)."
    ),
    "true_crime_mystery": (
        "Tipo: caso real ou mistério não resolvido, tom sombrio/noir e investigativo.\n"
        "Estrutura: CENA DO CRIME (gancho) -> VÍTIMA/CONTEXTO -> PISTAS -> SUSPEITOS/"
        "TEORIAS -> O MISTÉRIO QUE PERMANECE -> CTA.\n"
        "Duração alvo: 180-360s (500-800 palavras). 8-14 cenas. Narração tensa, pausada, "
        "sussurrada; evite citar nomes reais (use iniciais/fictícios).\n"
        "Cada cena tem narração e um 'visual_prompt' EM INGLÊS, cinematográfico e "
        "sombrio (ex.: 'dark rainy alley crime scene, noir lighting, 4k')."
    ),
    "reaction_commentary": (
        "Tipo: comentário/reação a uma tendência ou notícia do momento, dinâmico e opinativo.\n"
        "Estrutura: 'VOCÊ VIU ISSO?' (gancho) -> O QUE ACONTECEU -> MINHA OPINIÃO/HOT "
        "TAKE -> CONTRAPONTO -> CTA ('comenta o que você acha').\n"
        "Duração alvo: 45-120s. 6-10 cenas. Narração coloquial, rápida e com energia.\n"
        "Cada cena tem narração e um 'visual_query' (palavras-chave EM INGLÊS para "
        "buscar stock footage do tema, ex.: 'person reacting shocked phone')."
    ),
    "reddit_story": (
        "Tipo: narração de história estilo Reddit ('r/...'), em primeira pessoa, vertical (Shorts).\n"
        "Estrutura: SETUP ('isso aconteceu comigo') -> DESENVOLVIMENTO (tensão "
        "crescente) -> REVIRAVOLTA -> DESFECHO -> CTA ('qual seria sua reação?').\n"
        "Duração alvo: 30-90s. 5-9 cenas. Narração em 1ª pessoa, ritmo de fofoca; "
        "as LEGENDAS GRANDES são o foco visual.\n"
        "Cada cena tem narração e um 'visual_query' (palavras-chave EM INGLÊS para "
        "fundo neutro/satisfatório, ex.: 'satisfying gameplay background vertical')."
    ),
    "motivational_speech": (
        "Tipo: discurso motivacional narrado, com b-roll cinematográfico e música épica.\n"
        "Estrutura: REALIDADE DURA (gancho) -> VIRADA DE CHAVE -> CHAMADO À AÇÃO "
        "(crescendo) -> FRASE DE IMPACTO FINAL -> CTA.\n"
        "Duração alvo: 45-120s (150-350 palavras). 5-10 cenas. Narração intensa, "
        "imperativa, em 2ª pessoa ('você consegue').\n"
        "Cada cena tem narração e um 'visual_query' (palavras-chave EM INGLÊS de b-roll "
        "épico, ex.: 'lone runner sunrise mountain cinematic')."
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
        research: dict | None = None,
        video_format: str = "long",
        **_,
    ) -> dict:
        language = language or settings.default_language
        theme = topic or title
        if content_type in (None, "", "auto"):
            content_type = self._detect_content_type(theme)
            self.emit("progress", f"Tipo detectado automaticamente: {content_type}", progress=12)
        content_type = content_type if content_type in TEMPLATE_GUIDE else "film_recap_ai_images"
        research = research or self.ctx_get("research") or {}
        video_format = video_format or self.ctx_get("format") or "long"

        self.emit("progress", f"Gerando roteiro ({content_type}, {video_format}, modo={mode})", progress=20)

        try:
            script = await self._via_llm(theme, title, mode, content_type, style_dna, language, research, video_format)
        except llm.LLMUnavailable:
            self.emit("progress", "Sem LLM disponível — usando roteiro offline (placeholder)", progress=30)
            script = self._offline(theme, title, content_type, language)

        # Harden the most failure-prone seam: a syntactically-valid LLM JSON whose
        # "scenes" is the wrong shape (null / list of strings / dict) would crash the
        # normalization loop below. Coerce to the offline template instead of aborting.
        sc_list = script.get("scenes") if isinstance(script, dict) else None
        if not (isinstance(sc_list, list) and sc_list and all(isinstance(s, dict) for s in sc_list)):
            self.emit("progress", "Roteiro LLM com 'scenes' inválido — usando offline", progress=30)
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

        # Native short: keep it tight (vertical <60s). Cap scenes and recompute.
        script["format"] = video_format
        if video_format == "short" and len(scenes) > 6:
            scenes = scenes[:6]
            script["scenes"] = scenes
            if content_type != "quote_viral":
                script["narration_text"] = " ".join(
                    s.get("narration", "") for s in scenes if s.get("narration")).strip()
            script["estimated_duration"] = self._estimate_duration(script, content_type)

        self.ctx_set("script", script)
        self.emit("progress", f"Roteiro pronto: {len(scenes)} cena(s) [{video_format}]", progress=40)
        return script

    async def _via_llm(self, theme, title, mode, content_type, style_dna, language, research=None, video_format="long") -> dict:
        guide = TEMPLATE_GUIDE[content_type]
        style_hint = ""
        if mode == "from_remix" and style_dna:
            style_hint = (
                f"\nAdapte ao estilo de referência (StyleDNA): "
                f"pacing={style_dna.get('pacing', {}).get('style')}, "
                f"mood={style_dna.get('audio', {}).get('music_mood')}, "
                f"content_type={style_dna.get('content_type')}."
            )

        facts = (research or {}).get("facts", "").strip()
        if (research or {}).get("grounded") and facts:
            facts_block = (
                "\n\n=== FATOS VERIFICADOS (FONTE DA VERDADE) ===\n"
                "Baseie TODA a narração SOMENTE nestes fatos reais. É TERMINANTEMENTE "
                "PROIBIDO inventar placares, datas, nomes, números ou resultados. Se "
                "uma informação não estiver aqui, NÃO a afirme.\n" + facts + "\n"
                "=== FIM DOS FATOS ===\n"
            )
        else:
            facts_block = (
                "\n\n[ATENÇÃO] SEM FATOS VERIFICADOS para este tema. É PROIBIDO inventar "
                "resultados, placares, datas, nomes, números ou dizer que algo 'aconteceu "
                "hoje/ontem'. Se o tema pede um resultado/evento recente que você NÃO pode "
                "confirmar, não finja saber: fale da expectativa, do contexto e da importância "
                "de forma geral e atemporal, deixando claro que o desfecho não é afirmado.\n"
            )

        if video_format == "short":
            length_block = (
                "FORMATO: SHORT VERTICAL 9:16. Seja MUITO conciso: 4 a 6 cenas, 25 a 45 "
                "SEGUNDOS no total. Gancho imediato no 1º segundo, ritmo rápido, frases "
                "curtas e punchy. Corte tudo que não prende. (Ignore a duração-alvo longa acima.)"
            )
        else:
            length_block = (
                "IMPORTANTE (duração): gere o roteiro COMPLETO atingindo a contagem de "
                "palavras/cenas alvo do tipo acima — vídeos curtos demais são rejeitados. Não resuma."
            )
        retention_block = RETENTION_RULES if content_type != "quote_viral" else ""
        # The two coaches' accumulated knowledge (engagement + per-type/format tips).
        try:
            from backend.agents.coach import playbook_prompt_block
            coach_block = playbook_prompt_block(content_type, video_format)
        except Exception:
            coach_block = ""
        prompt = f"""Tema: "{theme}"
Modo: {mode}

{guide}{style_hint}{retention_block}{coach_block}{facts_block}
{length_block}

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
REGRA DOS VISUAIS (importante — o sistema usa VÍDEO real de stock):
- Em TODA cena preencha "visual_query": 2-5 palavras-chave CONCRETAS em inglês de
  algo REAL e filmável (lugares, objetos, ações, natureza), ex.: "soccer stadium
  night crowd", "rain city street neon", "old book candle close up". Evite nomes
  próprios/marcas e conceitos abstratos (eles não retornam stock footage).
- Preencha também "visual_prompt" (descrição cinematográfica em inglês) — é o
  fallback de imagem IA quando não houver clipe de vídeo para a cena.
- quote_viral deixa "narration" vazio e usa "on_screen_text"."""
        from backend.agents.style_guide import with_style
        system = with_style(SYSTEM.format(lang=language))
        return await llm.complete_json(prompt, system=system, max_tokens=4000)

    @classmethod
    def _detect_content_type(cls, theme: str) -> str:
        """Keyword-based auto-detection — picks the best content_type for a theme."""
        t = f" {(theme or '').lower()} "
        # Sports: reuse the existing domain keywords (most specific signal)
        sport_kws = cls._DOMAIN_KEYWORDS.get("soccer", []) + cls._DOMAIN_KEYWORDS.get("basketball", [])
        if any(k in t for k in sport_kws):
            return "sports_highlights"
        # Ranking / top N
        if any(k in t for k in ["top ", "top5", "top10", "top 5", "top 10", "melhores ", "ranking", "piores "]):
            return "top_list_ranking"
        # True crime / mystery
        if any(k in t for k in ["crime", "assassin", "mistério", "misterio", "desaparec",
                                  "serial killer", "caso", "morreu", "morte de ", "homicid"]):
            return "true_crime_mystery"
        # Motivational
        if any(k in t for k in ["motivação", "motivacao", "não desista", "nao desista",
                                  "disciplina", "mindset", "acredite", "guerreiro"]):
            return "motivational_speech"
        # Curiosity / explainer
        if any(k in t for k in ["por que", "como funciona", "por quê", "porquê",
                                  "ciência", "ciencia", "descoberta", "fenômeno", "fenomeno", "teoria"]):
            return "explainer_curiosity"
        # Reddit story
        if any(k in t for k in ["reddit", "aconteceu comigo", "confissão", "confissao", "tifu"]):
            return "reddit_story"
        # Reaction / commentary
        if any(k in t for k in ["react", "polêmica", "polemica", "notícia", "noticia", "trending"]):
            return "reaction_commentary"
        # Quote viral
        if any(k in t for k in ["frase", "reflexão", "reflexao", "filosofia", "sabedoria"]):
            return "quote_viral"
        return "film_recap_ai_images"

    # Domain -> concrete English stock-video search terms. Lets the OFFLINE
    # fallback (when the LLM is rate-limited) still pull ON-THEME footage instead
    # of generic city/ocean b-roll. Keyed off the THEME TEXT, not just content_type
    # — fixes "remix de futebol gerou arranha-céu/nuvem".
    _DOMAIN_TERMS = {
        "soccer": [
            "soccer stadium crowd night", "slow motion goal celebration",
            "football players running pitch", "stadium floodlights fans",
            "soccer ball net close up", "fans cheering stadium",
        ],
        "basketball": [
            "basketball arena crowd", "slam dunk slow motion", "basketball court night",
            "basketball players game", "basketball hoop close up", "cheering fans arena",
        ],
        "history": [
            "ancient ruins aerial", "old battlefield landscape", "vintage archival film grain",
            "ancient castle fog", "old map close up", "candle lit stone hall",
        ],
        "space": [
            "earth from space", "galaxy stars timelapse", "planet surface render",
            "rocket launch slow motion", "astronaut floating", "nebula deep space",
        ],
        "nature": [
            "ocean waves close up", "deep forest light rays", "wild animal slow motion",
            "volcano eruption", "mountain range aerial", "thunderstorm clouds",
        ],
        "default": [
            "city skyline aerial", "slow motion crowd", "dramatic clouds time lapse",
            "ocean waves close up", "person walking street", "forest light rays",
            "old documents close up", "stadium lights night", "rain window night",
        ],
    }
    _DOMAIN_KEYWORDS = {
        "soccer": [
            "futebol", "football", "soccer", "gol ", "golaço", "golaco", "jogador",
            "craque", "neymar", "messi", "ronaldo", "cr7", "cristiano", "mbappe",
            "mbappé", "copa", "champions", "libertadores", "penalti", "pênalti",
            "drible", "passe", "partida", "seleção", "selecao", "psg", "barcelona",
            "real madrid", "campeonato", "atacante", "zagueiro", "goleiro", "fifa",
            "estádio", "estadio", "chute", "dribl",
        ],
        "basketball": ["basquete", "basketball", "nba", "lebron", "jordan", "curry", "enterrada", "dunk"],
        # NOTE: "história/historia" is intentionally absent — it means "story" in
        # almost every theme ("a história de...") and would hijack every video.
        "history": [
            "guerra", "batalha", "império", "imperio", "antigo", "faraó", "farao",
            "roma", "egito", "medieval", "segunda guerra", "nazista", "revolução",
            "revolucao", "império romano", "gladiador", "viking", "cavaleiro",
        ],
        "space": [
            "espaço", "espaco", "universo", "planeta", "galáxia", "galaxia", "nasa",
            "astronauta", "marte", "estrela", "cosmos", "buraco negro", "foguete",
        ],
        "nature": [
            "oceano", " mar ", "floresta", "animal", "natureza", "selva", "tubarão",
            "tubarao", "vulcão", "vulcao", "montanha", "tempestade",
        ],
    }

    @classmethod
    def _domain_of(cls, text: str) -> str:
        """Best-effort topic of a theme/title so offline b-roll stays on-theme."""
        t = f" {(text or '').lower()} "
        for domain, kws in cls._DOMAIN_KEYWORDS.items():
            if any(k in t for k in kws):
                return domain
        return "default"

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

        # Pick b-roll from the THEME, not just content_type: a football theme must
        # pull football footage even when the LLM is down and content_type is the
        # neutral "film_recap_ai_images" (what remix uses).
        domain = self._domain_of(f"{theme} {title}")
        is_sport = content_type == "sports_highlights" or domain in ("soccer", "basketball")
        n = 6 if is_sport else 9
        terms = (self._DOMAIN_TERMS["soccer"] if content_type == "sports_highlights"
                 else self._DOMAIN_TERMS.get(domain, self._DOMAIN_TERMS["default"]))
        scenes = []
        # Deterministic fallback (LLM down). Can't know facts, so it stays generic —
        # but avoids the banned filler clichés and keeps anchoring to the theme.
        beats = [
            ("{t} — e tem um detalhe que quase ninguém percebeu.", True),
            ("Pra entender de verdade, olha como cada parte se conecta.", False),
            ("É aqui que {t} fica realmente interessante.", False),
            ("E foi nesse ponto que a virada aconteceu.", True),
            ("Repara nos detalhes — eles mudam como você enxerga {t}.", False),
            ("Poucos sabem o que veio logo depois disso.", False),
            ("Esse foi o momento que ninguém mais esquece.", True),
            ("E é por isso que {t} ainda dá o que falar.", False),
            ("Curtiu? Tem mais sobre {t} aqui no canal.", False),
        ]
        for i in range(n):
            text, hi = beats[i % len(beats)]
            narration = text.format(t=theme)
            scenes.append({
                "narration": narration,
                "visual_prompt": "" if is_sport
                else f"cinematic dramatic scene about {theme}, photorealistic, 4k, moody lighting",
                "visual_query": terms[i % len(terms)],
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
