"""
ResearchAgent — grounds factual topics in REAL web sources before scripting.

For news/sports/current-event/factual themes it queries Gemini + Google Search
(see llm.research) and stores verified facts + source URLs on the context as
ctx['research']. The ScriptwriterAgent then writes narration from THOSE facts
and is told never to invent scores/dates/names. Purely creative types (quotes,
motivational, fictional Reddit stories) skip research.

This exists because the LLM, ungrounded, confidently fabricates real-world
results (e.g. claiming a team won a match that actually ended in a draw).
"""
from __future__ import annotations

import asyncio

from backend.agents.base_agent import BaseAgent
from backend.config import settings
from backend import llm

# Types whose content is invented by design — grounding adds nothing.
SKIP_TYPES = {"quote_viral", "motivational_speech", "reddit_story"}


class ResearchAgent(BaseAgent):
    name = "research"

    async def run(
        self,
        title: str = "",
        topic: str | None = None,
        content_type: str = "film_recap_ai_images",
        trend_evidence: str = "",
        **_,
    ) -> dict:
        # For a "momento em alta" video the real headline (trend_evidence) is the most
        # specific, factual anchor — far better than the LLM's 1-line angle (topic).
        # Grounding the REAL headline is what keeps the script on the actual event
        # instead of a vague, off-topic expansion of a generic title.
        theme = (trend_evidence or topic or title or "").strip()
        # Copyright-risk brake: nothing else in the pipeline checks the incoming
        # theme/title against a per-channel risk list BEFORE generating — a
        # channel that already took a Content ID strike on a given franchise/
        # team/anime had no automatic guard stopping it from generating another
        # video on that exact same source next time around. Reuses the same
        # avoid_topics infra orchestrator.py already feeds into channel_config
        # for the scriptwriter's guardrails — no new account field required.
        risk_flag = self._risk_flag(theme, title, topic)

        if not theme or content_type in SKIP_TYPES or not settings.research_enabled:
            payload = {"facts": "", "sources": [], "grounded": False,
                       "unavailable": False, "skipped": True, "risk_flag": risk_flag}
            self.ctx_set("research", payload)
            return payload

        self.emit("progress", "Pesquisando fatos reais (fontes verificadas)", progress=12)
        res = await llm.research(theme)
        res["skipped"] = False
        res["risk_flag"] = risk_flag
        self.ctx_set("research", res)

        if res.get("grounded"):
            self.emit("progress",
                      f"Fatos confirmados em {len(res.get('sources', []))} fonte(s)", progress=18)
        elif res.get("unavailable"):
            self.emit("progress",
                      "Grounding indisponível (cota esgotada) — tema factual será re-tentado", progress=18)
        else:
            self.emit("progress",
                      "Sem grounding — roteiro em modo cauteloso (não afirma resultados)", progress=18)
        return res

    def _risk_flag(self, theme: str, title: str, topic: str | None) -> bool:
        """True when the incoming theme/title/topic mentions one of this
        channel's avoid_topics — orchestrator.py routes a flagged job to human
        Approval instead of auto-publish, the same way it already does for a
        stale trending job, so nobody has to remember by hand not to keep
        generating on the exact source that already earned a strike."""
        guardrails = (self.ctx_get("channel_config") or {}).get("guardrails") or {}
        avoid = [t for t in (guardrails.get("forbidden_topics") or []) if t and str(t).strip()]
        if not avoid:
            return False
        haystack = " ".join(str(x) for x in (theme, title, topic or "") if x).lower()
        return any(str(term).strip().lower() in haystack for term in avoid)


# --- standalone test: python -m backend.agents.research "Brasil x Marrocos ontem" ---
if __name__ == "__main__":
    import json
    import sys

    async def _demo():
        theme = sys.argv[1] if len(sys.argv) > 1 else "Brasil x Marrocos Copa 2026"
        agent = ResearchAgent(job_id=None, emit=False)
        res = await agent.execute(title=theme, content_type="sports_highlights")
        print(json.dumps(res, ensure_ascii=False, indent=2))

    asyncio.run(_demo())
