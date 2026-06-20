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
import copy
import re

from backend.agents.base_agent import AgentError, BaseAgent
from backend.config import settings
from backend import llm

# ── Channel Config v2.1 ─────────────────────────────────────────────────────
CHANNEL_DEFAULTS: dict = {
    "schema_version": "2.1", "platform": "youtube", "language": "pt-BR",
    "channel_name": "",
    "identity": {
        "niche": "", "sub_niches": [], "content_pillars": [], "target_audience": "",
        "persona": {"name": None, "pov": "narrator", "vocabulary_level": "popular",
                    "energy": "medio", "signature_moves": []},
    },
    "voice": {"tone": ["energetico", "credivel"], "narrator_style": "conversacional",
              "tts_voice": "pt-BR-AntonioNeural", "pacing": "medio"},
    "format": {"video_format": "long", "long_duration_target": 360,
               "shorts_duration_target": 35, "preferred_content_types": ["auto"],
               "aspect_ratio_long": "16:9", "aspect_ratio_short": "9:16"},
    "packaging": {
        "title_style": {"mechanisms": ["curiosity_gap", "number", "contrarian"],
                        "max_chars": 60, "must_include_number_when_possible": True,
                        "emoji_policy": "one"},
        "thumbnail_style": {
            "text_overlay": {"max_words": 4, "case": "UPPER",
                             "style": "bold sans, heavy stroke, high contrast"},
            "palette": ["#FF2D2D", "#FFD400", "#0B0B12"],
            "focal_subject": "auto", "emotion_default": "curiosidade",
            "must_complement_title": True, "consistency_anchor": "",
            "art_direction": "high-contrast cinematic, single clear subject, shallow depth of field",
        },
    },
    "retention": {"hook_style": "auto", "retention_target": 0.45,
                  "rehook_interval_hint_sec": 35, "open_loop_required": True,
                  "payoff_required": True},
    "audience_psychology": {
        "emotional_intensity": 0.6, "hook_aggressiveness": 0.6,
        "primary_emotions": ["curiosidade", "surpresa"], "core_desire": "",
        "identification_anchor": "", "stakes_frame": "", "share_drivers": ["moeda_social"],
        "controversy_tolerance": 0.3, "humor_level": 0.2, "forbidden_emotions": [],
    },
    "cta_style": {"profile": "follow_loop", "objective": "watch_next", "placement": "end",
                  "template": "deixa uma pergunta aberta ligada ao tema",
                  "cross_platform_pull": True},
    "visual_style": {
        "image_aesthetic": "", "footage_strategy": "auto", "stock_domains_hint": [],
        "use_face": True, "safe_zone": "right",
        "negative_prompt": "text, watermark, logo, distorted, low quality, extra fingers",
        "color_grade": "alto_contraste", "mood": "epic_cinematic",
        "thumbnail_archetype": "face_reaction", "accent_color": "#FFD400",
        "typography": "heavy_condensed_sans", "face_emotion_bias": "shock",
        "device_overlays": "arrows_circles",
    },
    "music": {"genre_hint": "cinematic_tension", "energy_curve": "build_to_climax",
              "bpm_range": [70, 110], "drop_on_climax": True, "duck_under_voice_db": -12},
    "captions": {"style": "karaoke", "burn_in": True, "max_chars_per_line": 24,
                 "highlight_color": "#FFD400", "position_long": "lower_third",
                 "position_short": "center_safe"},
    "seo": {"primary_keywords": [], "search_angle": "", "tags_count": [15, 25],
            "hashtag_policy": "nicho+amplo", "search_intent": "o que aconteceu",
            "channel_brand_tag": ""},
    "fyp_signals": {"target_completion_rate": 0.7, "rewatch_target": 1.1,
                    "save_ratio_target": 0.02, "share_ratio_target": 0.015,
                    "first_comment_seed": True},
    "guardrails": {
        "forbidden_topics": [], "claims_policy": "facts_only", "extra_instructions": "",
        "banned_cliches": [
            "olá pessoal", "você não vai acreditar", "prepare-se", "segura essa",
            "presta atenção", "hoje eu vou te mostrar", "sem mais delongas",
            "bem-vindos de volta", "você já parou para pensar",
        ],
    },
    "experimentation": {"ab_test_policy": {"title_variants": 3, "thumbnail_variants": 2,
                                           "test_axis": "mechanism",
                                           "winner_metric": "ctr_then_retention"}},
}


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def resolve_channel_config(channel: dict | None = None,
                           per_job_override: dict | None = None) -> dict:
    cfg = _deep_merge(CHANNEL_DEFAULTS, channel or {})
    cfg = _deep_merge(cfg, per_job_override or {})
    cfg = _deep_merge(CHANNEL_DEFAULTS, cfg)
    return cfg


