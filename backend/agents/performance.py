"""
PerformanceInsights — the LIVE learning signal that closes the loop between
published-video analytics and the generation of NEW videos.

Reads VideoAnalytics (already collected per job at 2h/24h/7d) for an account,
ranks past videos by real performance, and distills a compact "what's working /
what's not" brief. That brief is injected into the Scriptwriter and SEOAgent
prompts so every new video leans toward what actually performed — WITHOUT any
extra LLM/API cost (pure aggregation in Python; the free-API constraint stays
intact). This is the SEO/performance feedback loop: the more we publish, the
smarter the next video gets.

Signal today = views (the metric the free YouTube Data API reliably returns).
When richer metrics (CTR/retention via the YouTube Analytics API) get populated,
the same ranking sharpens automatically — no code change needed.
"""
from __future__ import annotations

import logging
from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from backend.models import VideoJob

logger = logging.getLogger("studio.performance")

# Need at least this many MEASURED videos (views > 0) before the signal is
# trustworthy — below this the loop stays silent (returns "") so early videos
# aren't steered by noise.
_MIN_VIDEOS = 3


class PerformanceInsights:
    def __init__(self, db: Session) -> None:
        self.db = db

    def _measured(self, account_id: int | None) -> list[dict]:
        """Latest snapshot per job for the account → [{job, views, likes, comments}]."""
        if not account_id:
            return []
        jobs = self.db.execute(
            select(VideoJob)
            .options(selectinload(VideoJob.analytics))
            .where(VideoJob.account_id == account_id)
        ).scalars().all()
        out: list[dict] = []
        for job in jobs:
            rows = list(job.analytics or [])
            if not rows:
                continue
            # Keep only the most recent snapshot per platform, then sum across them.
            latest: dict[str, object] = {}
            for r in rows:
                cur = latest.get(r.platform)
                if cur is None or (r.collected_at and (cur.collected_at is None
                                   or r.collected_at > cur.collected_at)):
                    latest[r.platform] = r
            vals = list(latest.values())
            # retention/completion are RATES → average across platforms (summing is
            # meaningless); they default to 0.0 until the YouTube Analytics API
            # populates them, so _score's factor stays 1.0 (no-op) until then.
            def _avg(attr: str) -> float:
                xs = [float(getattr(r, attr, 0.0) or 0.0) for r in vals]
                return sum(xs) / len(xs) if xs else 0.0
            out.append({
                "job": job,
                "views": sum(int(getattr(r, "views", 0) or 0) for r in vals),
                "likes": sum(int(getattr(r, "likes", 0) or 0) for r in vals),
                "comments": sum(int(getattr(r, "comments", 0) or 0) for r in vals),
                "retention_avg": _avg("retention_avg"),
                "completion_rate": _avg("completion_rate"),
            })
        return out

    @staticmethod
    def _score(d: dict) -> float:
        """Learning signal that rewards videos people actually WATCHED/engaged with, not
        just clicked. Uses real free-API data today (views + likes + comments) and folds
        in retention/completion automatically once the YouTube Analytics API populates
        them (default 0.0 → factor 1.0, a no-op until real data arrives). Raw views alone
        rewarded clickbait that tanks session-time — the opposite of what Browse/Suggested
        and the 4000h threshold reward."""
        views = max(0, int(d.get("views", 0) or 0))
        eng = (int(d.get("likes", 0) or 0) + int(d.get("comments", 0) or 0)) / max(1, views)
        ret = float(d.get("retention_avg", 0.0) or 0.0)
        comp = float(d.get("completion_rate", 0.0) or 0.0)
        return views * (1 + min(eng, 1.0)) * (1 + ret + comp)

    def account_insights(self, account_id: int | None) -> dict:
        """Structured "what worked / what didn't" for the account (or not-ready)."""
        measured = [d for d in self._measured(account_id) if d["views"] > 0]
        if len(measured) < _MIN_VIDEOS:
            return {"ready": False, "n": len(measured)}
        measured.sort(key=self._score, reverse=True)
        n = len(measured)
        cut = max(1, n // 3)
        top, bottom = measured[:cut], measured[-cut:]

        by_type: dict[str, list[int]] = defaultdict(list)
        for d in measured:
            by_type[d["job"].content_type or "?"].append(d["views"])
        type_ranking = sorted(
            ({"type": t, "avg_views": round(sum(v) / len(v)), "n": len(v)}
             for t, v in by_type.items()),
            key=lambda x: x["avg_views"], reverse=True,
        )

        # Tags from the top performers, weighted by the views they pulled.
        tag_score: dict[str, int] = defaultdict(int)
        for d in top:
            meta = d["job"].seo_metadata if isinstance(d["job"].seo_metadata, dict) else {}
            for tag in ((meta.get("youtube") or {}).get("tags") or [])[:15]:
                tag_score[tag] += int(self._score(d))
        best_tags = [t for t, _ in sorted(tag_score.items(), key=lambda x: x[1], reverse=True)[:12]]

        def _titles(items: list[dict]) -> list[str]:
            return [(d["job"].title or "")[:80] for d in items if d["job"].title]

        return {
            "ready": True,
            "n": n,
            "top_titles": _titles(top),
            "bottom_titles": _titles(bottom),
            "type_ranking": type_ranking,
            "best_tags": best_tags,
            "best_type": type_ranking[0]["type"] if type_ranking else None,
            "worst_type": type_ranking[-1]["type"] if len(type_ranking) > 1 else None,
        }

    def prompt_block(self, account_id: int | None) -> str:
        """Compact brief injected into the scriptwriter + SEO prompts. "" when not ready."""
        try:
            ins = self.account_insights(account_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("performance insights failed: %s", exc)
            return ""
        if not ins.get("ready"):
            return ""
        lines = ["\n=== DESEMPENHO REAL DO CANAL (aprenda com o que JÁ publicamos) ==="]
        lines.append(f"Baseado em {ins['n']} vídeos com métricas reais (views).")
        if ins.get("best_type") and ins.get("type_ranking"):
            tr = ", ".join(f"{t['type']}≈{t['avg_views']} views" for t in ins["type_ranking"][:4])
            lines.append(f"TIPO que mais performou: {ins['best_type']}. Ranking: {tr}.")
        if ins.get("top_titles"):
            lines.append("TÍTULOS que MAIS performaram (replique o ângulo/gancho/estilo): "
                         + " | ".join(ins["top_titles"][:5]))
        if ins.get("bottom_titles"):
            lines.append("TÍTULOS que MENOS performaram (EVITE esse padrão): "
                         + " | ".join(ins["bottom_titles"][:5]))
        if ins.get("best_tags"):
            lines.append("TAGS/temas dos melhores vídeos (priorize quando fizer sentido): "
                         + ", ".join(ins["best_tags"]))
        lines.append("Dobre no que funciona, evite o que não funcionou — sem inventar fatos.")
        lines.append("=== FIM DESEMPENHO REAL ===\n")
        return "\n".join(lines)
