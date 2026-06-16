"""
Quality gate — BLOCKS generic/templated/vague scripts from ever becoming a video.

The rule (per the user): a theme must produce REAL, specific content — true facts,
names, dates, numbers, a story — not hollow praise. Two failure modes are blocked:

1. The OFFLINE template / banned clichés ("a história fica realmente interessante…",
   "tem um detalhe que ninguém percebeu…") — emitted when the LLM is unavailable.
2. VAGUE FILLER — a script that is only generic praise ("dominou os campos",
   "habilidade sem igual", "gols impressionantes") with ZERO concrete anchor (no
   real name, number, place or date). That is the "sem história, sem contexto,
   sem pão nenhum" the user complained about.

Rejected scripts make the job retry → the LLM produces real, fact-grounded content
instead of shipping garbage. Real, specific scripts pass untouched.
"""
from __future__ import annotations

import re

from backend.agents.style_guide import BANNED_PHRASES

# Distinctive fragments of the offline template + generic AI filler. A real,
# fact-grounded script does not lean on these.
_GENERIC_MARKERS = [
    "tem um detalhe que quase ninguém percebeu",
    "olha como cada parte se conecta",
    "fica realmente interessante",
    "foi nesse ponto que a virada aconteceu",
    "eles mudam como você enxerga",
    "poucos sabem o que veio logo depois",
    "o momento que ninguém mais esquece",
    "ainda dá o que falar",
    "você precisa ver o que rolou",
    "o que vem agora muda",
    "pra entender de verdade",
]
_ALL_MARKERS = _GENERIC_MARKERS + [p.lower() for p in BANNED_PHRASES]

# Minimum spoken words. A script below this for its format/type is stunted — too
# thin to tell any story — and must be regenerated. Conservative floors (well under
# the per-type targets in scriptwriter.TEMPLATE_GUIDE) so only egregiously short
# scripts are rejected; the LLM normally clears these easily.
_MIN_WORDS_SHORT = 30
_MIN_WORDS_LONG = {
    "film_recap_ai_images": 220,
    "explainer_curiosity": 200,
    "true_crime_mystery": 260,
    "sports_highlights": 70,
    "top_list_ranking": 90,
    "reaction_commentary": 70,
    "reddit_story": 55,
    "motivational_speech": 110,
}
_DEFAULT_LONG_FLOOR = 120

# Vague-praise words: when a script leans on these with NO concrete anchor it's
# empty filler. Substring match (so "lendári" catches lendária/lendário, etc.).
_VAGUE_PRAISE = [
    "incrível", "incrivel", "sem igual", "impressionante", "inesquecível", "inesquecivel",
    "memoráv", "memorav", "extraordinári", "espetacular", "sensacional", "lendári",
    "dominou", "habilidade sem", "talento", "inspira", "ícone", "icone", "gigante do",
    "melhor de todos", "fora de série", "fora de serie", "surreal", "absurdo",
]

# A "concrete anchor" = a number/year, OR a real multi-word entity (two capitalized
# words in a row, e.g. "David Luiz", "Paris Saint", "Champions League"). Sentence-
# initial single capitals don't count (Portuguese capitalizes every sentence start),
# which is why we require TWO in a row — a real proper-noun phrase.
_NUMBER_RE = re.compile(r"\d")
_ENTITY_RE = re.compile(r"[A-ZÀ-Ý][a-zà-ÿ]{1,}\s+[A-ZÀ-Ý][a-zà-ÿ]{1,}")


def _has_concrete_anchor(raw_text: str) -> bool:
    return bool(_NUMBER_RE.search(raw_text) or _ENTITY_RE.search(raw_text))


def assess(script: dict) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the script is generic/vague/stunted and
    must not proceed (the caller should retry to get real content)."""
    if not isinstance(script, dict):
        return False, "roteiro ausente"
    if script.get("_offline"):
        return False, "roteiro offline (LLM indisponível) — sem conteúdo real"
    raw = (script.get("narration_text") or "").strip()
    text = raw.lower()
    if not text:
        return True, "ok"  # e.g. quote_viral has no narration — handled elsewhere

    # 1) Offline-template / banned clichés.
    hits = sum(1 for m in _ALL_MARKERS if m in text)
    if hits >= 2:
        return False, f"roteiro genérico/clichê ({hits} marcadores de filler)"

    # 2) Length floor for the format/type.
    fmt = (script.get("format") or "long").lower()
    ct = script.get("content_type") or ""
    nwords = len(text.split())
    floor = _MIN_WORDS_SHORT if fmt == "short" else _MIN_WORDS_LONG.get(ct, _DEFAULT_LONG_FLOOR)
    if nwords < floor:
        return False, f"roteiro raso ({nwords} palavras; mínimo {floor} para {ct or 'tipo'}/{fmt})"

    # 3) Vague filler with no concrete anchor — the "sem pão nenhum" case. Reject only
    #    when it leans on vague praise AND has zero real name/number (low false-positive:
    #    any script citing a real fact has an anchor and passes).
    vague = sum(1 for v in _VAGUE_PRAISE if v in text)
    if vague >= 2 and not _has_concrete_anchor(raw):
        return False, (f"roteiro vago sem âncora concreta ({vague} termos de elogio "
                       "genérico, nenhum nome/número real)")

    return True, "ok"


def looks_generic(script: dict) -> bool:
    ok, _ = assess(script)
    return not ok
