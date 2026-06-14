"""
TrendingAgent — content suggestions from Google Trends (pytrends) + LLM.

Runs 2x/day (06:00, 14:00) via the scheduler. Degrades gracefully: if pytrends
or the LLM is unavailable, it still returns niche-derived suggestions.
"""
from __future__ import annotations

import asyncio
import logging

from backend.agents.base_agent import BaseAgent
from backend import llm

logger = logging.getLogger("studio.trending")


class TrendingAgent(BaseAgent):
    name = "trending_agent"

    async def run(self, niche: str = "", region: str = "BR", n: int = 10, **_) -> dict:
        niche = niche or "entretenimento"
        suggestions: list[dict] = []

        # 1) Google Trends (blocking lib -> thread).
        trends = await asyncio.to_thread(self._pytrends, niche, region)
        for t in trends:
            suggestions.append({"topic": t, "score": 80, "reason": "Em alta no Google Trends", "source": "google_trends"})

        # 2) LLM niche topics.
        try:
            llm_topics = await self._llm_topics(niche, region)
            for t in llm_topics:
                suggestions.append({"topic": t, "score": 70, "reason": f"Tópico quente em {niche}", "source": "llm"})
        except llm.LLMUnavailable:
            if not suggestions:
                suggestions = self._offline(niche)

        # Dedup, keep top n.
        seen, out = set(), []
        for s in suggestions:
            key = s["topic"].lower().strip()
            if key and key not in seen:
                seen.add(key)
                out.append(s)
        out = out[:n]
        self.emit("progress", f"{len(out)} sugestões trending para '{niche}'")
        return {"niche": niche, "suggestions": out}

    @staticmethod
    def _pytrends(niche: str, region: str) -> list[str]:
        try:
            from pytrends.request import TrendReq

            py = TrendReq(hl="pt-BR", tz=180)
            py.build_payload([niche], geo=region, timeframe="now 1-d")
            related = py.related_queries().get(niche, {})
            rising = related.get("rising")
            if rising is not None and not rising.empty:
                return rising["query"].head(8).tolist()
        except Exception as exc:  # noqa: BLE001
            logger.debug("pytrends failed: %s", exc)
        return []

    async def _llm_topics(self, niche: str, region: str) -> list[str]:
        prompt = (
            f"Liste 10 tópicos/temas em alta sobre '{niche}' no Brasil hoje, ideais para "
            f"vídeos curtos virais. Responda em JSON: {{\"topics\": [\"...\"]}}"
        )
        data = await llm.complete_json(prompt, max_tokens=500)
        return data.get("topics", [])[:10]

    @staticmethod
    def _offline(niche: str) -> list[dict]:
        bases = ["curiosidades", "história", "mistério", "ranking", "fatos surpreendentes",
                 "antes e depois", "o que ninguém te conta", "top 5"]
        return [{"topic": f"{b} sobre {niche}", "score": 50, "reason": "Sugestão padrão (sem LLM/Trends)",
                 "source": "fallback"} for b in bases]


if __name__ == "__main__":
    async def _demo():
        agent = TrendingAgent(job_id=None, emit=False)
        r = await agent.execute(niche="histórias de terror")
        for s in r["suggestions"]:
            print(f"  [{s['score']}] {s['topic']} ({s['source']})")
    asyncio.run(_demo())
