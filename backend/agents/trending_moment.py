"""
TrendingMomentAgent — "Momento em alta".

For a channel that opted in, find what is HOT in its niche RIGHT NOW and turn the
best 1–2 moments into ready-to-generate video topics. Everything here is FREE and
keyless:

  • Google News RSS  (PRIMARY) — datacenter-safe, phrases real "moments" as
    headlines, pt-BR. https://news.google.com/rss/search
  • YouTube mostPopular (FALLBACK) — only if YT creds are handy; niche by category.
  • Reddit hot JSON   (FALLBACK) — often blocked on datacenter IPs; guarded.
  • pytrends          (opportunistic) — flaky from datacenter IPs; never the sole source.

A FREE-LLM pass (Groq→Gemini via backend.llm) ranks the raw items and writes the
final title + angle, capped at `max_moments`. If nothing real surfaces, it returns
ZERO moments — a fake "moment" is worse than none. No copyrighted media is touched:
the output is only a title/topic; the normal pipeline renders AI imagery.
"""
from __future__ import annotations

import asyncio
import logging
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import httpx

from backend.agents.base_agent import BaseAgent
from backend import llm

logger = logging.getLogger("studio.trending_moment")

# niche keyword -> YouTube videoCategoryId (BR). Used only for the YT fallback.
_YT_CATEGORY = {
    "futebol": "17", "esporte": "17", "sport": "17", "game": "20", "games": "20",
    "gaming": "20", "música": "10", "music": "10", "filme": "1", "cinema": "1",
    "notícia": "25", "news": "25", "tecnologia": "28", "tech": "28",
}
# niche keyword -> a candidate subreddit (best-effort; Reddit may block Railway IPs).
_SUBREDDIT = {
    "futebol": "futebol", "esporte": "sports", "game": "gaming", "games": "gaming",
    "gaming": "gaming", "filme": "movies", "cinema": "movies", "tecnologia": "technology",
}

_UA = "Mozilla/5.0 (compatible; SAS-TrendBot/1.0)"

# Only ride headlines from roughly the last ~2 days — anything older isn't "do momento".
_MAX_AGE_H = 48

# Brand-safety denylist: a moment touching tragedy/death/politics/violence/crime must
# NOT auto-publish on an entertainment channel. Matches → the moment is routed to human
# Approval instead of going live unattended (see _create_trending_job).
_SENSITIVE = (
    "morte", "morre", "morreu", "morrer", "falece", "faleceu", "óbito", "obito", "luto",
    "acidente", "tragédia", "tragedia", "desastre", "atentado", "tiroteio", "massacre",
    "estupro", "abuso", "assédio", "assedio", "racismo", "homofobia", "suicíd", "suicid",
    "guerra", "ataque", "bomba", "terror", "sequestro", "assassin", "homicíd", "homicid",
    "política", "politica", "eleição", "eleicao", "presidente", "governo", "processo",
    "preso", "prisão", "prisao", "câncer", "cancer", "doença grave", "internado", "uti",
)


def _norm(s: str) -> set[str]:
    return {w for w in re.sub(r"[^\w\s]", " ", (s or "").lower()).split() if len(w) > 2}


def _is_sensitive(text: str) -> bool:
    t = (text or "").lower()
    return any(w in t for w in _SENSITIVE)


