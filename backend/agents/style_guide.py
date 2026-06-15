"""
Shared quality "brain" injected into every script/hook/SEO prompt.

Distilled from docs/MANUAL_DE_QUALIDADE.md (the full editor-chief manual produced
by the YouTube/SEO/storytelling specialists). Kept concise on purpose so it can be
prepended to system prompts — including the small growth-agent calls — without
blowing the token budget. The single goal: stop "roteiro brega" (cheesy, generic,
AI-sounding scripts) and force concrete, retention-first writing in PT-BR.
"""
from __future__ import annotations

# Hard ban-list — these phrases scream "AI/brega" and must never appear.
BANNED_PHRASES = [
    "espera, você precisa ver",
    "você não vai acreditar",
    "prepare-se",
    "o que vem agora muda tudo",
    "isso é mais profundo do que parece",
    "tudo começou de um jeito que ninguém esperava",
    "as consequências foram imediatas",
    "segura essa",
    "presta atenção",
    "hoje eu vou te mostrar",
    "nesse vídeo a gente",
    "bem-vindos de volta",
    "sem mais delongas",
    "você já parou para pensar",
]

# Prepended to the system prompt of script/hook/retention/packaging/SEO agents.
STYLE_GUIDE = (
    "\n=== MANUAL DE QUALIDADE (OBRIGATÓRIO — ANTI-BREGA) ===\n"
    "Você escreve português brasileiro coloquial, energético e CRÍVEL — nunca cringe, "
    "brega, genérico ou com cara de texto gerado por máquina. Especificidade vence "
    "intensidade: um fato concreto vale mais que dez adjetivos gritados.\n"
    "GANCHO (0-3s): a 1ª frase falável tem 8-14 palavras (Shorts ≤8), zero aquecimento, "
    "e abre com o elemento MAIS concreto (número, nome, valor, data, placar). Use UM "
    "mecanismo só: lacuna de curiosidade, afirmação contraintuitiva, aposta alta, "
    "negativa ('quase ninguém percebeu...') ou promessa numerada. Nada de empilhar.\n"
    "RETENÇÃO: o gancho abre um loop que SÓ fecha no final (payoff real). Plante uma "
    "micro-tensão nova a cada ~30-45s. Toda frase entrega informação, tensão ou avanço — "
    "zero enrolação. CTA final reconecta e empurra pro próximo vídeo.\n"
    "VERDADE: só afirme o que está nos fatos verificados (research). NUNCA invente "
    "número, nome, placar ou data pra parecer mais forte. Promessa = entrega.\n"
    "PROIBIDO (rejeição automática) usar frases clichê/brega como: "
    + "; ".join(f'\"{p}\"' for p in BANNED_PHRASES[:9])
    + ".\n"
    "Não aquecer, não usar CAPS gritado no lugar de fato, não repetir no overlay a "
    "frase do áudio, não usar overlay genérico fixo ('VOCÊ VIU ISSO?').\n"
    "=== FIM DO MANUAL ===\n"
)


def with_style(system: str | None) -> str:
    """Prepend the quality guide to an agent's system prompt."""
    return (STYLE_GUIDE + "\n" + system) if system else STYLE_GUIDE
