"""
Quality gate — BLOCKS generic/templated scripts from ever becoming a video.

The rule (per the user): a theme must produce REAL, specific content (true facts,
names, dates). When the LLM is unavailable the scriptwriter would otherwise emit a
hollow offline template ("a história fica realmente interessante...", "tem um
detalhe que ninguém percebeu...") that throws away the researched facts. That is
exactly the "vídeo escroto" we must never publish.

So: scripts that are the offline template, or stuffed with empty AI filler, are
rejected → the job retries (and produces real content when the LLM is back) instead
of shipping garbage. Real, fact-based scripts pass untouched.
"""
from __future__ import annotations

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


def assess(script: dict) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the script is generic/garbage and must
    not proceed (the caller should retry to get real content)."""
    if not isinstance(script, dict):
        return False, "roteiro ausente"
    if script.get("_offline"):
        return False, "roteiro offline (LLM indisponível) — sem conteúdo real"
    text = (script.get("narration_text") or "").lower().strip()
    if not text:
        return True, "ok"  # e.g. quote_viral has no narration — handled elsewhere
    hits = sum(1 for m in _ALL_MARKERS if m in text)
    if hits >= 2:
        return False, f"roteiro genérico/clichê ({hits} marcadores de filler)"
    return True, "ok"


def looks_generic(script: dict) -> bool:
    ok, _ = assess(script)
    return not ok