def _band(x) -> str:
    try:
        x = float(x)
    except (TypeError, ValueError):
        x = 0.6
    if x < 0.34:
        return "BAIXA (contido, sóbrio; nada de sensacionalismo)"
    if x < 0.67:
        return "MÉDIA (firme, mas sem exagero; 1 pico controlado)"
    return "ALTA (provocador e intenso, ainda dentro da verdade dos fatos)"


def render_channel_block(cfg: dict | None = None) -> str:
    g = resolve_channel_config(cfg)

    def G(path, default=""):
        cur = g
        for key in path.split("."):
            if not isinstance(cur, dict):
                return default
            cur = cur.get(key, None)
            if cur is None:
                return default
        return cur

    def line(label, val):
        if val in (None, "", [], {}):
            return ""
        if isinstance(val, (list, tuple)):
            val = ", ".join(map(str, val))
        return f"- {label}: {val}\n"

    pillars = ", ".join(
        p.get("name", "") for p in G("identity.content_pillars", []) if p.get("name")
    )
    persona = (
        f'{G("identity.persona.pov", "narrator")}, '
        f'vocabulário {G("identity.persona.vocabulary_level", "popular")}, '
        f'energia {G("identity.persona.energy", "medio")}'
    )

    block = "=== CHANNEL CONFIG (FONTE DE IDENTIDADE — OBEDEÇA) ===\n"
    block += line("Canal", G("channel_name"))
    block += line("Idioma de saída (OBRIGATÓRIO)", G("language", "pt-BR"))
    block += line("Nicho", G("identity.niche"))
    block += line("Pilares de conteúdo", pillars)
    block += line("Público-alvo", G("identity.target_audience"))
    block += line("Persona/POV", persona)
    block += line("Bordões da persona", G("identity.persona.signature_moves"))
    block += line("Tom", G("voice.tone"))
    block += line("Estilo de narração", G("voice.narrator_style"))
    block += line("Ritmo", G("voice.pacing"))
    block += line("Estilo de gancho", G("retention.hook_style", "auto"))
    hint = G("retention.rehook_interval_hint_sec", 35)
    block += (
        f"- Cadência de re-hook: CALCULE R = clamp(D/(n+1), 25, 60) a partir da "
        f"duração-alvo D; n = max(1, round(D/45)). O valor {hint}s é apenas um HINT: "
        f"use-o só se cair dentro de [25,60]; o R calculado sempre vence.\n"
    )
    block += line("Meta de retenção", G("retention.retention_target"))
    block += line("Objetivo do CTA", G("cta_style.objective"))
    block += line("Molde de CTA", G("cta_style.template"))
    block += line("Mecanismos de título permitidos", G("packaging.title_style.mechanisms"))
    block += line("Search angle", G("seo.search_angle"))
    block += line("Intensidade emocional (alvo)",
                  _band(G("audience_psychology.emotional_intensity", 0.6)))
    block += line("Ousadia do gancho (teto)",
                  _band(G("audience_psychology.hook_aggressiveness", 0.6)))
    block += line("Emoções-alvo", G("audience_psychology.primary_emotions"))
    fe = G("audience_psychology.forbidden_emotions")
    if fe:
        block += f"- EMOÇÕES PROIBIDAS (rejeição automática se evocadas): {', '.join(fe)}\n"
    ft = G("guardrails.forbidden_topics")
    if ft:
        block += f"- PROIBIDO falar sobre: {', '.join(ft)}\n"
    bc = G("guardrails.banned_cliches")
    if bc:
        block += f"- CLICHÊS PROIBIDOS (rejeição automática): {', '.join(bc)}\n"
    block += line("Instruções extras", G("guardrails.extra_instructions"))
    block += "=== FIM DO CHANNEL CONFIG ===\n"
    return block


