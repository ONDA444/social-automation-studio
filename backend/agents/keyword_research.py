"""Real search-query grounding for SEO generation.

Every keyword/tag the pipeline publishes today is invented by an LLM from the
video's own title/script/analysis — nothing is checked against what people
actually search for. This module plugs the same free, keyless Google Trends
technique already used by trending_agent.py/trending_moment.py (to pick a
video's TOPIC) into the SEO step itself, so the keywords/tags attached to the
finished video are grounded in real search behavior for its own title,
instead of purely LLM invention.

Best-effort by design: Google Trends scraping is flaky from datacenter IPs
(see trending_moment.py's own docstring) — any failure returns an empty list
so SEO generation never blocks or degrades on this.
"""
from __future__ import annotations

import logging

logger = logging.getLogger("studio.keyword_research")


def region_for_language(language: str | None) -> str:
    """Best-effort language->geo mapping so Trends isn't silently geo-locked to
    Brazil for non-pt channels (same "pt -> BR else US" rule scheduler.py
    already applies when calling TrendingMomentAgent)."""
    lang = (language or "").strip().lower()
    return "BR" if lang.startswith("pt") else "US"


def related_search_queries(seed: str, language: str = "pt-BR", region: str | None = None) -> list[str]:
    """Real Google Trends queries related to `seed` — both "top" (most
    searched) and "rising" (trending now), deduplicated. `seed` should be the
    video's own title or primary topic, not the channel's broad niche (unlike
    trending_agent.py's usage, which grounds topic SELECTION for the niche as
    a whole)."""
    seed = (seed or "").strip()
    if not seed:
        return []
    region = region or region_for_language(language)
    try:
        from pytrends.request import TrendReq

        # Short, explicit (connect, read) timeout — this call sits directly in
        # the render/SEO critical path (see callers), and pytrends is already
        # documented elsewhere in this codebase as "flaky from datacenter IPs"
        # (trending_moment.py). Without a bound it can hang for many seconds
        # per video before falling through to the except below.
        py = TrendReq(hl=language or "pt-BR", tz=180, timeout=(2, 3))
        py.build_payload([seed], geo=region, timeframe="today 3-m")
        related = py.related_queries().get(seed, {}) or {}
        out: list[str] = []
        for key in ("top", "rising"):
            df = related.get(key)
            if df is not None and not df.empty:
                out.extend(df["query"].head(8).tolist())
        seen: set[str] = set()
        deduped = []
        for q in out:
            q = (q or "").strip()
            low = q.lower()
            if q and low not in seen:
                seen.add(low)
                deduped.append(q)
        return deduped[:12]
    except Exception as exc:  # noqa: BLE001 — flaky from datacenter IPs, never block SEO
        logger.debug("related_search_queries failed for %r: %s", seed, exc)
        return []
