"""
Coach agents — the "YouTube performance brain" that TEACHES the scriptwriter.

Two specialists author a persistent playbook (assets/knowledge/script_playbook.json):
  * RetentionCoachAgent  -> what keeps viewers WATCHING to the end
  * EngagementCoachAgent -> what makes viewers LIKE / comment / subscribe

The scriptwriter loads this playbook on every run and injects the relevant slice
into its prompt — so the "brain" is guided by accumulated best-practice knowledge
WITHOUT any per-video LLM cost and without fine-tuning the model. The playbook can
be refreshed/enriched (LLM optional) and, later, refined from real analytics.

Build/refresh the file:  python -m backend.agents.coach
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from backend.agents.base_agent import BaseAgent
from backend.config import settings

PLAYBOOK_PATH = settings.abs_path(settings.assets_dir) / "knowledge" / "script_playbook.json"

# --- RetentionCoach: keep people WATCHING ---
RETENTION_KNOWLEDGE = {
    "principles": [
        "Os 3 primeiros segundos decidem a maior parte da retenção: comece no PICO, sem introdução.",
        "Abra um ciclo (pergunta/promessa) no gancho e só feche no fim — isso segura até o final.",
        "Sem tempo morto: cada frase entrega informação, tensão ou avanço da história.",
        "Especificidade prende: número, nome e detalhe concreto sempre > frase genérica.",
        "Micro-ganchos a cada 15-20s ('mas o que ninguém viu foi...') re-seguram quem ia sair.",
        "Pattern interrupt: troque ritmo/ângulo/visual antes que o cérebro relaxe.",
        "Entregue a recompensa que o gancho prometeu — frustrar o gancho mata o canal a longo prazo.",
        "Frases curtas, voz ativa, sem jargão — como se falasse com UMA pessoa.",
    ],
    "hook_formulas": [
        "Resultado chocante primeiro: '{fato surpreendente} — e quase ninguém percebeu.'",
        "Lacuna de curiosidade: 'Tem um detalhe em {tema} que muda tudo.'",
        "Aposta/risco: 'Isso quase {consequência grande} — veja como.'",
        "Promessa de valor rápido: 'Em 30s você entende {tema} melhor que 99%.'",
    ],
    "banned_phrases": [
        "Espera, você precisa ver", "Tudo começou de um jeito que ninguém esperava",
        "isso é mais profundo do que parece", "as consequências foram imediatas",
        "o que vem agora muda tudo", "prepare-se", "você não vai acreditar",
        "sente-se e relaxe", "neste vídeo vamos falar sobre", "sem mais delongas",
    ],
}

# --- EngagementCoach: make people LIKE / comment / subscribe ---
ENGAGEMENT_KNOWLEDGE = {
    "principles": [
        "Gatilho emocional claro (surpresa, indignação, orgulho, curiosidade) gera like e comentário.",
        "Faça UMA pergunta que divide opinião para puxar comentário ('você concorda?').",
        "CTA ligado ao conteúdo e colocado no momento do PICO — não um 'inscreva-se' solto no fim.",
        "Relatabilidade: conecte o tema à vida/realidade de quem assiste.",
        "Crie torcida: um lado pra apoiar e um vilão/obstáculo pra rejeitar.",
        "Prometa continuação ('parte 2') quando o tema permite — gera retorno e follow.",
        "Fale a língua do nicho: futebol soa como torcedor, não como repórter de TV.",
    ],
    "cta_formulas": [
        "'Comenta {pergunta divisiva} que eu respondo.'",
        "'Segue que a parte 2 de {tema} vem ainda mais surreal.'",
        "'Salva esse pra não esquecer {benefício}.'",
    ],
}

BY_CONTENT_TYPE = {
    "sports_highlights": "Fale como torcedor. Gancho = o lance mais absurdo. Narração curta e empolgada, com nome do craque e do momento exato.",
    "film_recap_ai_images": "Conte como história com tensão crescente; pré-revele só o suficiente pra criar dúvida e segure o clímax.",
    "top_list_ranking": "Anuncie o número de cada item, suba o suspense até o #1 e guarde o melhor pro final.",
    "explainer_curiosity": "Abra com a pergunta intrigante, explique passo a passo e termine com um fato-surpresa.",
    "true_crime_mystery": "Tom sombrio e investigativo; abra na cena do crime, espalhe pistas e deixe o mistério no ar.",
    "reaction_commentary": "Hot take claro e opinativo; gancho = 'você viu isso?', e puxe o contraponto pra gerar debate.",
    "reddit_story": "Primeira pessoa, ritmo de fofoca, reviravolta no meio; legendas grandes são o foco.",
    "motivational_speech": "Segunda pessoa ('você consegue'), realidade dura no gancho, crescendo até a frase de impacto.",
    "quote_viral": "Uma frase só, provocativa, que cabe num print. O texto na tela É o conteúdo.",
}

BY_FORMAT = {
    "short": "Gancho no 1º frame (zero intro). UMA ideia só. Cortes rápidos. Loop final que puxa de volta pro começo.",
    "long": "Gancho forte + um mapa do que vem (sem entregar tudo). Re-ganchos no início de cada bloco.",
}


def _default_playbook() -> dict:
    return {
        "version": 1,
        "authored_by": ["retention_coach", "engagement_coach"],
        "retention": RETENTION_KNOWLEDGE,
        "engagement": ENGAGEMENT_KNOWLEDGE,
        "by_content_type": BY_CONTENT_TYPE,
        "by_format": BY_FORMAT,
    }


_CACHE: dict | None = None


def load_playbook() -> dict:
    """Read the persisted playbook (cached). Falls back to the in-code defaults so
    the scriptwriter is never left without its brain, even if the file is missing."""
    global _CACHE
    if _CACHE is not None:
        return _CACHE
    try:
        if PLAYBOOK_PATH.exists():
            _CACHE = json.loads(PLAYBOOK_PATH.read_text(encoding="utf-8"))
            return _CACHE
    except Exception:
        pass
    _CACHE = _default_playbook()
    return _CACHE


def playbook_prompt_block(content_type: str, video_format: str = "long") -> str:
    """Compact slice of the playbook to inject into the scriptwriter prompt —
    the engagement + per-type + per-format knowledge the coaches taught."""
    try:
        pb = load_playbook()
        eng = (pb.get("engagement") or {}).get("principles", [])[:4]
        hooks = (pb.get("retention") or {}).get("hook_formulas", [])[:2]
        ctas = (pb.get("engagement") or {}).get("cta_formulas", [])[:2]
        type_tip = (pb.get("by_content_type") or {}).get(content_type, "")
        fmt_tip = (pb.get("by_format") or {}).get(video_format, "")
        lines = ["\n=== MANUAL DO CANAL (treinado pelos coaches de retenção/engajamento) ==="]
        if type_tip:
            lines.append(f"ESTE TIPO ({content_type}): {type_tip}")
        if fmt_tip:
            lines.append(f"ESTE FORMATO ({video_format}): {fmt_tip}")
        if eng:
            lines.append("ENGAJAMENTO (like/comentário/follow): " + " ".join(f"- {p}" for p in eng))
        if hooks:
            lines.append("FÓRMULAS DE GANCHO: " + " | ".join(hooks))
        if ctas:
            lines.append("FÓRMULAS DE CTA: " + " | ".join(ctas))
        lines.append("=== FIM DO MANUAL ===\n")
        return "\n".join(lines)
    except Exception:
        return ""


class RetentionCoachAgent(BaseAgent):
    """Specialist that owns the WATCH-TIME knowledge fed into the script brain."""

    name = "retention_coach"

    async def run(self, **_) -> dict:
        self.emit("progress", "Conhecimento de RETENÇÃO consolidado", progress=100)
        return RETENTION_KNOWLEDGE


class EngagementCoachAgent(BaseAgent):
    """Specialist that owns the LIKE/COMMENT/SUBSCRIBE knowledge for the brain."""

    name = "engagement_coach"

    async def run(self, **_) -> dict:
        self.emit("progress", "Conhecimento de ENGAJAMENTO consolidado", progress=100)
        return ENGAGEMENT_KNOWLEDGE


async def build_playbook() -> dict:
    """Run both coaches and persist the combined playbook to disk."""
    retention = await RetentionCoachAgent(job_id=None, emit=False).execute()
    engagement = await EngagementCoachAgent(job_id=None, emit=False).execute()
    playbook = {
        "version": 1,
        "authored_by": ["retention_coach", "engagement_coach"],
        "retention": retention,
        "engagement": engagement,
        "by_content_type": BY_CONTENT_TYPE,
        "by_format": BY_FORMAT,
    }
    PLAYBOOK_PATH.parent.mkdir(parents=True, exist_ok=True)
    PLAYBOOK_PATH.write_text(json.dumps(playbook, ensure_ascii=False, indent=2), encoding="utf-8")
    global _CACHE
    _CACHE = playbook
    return playbook


if __name__ == "__main__":
    pb = asyncio.run(build_playbook())
    print(f"[OK] Playbook gravado em {PLAYBOOK_PATH}")
    print(f"     retenção: {len(pb['retention']['principles'])} princípios | "
          f"engajamento: {len(pb['engagement']['principles'])} princípios | "
          f"tipos: {len(pb['by_content_type'])}")