_MARKERS_RE = re.compile(r"\[(RE-HOOK|PATTERN-INT|LOOP-OPEN:[^\]]+|LOOP-PAY:[^\]]+)\]")


def build_tts_text(scenes: list) -> str:
    """Strip inline direction markers, leaving only the speakable narration text."""
    raw = " ".join(
        s.get("narration", "")
        for s in sorted(scenes, key=lambda s: s.get("index", 0))
        if s.get("narration")
    )
    raw = _MARKERS_RE.sub("", raw)
    raw = re.sub(r"\[PAUSA\]", " ... ", raw)
    raw = re.sub(r"\[ENFASE\]\{([^}]*)\}", r"\1", raw)
    raw = re.sub(r"\[[^\]]*\]", "", raw)
    return re.sub(r"\s{2,}", " ", raw).strip()


# ── System prompt (v2.1) ─────────────────────────────────────────────────────
SYSTEM = (
    "Você é o roteirista-chefe de um canal — referência mundial em RETENÇÃO. "
    "Escreva na voz definida no CHANNEL CONFIG (persona, tom, ritmo, pilares). "
    "Conteúdo 100% original. Idioma obrigatório: {lang}. Responda SOMENTE com JSON válido.\n\n"
    "=== MOTOR DE GANCHO (COLD-OPEN) — OBRIGATÓRIO ===\n"
    "A primeira cena (scenes[0]) é o gancho e decide o vídeo nos 3 primeiros segundos.\n"
    "MECANISMO — use EXATAMENTE UM, ditado por hook_style do canal:\n"
    "- curiosity_gap: nomeie o RESULTADO, esconda a CAUSA.\n"
    "- bold_claim: afirmação contraintuitiva e específica.\n"
    "- high_stakes: explicite o que está em jogo (valor, título, vida).\n"
    "- negation: 'quase ninguém percebeu [detalhe concreto]'.\n"
    "- numbered: promessa numerada ('3 coisas — a 3ª parece impossível').\n"
    "- in_medias_res: abra no instante mais quente, sem contexto prévio.\n"
    "Se hook_style='auto': film_recap→in_medias_res, sports→high_stakes, top_list→numbered, "
    "explainer→curiosity_gap, true_crime→in_medias_res, reaction→bold_claim, "
    "reddit→in_medias_res, motivational→bold_claim.\n"
    "REGRAS DURAS DO GANCHO (vídeo LONG): 1ª frase falável = 8-14 palavras, ZERO aquecimento; "
    ">=1 elemento CONCRETO dos fatos; abra loop que SÓ fecha no final; gancho visual (overlay) = "
    "2-5 palavras MAIÚSCULAS, NUNCA repetindo o áudio; NÃO empilhe mecanismos; NÃO use saudações. "
    "Respeite a OUSADIA DO GANCHO (teto) do CHANNEL CONFIG. Marque scenes[0].is_highlight=true.\n"
    "=== FIM DO MOTOR DE GANCHO ===\n\n"
    "=== ARQUITETURA DE RETENÇÃO (OBRIGATÓRIA) ===\n"
    "Você projeta uma CURVA DE RETENÇÃO. Calcule a partir da duração-alvo D:\n"
    "n_rehooks = max(1, round(D/45)); R = clamp(D/(n_rehooks+1), 25, 60); P ≈ R/2.\n"
    "MAPA: (1) 0-3s GANCHO is_highlight=true; (2) 3-15s stakes + [LOOP-OPEN:1]; "
    "(3) CORPO em ONDAS — a cada ~R s um [RE-HOOK], no ponto 50-65% o mais forte; "
    "a cada ~P s um [PATTERN-INT]; cada onda traz >=1 fato NOVO; "
    "(4) CLÍMAX (~15-20% finais) — pague o gancho com [LOOP-PAY:id], reserve o melhor fato; "
    "(5) CTA-LOOP (~5-8% finais) — ligado à curiosidade do tema, NUNCA 'segue o canal' solto. "
    "TODO [LOOP-OPEN:id] PRECISA de [LOOP-PAY:id] antes do CTA.\n"
    "MARCADORES (em scenes[].narration, nunca falados): "
    "[RE-HOOK] [PAUSA] [ENFASE]{texto} [LOOP-OPEN:id] [LOOP-PAY:id] [PATTERN-INT].\n"
    "=== FIM DA ARQUITETURA DE RETENÇÃO ===\n\n"
    "=== REGRA DE FONTE ÚNICA DA NARRAÇÃO ===\n"
    "narration_text = concatenação de scenes[].narration COM marcadores (auditoria). "
    "tts_text = mesma concatenação JÁ LIMPA: remove [RE-HOOK]/[PATTERN-INT]/[LOOP-OPEN/PAY]; "
    "troca [PAUSA] por ' ... '; em [ENFASE]{x} mantém só x. NUNCA colchetes em tts_text.\n"
    "=== FIM ===\n\n"
    "CLICHÊS PROIBIDOS (rejeição automática): 'olá pessoal', 'você não vai acreditar', "
    "'prepare-se', 'o que vem agora muda tudo', 'presta atenção', 'segura essa', "
    "'hoje eu vou te mostrar', 'bem-vindos de volta', 'você já parou para pensar', "
    "'espera você precisa ver'. Responda SOMENTE com JSON válido, sem comentários."
)