class TrendingMomentAgent(BaseAgent):
    name = "trending_moment"

    async def run(
        self,
        *,
        niche: str = "entretenimento",
        language: str = "pt-BR",
        region: str = "BR",
        max_moments: int = 1,
        recent_titles: list[str] | None = None,
        **_,
    ) -> dict:
        niche = (niche or "entretenimento").strip()
        max_moments = max(1, min(2, int(max_moments or 1)))
        recent = [_norm(t) for t in (recent_titles or []) if t]

        # 1) Gather raw "what's happening now" items from free sources.
        raw = await self._gather(niche, language, region)
        # 2) Drop anything we already covered recently (token-overlap >= 0.6).
        fresh = [it for it in raw if not self._seen(it["text"], recent)]
        if not fresh:
            self.emit("progress", f"Nenhum momento novo em '{niche}'")
            return {"niche": niche, "moments": []}

        # 3) Let the free LLM pick the best real MOMENTS and write the video angle.
        moments = await self._select(niche, language, fresh, max_moments)
        self.emit("progress", f"{len(moments)} momento(s) em alta para '{niche}'")
        return {"niche": niche, "moments": moments[:max_moments]}

    # ----------------------------------------------------------------- sources
    async def _gather(self, niche: str, language: str, region: str) -> list[dict]:
        results = await asyncio.gather(
            asyncio.to_thread(self._google_news, niche, language, region),
            asyncio.to_thread(self._reddit, niche),
            asyncio.to_thread(self._pytrends, niche, language, region),
            return_exceptions=True,
        )
        out: list[dict] = []
        seen: set[str] = set()
        for res in results:
            if isinstance(res, Exception) or not res:
                continue
            for item in res:
                key = item["text"].lower().strip()
                if key and key not in seen:
                    seen.add(key)
                    out.append(item)
        return out

    @staticmethod
    def _google_news(niche: str, language: str, region: str) -> list[dict]:
        """Primary source: Google News RSS search. Keyless, datacenter-safe."""
        hl = language or "pt-BR"
        gl = region or "BR"
        url = (f"https://news.google.com/rss/search?q={quote_plus(niche)}"
               f"&hl={hl}&gl={gl}&ceid={gl}:{hl.split('-')[0]}")
        try:
            r = httpx.get(url, timeout=20, headers={"User-Agent": _UA}, follow_redirects=True)
            r.raise_for_status()
            root = ET.fromstring(r.content)
            cutoff = datetime.now(timezone.utc) - timedelta(hours=_MAX_AGE_H)
            items = []
            for it in root.iter("item"):
                title = (it.findtext("title") or "").strip()
                if not title:
                    continue
                # Drop stale headlines — a real "moment" is from the last ~2 days.
                pub = it.findtext("pubDate")
                if pub:
                    try:
                        if parsedate_to_datetime(pub) < cutoff:
                            continue
                    except (TypeError, ValueError):
                        pass
                # Capture the RSS <description> too: a headline alone is too thin to
                # ground a script on — the extra context keeps the angle/script on the
                # ACTUAL event instead of a vague expansion of the title.
                desc = re.sub(r"<[^>]+>", " ", it.findtext("description") or "")
                desc = re.sub(r"\s+", " ", desc).strip()[:280]
                items.append({"text": title, "context": desc, "source": "google_news"})
                if len(items) >= 12:
                    break
            return items
        except Exception as exc:  # noqa: BLE001
            logger.debug("google_news failed: %s", exc)
            return []

    @staticmethod
    def _reddit(niche: str) -> list[dict]:
        """Fallback: a niche subreddit's hot posts. Often blocked on Railway — guarded."""
        sub = None
        for k, v in _SUBREDDIT.items():
            if k in niche.lower():
                sub = v
                break
        if not sub:
            return []
        try:
            r = httpx.get(f"https://www.reddit.com/r/{sub}/hot.json?limit=12",
                          timeout=15, headers={"User-Agent": _UA})
            r.raise_for_status()
            children = r.json().get("data", {}).get("children", [])
            return [{"text": c["data"]["title"], "source": "reddit"}
                    for c in children if c.get("data", {}).get("title")][:10]
        except Exception as exc:  # noqa: BLE001
            logger.debug("reddit failed: %s", exc)
            return []

    @staticmethod
    def _pytrends(niche: str, language: str, region: str) -> list[dict]:
        """Opportunistic: Google Trends rising queries. Flaky from datacenter IPs."""
        try:
            from pytrends.request import TrendReq

            py = TrendReq(hl=language or "pt-BR", tz=180)
            py.build_payload([niche], geo=region, timeframe="now 1-d")
            rising = py.related_queries().get(niche, {}).get("rising")
            if rising is not None and not rising.empty:
                return [{"text": q, "source": "pytrends"} for q in rising["query"].head(6).tolist()]
        except Exception as exc:  # noqa: BLE001
            logger.debug("pytrends failed: %s", exc)
        return []

    # ------------------------------------------------------------- dedupe + LLM
    @staticmethod
    def _seen(text: str, recent: list[set[str]]) -> bool:
        toks = _norm(text)
        if not toks:
            return True
        for prev in recent:
            if not prev:
                continue
            overlap = len(toks & prev) / max(1, len(toks))
            if overlap >= 0.6:
                return True
        return False

    async def _select(self, niche: str, language: str, items: list[dict], k: int) -> list[dict]:
        headlines = "\n".join(
            f"- {it['text']}" + (f" — {it['context']}" if it.get("context") else "") + f"  [{it['source']}]"
            for it in items[:24]
        )
        prompt = (
            f"Você é editor de um canal sobre '{niche}'. Abaixo estão manchetes/assuntos "
            f"do MOMENTO (últimas horas). Escolha no MÁXIMO {k} que sejam um MOMENTO REAL e "
            f"atual (um acontecimento/notícia quente), relevante para o canal e que renda um "
            f"vídeo curto. IGNORE assuntos genéricos/atemporais. Se NENHUM for um momento real "
            f"e relevante, retorne lista vazia.\n\n"
            f"Para cada escolhido, escreva um título chamativo (idioma {language}) e um ângulo "
            f"de 1 linha para o roteirista.\n\nMANCHETES:\n{headlines}\n\n"
            f'Responda em JSON: {{"moments": [{{"title": "...", "topic": "...", "evidence": "manchete original"}}]}}'
        )
        try:
            # This pick DEFINES the video's title/topic, so use the stronger tier — the
            # 3B 'fast' models produced weak, off-topic angles that then auto-published.
            data = await llm.complete_json(prompt, max_tokens=700)
            picked = data.get("moments", []) if isinstance(data, dict) else []
        except llm.LLMUnavailable:
            # Degrade honestly: take the top news headline verbatim (still a REAL item).
            news = next((it for it in items if it["source"] == "google_news"), items[0])
            return [{"title": news["text"], "topic": f"Cobertura do momento: {news['text']}",
                     "source": news["source"], "evidence": news["text"], "grounded": False,
                     "safe": not _is_sensitive(news["text"])}]

        out: list[dict] = []
        for m in picked[:k]:
            title = (m.get("title") or "").strip()
            if not title:
                continue
            ev = (m.get("evidence") or "").strip()
            matched = next((it for it in items if it["text"] == ev), None)
            src = matched["source"] if matched else "google_news"
            # Carry the article context into the evidence so the ResearchAgent grounds on
            # the real event detail, not just the bare headline.
            extra = (matched or {}).get("context", "")
            evidence_rich = f"{ev} — {extra}".strip(" —") if extra else ev
            out.append({
                "title": title,
                "topic": (m.get("topic") or title).strip(),
                "source": src,
                "evidence": evidence_rich,
                "safe": not _is_sensitive(f"{title} {m.get('topic', '')} {ev} {extra}"),
                "grounded": True,
            })
        return out


if __name__ == "__main__":
    async def _demo():
        agent = TrendingMomentAgent(job_id=None, emit=False)
        r = await agent.execute(niche="futebol", max_moments=2)
        for m in r["moments"]:
            print(f"  • {m['title']}  ({m['source']}) — {m['topic']}")
    asyncio.run(_demo())
