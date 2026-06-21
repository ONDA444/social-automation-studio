"""Channel Optimizer ("Otimizar canal").

Per-channel, opt-in agent. analyze() reads the channel's current identity and
drafts SEO-optimized branding (keywords/description/country/language) from the
account's niche/audience/tone via one free LLM call (deterministic fallback if
the LLM is down). apply() writes ONLY the fields the free YouTube Data API
allows — and only auto-fills EMPTY fields; anything that already has a different
value is routed to a confirm-diff the user must approve, so we never silently
overwrite a setting the user chose. Everything the API can't touch (name, banner,
trailer, moderation, etc.) is surfaced as a 1-click Studio checklist.

Honors: free APIs only; never overwrite a purposeful value; never run on a timer
(analyze/apply are explicit user actions) — see sas-channel-optimizer-design.
"""
from __future__ import annotations

import asyncio
import logging

from backend import llm
from backend.uploaders import youtube as yt

logger = logging.getLogger("studio.channel_optimizer")

# niche keyword (substring, normalized) -> YouTube categoryId. Falls back to 24
# (Entertainment) — never "People & Blogs" (22), which is where unconfigured
# channels land and the algorithm clusters them poorly.
_NICHE_CATEGORY = [
    (("game", "gaming", "jogo", "efootball", "fifa", "roblox", "minecraft"), "20"),  # Gaming
    (("tech", "tecnolog", "ia ", "review", "gadget", "programa", "pc "), "28"),       # Science & Tech
    (("educa", "tutorial", "aula", "ensino", "curso", "history", "histó", "ciência", "ciencia"), "27"),  # Education
    (("music", "música", "musica", "beat"), "10"),                                    # Music
    (("esporte", "futebol", "sport", "football", "nba", "luta"), "17"),               # Sports
    (("news", "notícia", "noticia", "polít", "polit"), "25"),                         # News & Politics
    (("comédia", "comedia", "humor", "meme", "funny"), "23"),                         # Comedy
    (("finan", "invest", "dinheiro", "money", "negócio", "negocio"), "27"),           # → Education
    (("filme", "cinema", "movie", "série", "serie", "anime"), "24"),                  # Entertainment
]

# content_language -> default channel country (ISO-3166-1 alpha-2). Faceless ops
# often target the audience market; we use the language's primary market.
_LANG_COUNTRY = {
    "pt-br": "BR", "pt": "BR", "pt-pt": "PT", "en": "US", "en-us": "US",
    "en-gb": "GB", "es": "ES", "es-mx": "MX", "es-us": "US", "hi": "IN",
    "fr": "FR", "de": "DE", "ja": "JP", "ru": "RU", "ar": "SA", "id": "ID",
}

# Recommended blocked-words paste-list for faceless niches (impersonation/scam/bot
# spam). The channel's own name is appended at runtime (impersonator "reply" scams).
_BLOCKED_WORDS_BASE = [
    "telegram", "t.me", "whatsapp", "whats app", "+1", "+55", "investment",
    "invista", "crypto", "bitcoin", "guru", "mentor", "reach out", "dm me",
    "manda dm", "chama no", "lucro garantido", "ganhe dinheiro", "renda extra",
]


def _category_for(niche: str | None) -> str:
    n = (niche or "").lower()
    for keys, cat in _NICHE_CATEGORY:
        if any(k in n for k in keys):
            return cat
    return "24"


def _country_for(content_language: str | None) -> str:
    return _LANG_COUNTRY.get((content_language or "").lower(), "BR")


def _keywords_str(keywords: list[str], limit: int = 480) -> str:
    """Build the space-separated channel-keywords string (multi-word phrases quoted),
    capped under YouTube's 500-char hard limit — over it, channels.update 400s the
    whole request. Adds whole phrases until the next one wouldn't fit."""
    out, total = [], 0
    for k in keywords:
        tok = f'"{k}"' if " " in k else k
        add = len(tok) + (1 if out else 0)
        if total + add > limit:
            break
        out.append(tok)
        total += add
    return " ".join(out)


