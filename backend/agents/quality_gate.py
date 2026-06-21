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
import unicodedata

from backend.agents.style_guide import BANNED_PHRASES

# Portuguese/English stopwords — ignored when measuring topic↔script relevance so the
# overlap test keys off meaningful words (entities/nouns), not glue words.
_STOPWORDS = {
    "de", "da", "do", "das", "dos", "que", "com", "para", "por", "uma", "um", "uns",
    "umas", "os", "as", "na", "no", "nas", "nos", "em", "se", "sua", "seu", "suas",
    "seus", "mais", "como", "ele", "ela", "eles", "isso", "esse", "essa", "este",
    "esta", "the", "of", "and", "for", "with", "this", "that",
}


def _norm(s: str) -> str:
    """Lowercase + strip accents so 'Seleção' and 'selecao' compare equal."""
    s = unicodedata.normalize("NFKD", (s or "").lower())
    return "".join(c for c in s if not unicodedata.combining(c))


def _sig_words(s: str) -> set[str]:
    return {w for w in re.sub(r"[^\w\s]", " ", _norm(s)).split()
            if len(w) > 3 and w not in _STOPWORDS}

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


# Literal slots a model leaves behind when it refuses to invent a fact but doesn't
# rewrite the sentence — '{nome do jogador}', '{data}', '{time A}'. Normalized
# (lowercased, accent-stripped) so '{Estádio}' matches. Exact-match only, so a real
# emphasis like [ENFASE]{O estádio tremeu} is NOT flagged.
_PLACEHOLDER_SLOTS = {
    "nome", "nome do jogador", "nome do time", "jogador", "time", "time a", "time b",
    "nome a", "nome b", "data", "tempo", "minuto", "minutos", "ano", "estadio",
    "lugar", "local", "valor", "numero", "placar", "resultado", "evento", "fulano",
    "ciclano", "x", "y", "z", "a", "b", "n",
}


def _has_placeholder(raw_text: str) -> str | None:
    """Return the first unfilled placeholder slot found in the narration, else None.
    These get spoken verbatim ('o gol foi marcado por nome do jogador'), so a script
    containing one must be rejected and regenerated."""
    for inner in re.findall(r"\{([^}]*)\}", raw_text or ""):
        if _norm(inner).strip() in _PLACEHOLDER_SLOTS:
            return inner.strip()
    return None


def assess(script: dict, topic: str | None = None,
           forbidden_topics: list[str] | None = None) -> tuple[bool, str]:
    """Return (ok, reason). ok=False means the script is generic/vague/stunted/off-theme
    and must not proceed (the caller should retry to get real content).

    `topic` enables a RELEVANCE check (does the script actually talk about its subject?)
    and `forbidden_topics` enforces the channel's avoid-list as a HARD block, not just a
    prompt hint — both added to stop off-topic 'momento' videos from shipping."""
    if not isinstance(script, dict):
        return False, "roteiro ausente"
    if script.get("_offline"):
        return False, "roteiro offline (LLM indisponível) — sem conteúdo real"
    raw = (script.get("narration_text") or "").strip()
    text = raw.lower()
    if not text:
        return True, "ok"  # e.g. quote_viral has no narration — handled elsewhere

    # 0a) Unfilled placeholder slots: the model refused to invent a fact but left a
    #     variable ('{nome do jogador}', '{data}') instead of rewriting. Block + retry.
    ph = _has_placeholder(raw)
    if ph:
        return False, f"roteiro com placeholder não preenchido ('{{{ph}}}') — variável deixada no texto"

    # 0) Channel guardrails: avoid_topics as a HARD gate. If the narration actually
    #    mentions a forbidden subject, reject (retry) instead of publishing it.
    for ft in (forbidden_topics or []):
        fn = _norm(str(ft)).strip()
        if fn and fn in _norm(raw):
            return False, f"roteiro toca em tema proibido do canal: '{ft}'"

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

    # 4) Relevance: an off-theme script (the "futebol americano" video that talked about
    #    something else) shares ZERO significant words with its own topic. Conservative
    #    on purpose — only fires when the topic is SPECIFIC (>=4 significant words) and
    #    NOT ONE appears in the narration, so paraphrases (which still name the subject)
    #    pass untouched and false-positives stay near zero.
    if topic:
        topic_words = _sig_words(topic)
        if len(topic_words) >= 4 and not (topic_words & _sig_words(raw)):
            return False, ("roteiro desconectado do tema — nenhuma palavra do tópico "
                           f"({', '.join(sorted(topic_words))}) aparece na narração")

    return True, "ok"


def looks_generic(script: dict) -> bool:
    ok, _ = assess(script)
    return not ok