# Readable language name for the prompt (a channel may store en-US, es, etc.).
_LANG_NAMES = {
    "pt": "português do Brasil", "en": "inglês (English)", "es": "espanhol (Español)",
    "fr": "francês (Français)", "de": "alemão (Deutsch)", "it": "italiano (Italiano)",
}


def _lang_name(code: str) -> str:
    return _LANG_NAMES.get((code or "pt").lower().split("-")[0], code or "português do Brasil")

# Kept for backward-compat imports from other modules; logic is now in SYSTEM (v2.1).
RETENTION_RULES = ""

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
    # Scripting is the content source — retry fast (LLM rate limits recover in
    # seconds) instead of the default 30s/2min/5min, so a real script is produced
    # without long stalls before the gate gives up.
    backoffs = [8, 20, 45]

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

        # A "momento em alta" video is factual and time-sensitive. If grounding was only
        # TRANSIENTLY unavailable (Gemini 429 storm — not "skipped by design"), writing an
        # ungrounded script is exactly what produced the off-topic moment the user saw.
        # Reject so BaseAgent retries (and _job_retry_errored resurrects later when the
        # quota reopens) instead of shipping a generic, off-theme script.
        if (research.get("unavailable") and not settings.allow_offline_script
                and self.ctx_get("is_trending")):
            self.emit("progress", "Grounding do momento indisponível — re-tentando (sem publicar genérico)", progress=18)
            raise AgentError("Grounding indisponível para vídeo do momento — re-tentando para conteúdo factual")

        self.emit("progress", f"Gerando roteiro ({content_type}, {video_format}, modo={mode})", progress=20)

        try:
            script = await self._via_llm(theme, title, mode, content_type, style_dna, language, research, video_format)
        except llm.LLMUnavailable as exc:
            # NEVER ship the hollow generic template (it throws away the researched
            # facts -> "vídeo escroto"). Reject so BaseAgent retries; if it keeps
            # failing the job errors and is retried later, when the LLM is back.
            if not settings.allow_offline_script:
                raise AgentError("LLM indisponível — roteiro real não pôde ser gerado (não publico genérico)") from exc
            self.emit("progress", "Sem LLM disponível — usando roteiro offline (placeholder)", progress=30)
            script = self._offline(theme, title, content_type, language)

        # A syntactically-valid LLM JSON whose "scenes" is the wrong shape is also
        # unusable — retry rather than fall back to the generic template.
        sc_list = script.get("scenes") if isinstance(script, dict) else None
        if not (isinstance(sc_list, list) and sc_list and all(isinstance(s, dict) for s in sc_list)):
            if not settings.allow_offline_script:
                raise AgentError("LLM retornou roteiro inválido — repetindo para gerar conteúdo real")
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
        # Decide stock-vs-AI per the topic domain (covers BOTH the LLM and offline
        # paths): niche topics with no real stock footage get ON-THEME AI images so
        # the visuals match the theme instead of a random fuzzy stock clip.
        _prefer_ai = self._domain_of(f"{theme} {title}") not in self._FOOTAGE_RICH
        for sc in scenes:
            sc.setdefault("ai_image", _prefer_ai)
        if content_type != "quote_viral":
            script["narration_text"] = " ".join(s["narration"] for s in scenes if s.get("narration")).strip()
            # tts_text: clean version without inline markers — the only text the TTS speaks.
            # If the LLM already generated it clean, validate; else derive it here.
            tts = (script.get("tts_text") or "").strip()
            if "[" in tts or not tts:
                tts = build_tts_text(scenes)
            script["tts_text"] = tts
        else:
            script["narration_text"] = ""
            script["tts_text"] = ""
        script.setdefault("on_screen_text", script.get("on_screen_text", []))
        titles = script.get("title_options") or [title or theme]
        script["title_options"] = titles[:3]
        script["title"] = script.get("title") or titles[0]
        script.setdefault("estimated_duration", self._estimate_duration(script, content_type))
        script.setdefault("seo_keywords", script.get("seo_keywords", []))

        # Native short: keep it tight (vertical <60s). Cap scenes and recompute.
        script["format"] = video_format
        if video_format == "short" and len(scenes) > 6:
            # Trim to a Short WITHOUT decapitating the payoff: a blind scenes[:6]
            # drops the ending (payoff + CTA) — the completion-rate signal that most
            # drives the Shorts feed. Keep the hook (first) + resolution (last two),
            # then fill the middle preferring is_highlight scenes, preserving order.
            n = len(scenes)
            keep = {0, n - 2, n - 1}
            middle = list(range(1, n - 2))
            ordered = ([i for i in middle if scenes[i].get("is_highlight")]
                       + [i for i in middle if not scenes[i].get("is_highlight")])
            for i in ordered:
                if len(keep) >= 6:
                    break
                keep.add(i)
            scenes = [scenes[i] for i in sorted(keep)]
            script["scenes"] = scenes
            if content_type != "quote_viral":
                script["narration_text"] = " ".join(
                    s.get("narration", "") for s in scenes if s.get("narration")).strip()
            script["estimated_duration"] = self._estimate_duration(script, content_type)

        # QUALITY GATE: never let a generic/templated script (no real content) through.
        # If it reads as filler, raise so BaseAgent retries and the LLM produces real,
        # fact-based narration instead of the hollow placeholder.
        if not settings.allow_offline_script:
            from backend.agents.quality_gate import assess

            forbidden = ((self.ctx_get("channel_config") or {})
                         .get("guardrails", {}).get("forbidden_topics") or [])
            ok, reason = assess(script, topic=theme, forbidden_topics=forbidden)
            if not ok:
                self.emit("progress", f"Roteiro rejeitado pelo controle de qualidade: {reason}", progress=30)
                raise AgentError(f"Roteiro genérico rejeitado: {reason}")

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
                f"content_type={style_dna.get('content_type')}.\n"
            )

        facts = (research or {}).get("facts", "").strip()
        # Time-sensitive content (live scores, breaking/"do momento") is dangerous
        # to write ungrounded — an unverified date/result is misinformation.
        # Evergreen content (tutorials, lists, curiosities, recaps) is NOT: the
        # model's own general knowledge IS the content, so ungrounded ≠ "stay
        # vague". The old code treated EVERY ungrounded topic as time-sensitive,
        # forcing hollow hedging ("é importante", "muda tudo") on tutorial/tech
        # videos — the main cause of generic output.
        _TIME_SENSITIVE = {"sports_highlights", "reaction_commentary"}
        is_time_sensitive = content_type in _TIME_SENSITIVE or bool(self.ctx_get("is_trending"))
        if (research or {}).get("grounded") and facts:
            facts_block = (
                "\n\n=== FATOS VERIFICADOS (FONTE DA VERDADE) ===\n"
                "Baseie TODA a narração SOMENTE nestes fatos reais. É TERMINANTEMENTE "
                "PROIBIDO inventar placares, datas, nomes, números ou resultados. Se "
                "uma informação não estiver aqui, NÃO a afirme.\n" + facts + "\n"
                "=== FIM DOS FATOS ===\n"
            )
        elif is_time_sensitive:
            facts_block = (
                "\n\n[ATENÇÃO] SEM FATOS VERIFICADOS para este tema sensível ao tempo. "
                "É PROIBIDO inventar resultados, placares, datas, nomes, números ou dizer "
                "que algo 'aconteceu hoje/ontem'. Se o tema pede um resultado/evento recente "
                "que você NÃO pode confirmar, não finja saber: fale da expectativa, do contexto "
                "e da importância de forma geral e atemporal, deixando claro que o desfecho não "
                "é afirmado.\n"
            )
        else:
            facts_block = (
                "\n\n[CONTEÚDO EVERGREEN] Não há pesquisa da web, mas este é um tema de "
                "CONHECIMENTO GERAL (tutorial, dica, lista, curiosidade, recap) — não é "
                "notícia. USE seu conhecimento para entregar informação CONCRETA e ESPECÍFICA: "
                "passos reais, configurações, números, nomes, exemplos acionáveis. É PROIBIDO "
                "ser vago ou genérico ('é importante', 'muda tudo', 'otimização é essencial') — "
                "entregue o COMO, com detalhe que o espectador consiga aplicar. Evite apenas "
                "afirmar eventos/datas/resultados RECENTES que você não pode confirmar.\n"
            )

        # Channel block — read from ctx if the orchestrator stored it; else minimal.
        channel_cfg = self.ctx_get("channel_config") or {}
        if language:
            channel_cfg.setdefault("language", language)
        channel_block = render_channel_block(channel_cfg)

        if video_format == "short":
            length_block = (
                "FORMATO: SHORT VERTICAL 9:16. Seja MUITO conciso: 4 a 6 cenas, 25 a 45 "
                "SEGUNDOS no total. Gancho (≤8 palavras) imediato no 1º segundo, ritmo "
                "rápido, frases curtas e punchy. Corte tudo que não prende."
            )
        else:
            length_block = (
                "IMPORTANTE (duração): gere o roteiro COMPLETO atingindo a contagem de "
                "palavras/cenas alvo do tipo acima — vídeos curtos demais são rejeitados. Não resuma."
            )
        # HARD mandate: concrete STORY over vague praise. The free LLM tends to fill
        # short videos with empty adjectives ("dominou os campos", "habilidade sem
        # igual", "gols impressionantes") — the quality gate REJECTS that and the job
        # regenerates, so demand specificity up front to get it right the first time.
        story_block = (
            "\n\n=== HISTÓRIA E ESPECIFICIDADE (OBRIGATÓRIO — senão o roteiro é REJEITADO) ===\n"
            "Conte uma HISTÓRIA real, não um amontoado de elogios. Mesmo em vídeo curto:\n"
            "- ÂNCORAS CONCRETAS: cite nomes próprios, lugares, números, datas, placares, "
            "recordes REAIS (use os FATOS abaixo). Cada cena precisa de pelo menos UM detalhe "
            "concreto e verificável — não frases genéricas que serviriam para qualquer tema.\n"
            "- ARCO: setup (quem/quando/o que estava em jogo) -> tensão (a virada, o conflito, "
            "o número improvável) -> PAGAMENTO concreto (o que de fato aconteceu, com o detalhe "
            "específico). NÃO termine no vago.\n"
            "- PROIBIDO recheio vago: 'incrível', 'sem igual', 'impressionante', 'inesquecível', "
            "'dominou os campos', 'talento sem igual', 'inspirou gerações' — SÓ valem se "
            "acompanhados de um fato concreto que os comprove. Adjetivo sem fato = lixo.\n"
            "- Se NÃO houver fato concreto disponível para o tema, ancore no CONTEXTO real "
            "(história, regras, por que importa) em vez de inventar ou encher de elogio.\n"
            "=== FIM ===\n"
        )

        try:
            from backend.agents.coach import playbook_prompt_block
            coach_block = playbook_prompt_block(content_type, video_format)
        except Exception:
            coach_block = ""

        # Live learning signal — what already performed on this channel (from real
        # analytics). Empty until enough measured videos exist. See performance.py.
        perf_block = self.ctx_get("performance_insights") or ""

        is_narrated = content_type != "quote_viral"
        schema_extra = (
            '  "has_narration": true,\n'
            '  "narration_text": "concatenação COM marcadores (auditoria)",\n'
            '  "tts_text": "concatenação LIMPA sem colchetes — texto que o TTS fala",\n'
            '  "retention_map": {\n'
            '    "duration_target_s": 240, "n_rehooks": 5, "rehook_interval_s": 40,\n'
            '    "rehook_scene_indices": [2,5,8,11,14], "valley_rehook_scene_index": 8,\n'
            '    "loops": [{"id":1,"open_scene":0,"pay_scene":14,"promise":"..."}],\n'
            '    "payoff_fact": "qual fato concreto paga o gancho no clímax"\n'
            "  },\n"
        ) if is_narrated else '  "has_narration": false,\n'

        prompt = f"""{channel_block}

=== TEMA DESTE VÍDEO ===
Tema: "{theme}"
Modo: {mode}
content_type: {content_type}
video_format: {video_format}

{guide}{style_hint}{coach_block}{perf_block}{facts_block}{story_block}
{length_block}

Responda com JSON neste formato EXATO:
{{
  "title_options": ["...", "...", "..."],
  "tone": "...",
  "content_type": "{content_type}",
  "estimated_duration": <segundos>,
{schema_extra}  "scenes": [
    {{"index": 0, "narration": "...com marcadores inline...", "visual_prompt": "EN cinematic prompt", "visual_query": "2-5 EN stock keywords", "is_highlight": true}}
  ],
  "on_screen_text": ["..."],
  "seo_keywords": ["...", "..."]
}}
REGRA DOS VISUAIS:
- "visual_query": 2-5 palavras-chave CONCRETAS em inglês de algo REAL e filmável.
  Evite nomes próprios/marcas (não retornam stock footage).
- "visual_prompt": descrição cinematográfica em inglês — fallback de imagem IA.
- quote_viral: "narration" vazio, conteúdo em "on_screen_text".
GERE narration_text (COM marcadores) E tts_text (LIMPO, sem nenhum colchete)."""
        from backend.agents.style_guide import with_style
        # NB: use .replace, not .format — SYSTEM contains literal prompt braces
        # like [ENFASE]{texto} and {x} that .format() would treat as fields
        # (KeyError 'texto'). Only {lang} is a real placeholder.
        system = with_style(SYSTEM.replace("{lang}", _lang_name(language)))
        # 3000 fits even a 14-scene recap (target ~400-700 words ≈ 2.2-2.6k tokens
        # incl. JSON + visual prompts); 4500 was padding that burned the scarce free
        # quota faster. Don't drop below ~2800 or long scripts truncate.
        return await llm.complete_json(prompt, system=system, max_tokens=3000)

    @classmethod
    def _detect_content_type(cls, theme: str) -> str:
        """Keyword-based auto-detection — picks the best content_type for a theme."""
        t = f" {(theme or '').lower()} "
        # Sports: reuse the existing domain keywords (most specific signal), plus
        # explicit extras the domain map misses — American football / NFL / generic
        # "football" were falling through to film_recap (the off-theme bug).
        sport_kws = (cls._DOMAIN_KEYWORDS.get("soccer", [])
                     + cls._DOMAIN_KEYWORDS.get("basketball", [])
                     + ["futebol americano", "american football", "nfl", "super bowl",
                        "futebol", "vôlei", "volei", "tênis", "tenis", "mma", "ufc",
                        "fórmula 1", "formula 1", "f1", "olimpíada", "olimpiada"])
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
        "gaming": [
            "gaming setup rgb lights", "person playing video game", "game controller close up",
            "esports arena crowd", "computer screen gameplay neon", "gamer reacting headset",
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
        "gaming": [
            "roblox", "minecraft", "fortnite", "free fire", "gta", "valorant", "league of legends",
            "videogame", "video game", "gameplay", "gamer", "jogo", "jogos", "jogar", "console",
            "playstation", "xbox", "nintendo", "fps", "rpg", "skin", "robux",
        ],
        "nature": [
            "oceano", " mar ", "floresta", "animal", "natureza", "selva", "tubarão",
            "tubarao", "vulcão", "vulcao", "montanha", "tempestade",
        ],
    }

    # Domains that DO have matching real stock footage. Everything else (gaming,
    # brands, specific people, niche topics) gets ON-THEME AI images instead of a
    # fuzzy/irrelevant stock clip.
    _FOOTAGE_RICH = {"soccer", "basketball", "space", "history", "nature"}

    @classmethod
    def _domain_of(cls, text: str) -> str:
        """Best-effort topic of a theme/title so offline b-roll stays on-theme."""
        t = f" {(text or '').lower()} "
        for domain, kws in cls._DOMAIN_KEYWORDS.items():
            if any(k in t for k in kws):
                return domain
        return "default"

    _TITLE_STOP = {
        "the", "a", "an", "of", "to", "in", "on", "for", "and", "or", "is", "are",
        "most", "top", "best", "how", "why", "what", "your", "you", "with",
        "melhores", "melhor", "como", "por", "que", "os", "as", "de", "do", "da",
        "dos", "das", "mais", "um", "uma", "no", "na", "sobre", "pra", "para",
    }

    @classmethod
    def _title_keywords(cls, text: str) -> str:
        """The theme's own salient words — used as the stock query for niche topics so
        the search misses and the pipeline draws an ON-THEME AI image instead."""
        import re

        words = re.findall(r"[A-Za-zÀ-ÿ0-9]+", text or "")
        kw = [w for w in words if w.lower() not in cls._TITLE_STOP and not w.isdigit() and len(w) > 2]
        return " ".join(kw[:4]).strip() or (text or "").strip()[:40] or "cinematic abstract"

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
        # Footage-rich domains have real matching stock — use the curated terms.
        # Anything else (gaming, brands, people, niche topics) won't exist on stock
        # banks; using the THEME's own keywords makes the stock search miss, so the
        # pipeline falls back to an ON-THEME AI image instead of grabbing a random
        # generic clip (what made "Roblox" videos show ocean/forest footage).
        title_kw = self._title_keywords(title or theme)
        if content_type == "sports_highlights":
            terms = self._DOMAIN_TERMS["soccer"]
        elif domain in self._FOOTAGE_RICH:
            terms = self._DOMAIN_TERMS[domain]
        else:
            terms = [title_kw]
        angles = ["wide establishing shot", "dramatic close up", "dynamic action angle",
                  "moody atmospheric wide shot", "vibrant colorful scene", "epic hero shot"]
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
                # Vary the angle per scene so AI fallback images differ (the image
                # seed is derived from this prompt) while staying anchored to the theme.
                "visual_prompt": "" if is_sport
                else f"cinematic scene about {theme}, {angles[i % len(angles)]}, photorealistic, 4k, moody lighting",
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
            "_offline": True,  # flag so the quality gate can block this placeholder
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