def _checklist(account) -> list[dict]:
    """Studio-only items (no free API) as a 1-click checklist with deep links."""
    name = (account.display_name or "").strip()
    blocked = _BLOCKED_WORDS_BASE + ([name.lower()] if name and len(name) > 2 else [])
    return [
        {"id": "name", "label": "Nome e @handle do canal",
         "why": "A API não pode mudar o nome (channelTitleUpdateForbidden). Deixe profissional e fácil de buscar.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/details"},
        {"id": "branding_images", "label": "Foto, banner e imagem do canal",
         "why": "Imagens do canal são só no Studio. Banner com o nicho + horário de postagem passa profissionalismo.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/images"},
        {"id": "trailer", "label": "Trailer para quem não é inscrito",
         "why": "O item de MAIOR conversão de inscrito — a API não toca. Coloque um vídeo de 30-90s que vende o canal.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/sections"},
        {"id": "watermark", "label": "Marca d'água (botão de inscrever-se)",
         "why": "Suba seu logo como marca d'água e defina 'vídeo inteiro' — o botão de inscrever fica sempre na tela.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/branding"},
        {"id": "moderation", "label": "Moderação: reter comentários para revisão",
         "why": "Mate spam de bot/golpe. A API antiga foi desativada — defina no Studio: 'Reter comentários potencialmente inadequados' (ou Rígido).",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/community"},
        {"id": "blocked_words", "label": "Palavras bloqueadas (anti-spam)",
         "why": "Cole esta lista nas palavras bloqueadas — qualquer comentário com elas é retido automaticamente.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/community",
         "copy_text": ", ".join(blocked)},
        {"id": "block_links", "label": "Reter comentários com links",
         "why": "Bloqueia links de golpe/phishing nos comentários. Só no Studio.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/community"},
        {"id": "made_for_kids_channel", "label": "Público padrão do canal (não é para crianças)",
         "why": "Já carimbamos isso em cada vídeo; confirme o padrão do canal para casar.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/advanced"},
        {"id": "feature_eligibility", "label": "Elegibilidade de recursos (verificar telefone)",
         "why": "Verifique o telefone no Studio para liberar thumbnails personalizadas e uploads longos. Sem API.",
         "studio_url": "https://studio.youtube.com/channel/UC/editing/features"},
    ]


async def _propose(account) -> dict:
    """LLM-drafted branding from the channel identity. Deterministic fallback."""
    niche = (account.niche or account.display_name or "conteúdo").strip()
    audience = (account.target_audience or "").strip()
    tone = (account.content_tone or "neutral").strip()
    lang = account.content_language or "pt-BR"
    name = account.display_name or niche
    try:
        prompt = (
            f"Otimize a identidade de um canal do YouTube para descoberta e profissionalismo.\n"
            f"Canal: {name}\nNicho: {niche}\nPúblico: {audience or 'geral'}\nTom: {tone}\n"
            f"Idioma: {lang}\n\n"
            f"Responda SOMENTE JSON: {{\"keywords\": [\"10-15 frases-chave de nicho, a mais específica "
            f"primeiro, sem # e sem repetir\"], \"description\": \"descrição/Sobre de até 900 caracteres, "
            f"com a proposta de valor e a palavra-chave principal nas 2 primeiras frases, mais uma linha de "
            f"cadência de postagem\"}}"
        )
        out = await llm.complete_json(prompt, system="Especialista de SEO de canais do YouTube. Só JSON válido.",
                                      max_tokens=900)
        kws = [str(k).strip() for k in (out.get("keywords") or []) if str(k).strip()][:15]
        desc = (out.get("description") or "").strip()[:900]
        if kws and desc:
            return {"keywords": kws, "description": desc, "source": "llm"}
    except Exception as exc:  # noqa: BLE001
        logger.info("optimizer LLM proposal failed, using fallback: %s", exc)
    # Deterministic fallback — concrete, not vague.
    kws = [niche] + [w for w in niche.split() if len(w) > 3]
    if audience:
        kws.append(audience.split(",")[0].strip())
    desc = (f"{name} — {niche}. "
            f"{('Para ' + audience + '. ') if audience else ''}"
            f"Vídeos novos toda semana. Inscreva-se para não perder.")
    return {"keywords": list(dict.fromkeys([k for k in kws if k]))[:12],
            "description": desc[:900], "source": "fallback"}


async def analyze(account, creds: dict) -> dict:
    """Read-only: build the current-vs-proposed plan + the Studio checklist. No writes."""
    branding = await asyncio.to_thread(yt.get_branding, creds)
    if not branding.get("ok"):
        return {"ok": False, "error": branding.get("error", "Não foi possível ler o canal.")}
    cur = branding.get("branding") or {}
    proposed = await _propose(account)
    keywords_str = _keywords_str(proposed["keywords"])

    targets = {
        "keywords": (keywords_str, "Palavras-chave do canal"),
        "description": (proposed["description"], "Descrição (Sobre)"),
        "country": (_country_for(account.content_language), "País"),
        "defaultLanguage": ((account.content_language or "pt-BR").split("-")[0], "Idioma padrão"),
    }
    fields = []
    for key, (value, label) in targets.items():
        current = (cur.get(key) or "").strip()
        if not value or current == str(value).strip():
            action = "nochange"
        elif not current:
            action = "auto"        # empty -> safe to fill automatically
        else:
            action = "confirm"     # already set & differs -> needs user OK
        fields.append({"key": key, "label": label, "current": current,
                       "proposed": value, "action": action})

    _niche = (account.niche or "Destaques").strip() or "Destaques"
    return {
        "ok": True,
        "channel_id": branding.get("channel_id"),
        "title": branding.get("title"),
        "fields": fields,
        "playlists": [f"{_niche.title()}: melhores momentos",
                      f"{_niche.title()}: do básico ao avançado"],
        "category_id": _category_for(account.niche),
        "source": proposed["source"],
        "checklist": _checklist(account),
    }


async def apply(account, creds: dict, confirmed_fields: list[str] | None = None) -> dict:
    """Apply AUTO fields + any the user confirmed. Re-analyzes (idempotent) so the
    write reflects the live state, then persists what was applied."""
    confirmed = set(confirmed_fields or [])
    plan = await analyze(account, creds)
    if not plan.get("ok"):
        return plan

    patch, applied_log = {}, {}
    for f in plan["fields"]:
        if f["action"] == "auto" or (f["action"] == "confirm" and f["key"] in confirmed):
            patch[f["key"]] = f["proposed"]
    results = {"branding": None, "playlists": [], "sections": None}

    if patch:
        res = await asyncio.to_thread(yt.update_branding, creds, patch)
        results["branding"] = res
        if res.get("ok"):
            for k in patch:
                applied_log[k] = patch[k]

    # Niche playlists (cheap session-time win; idempotent by title).
    pl_ids = []
    for title in plan.get("playlists", []):
        pid = await asyncio.to_thread(yt.ensure_playlist, creds, title,
                                      f"{account.niche or ''}".strip())
        if pid:
            pl_ids.append(pid)
            results["playlists"].append({"title": title, "id": pid})

    # Homepage: lead with popular uploads (session-duration signal) — only if the
    # channel still has the bare default layout (never reshuffles a curated one).
    if pl_ids:
        ok = await asyncio.to_thread(yt.ensure_section, creds, "popularUploads", "", 0, None)
        results["sections"] = {"popularUploads": ok}

    # Persist state (proves what we changed; checklist 'done' flags managed separately).
    state = dict(getattr(account, "channel_optimization", None) or {})
    state["activated"] = True
    state.setdefault("checklist", [])
    state["applied"] = {**(state.get("applied") or {}), **applied_log}
    state["last_result"] = {"branding_ok": bool((results["branding"] or {}).get("ok")),
                            "playlists": len(results["playlists"]),
                            "fields": list(applied_log.keys())}
    account.channel_optimization = state
    return {"ok": True, "applied": applied_log, "results": results, "state": state}
