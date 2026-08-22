"""Scores determinísticos de qualidade (QC) e risco (Compliance)."""
from __future__ import annotations

import asyncio
import unittest

from backend.agents.compliance_agent import ComplianceAgent
from backend.agents.quality_control import _quality_score


class QualityScoreTests(unittest.TestCase):
    def test_perfect_video_scores_100(self) -> None:
        self.assertEqual(_quality_score("qc_passed", []), 100)

    def test_warnings_discount_by_weight(self) -> None:
        score = _quality_score("qc_warning", ["áudio baixo (-35.0 dB)"])  # peso 20
        self.assertEqual(score, 80)

    def test_unknown_warning_costs_default_10(self) -> None:
        self.assertEqual(_quality_score("qc_warning", ["algo novo qualquer"]), 90)

    def test_fatal_failure_scores_zero(self) -> None:
        self.assertEqual(_quality_score("qc_failed_corrupt", ["arquivo ausente"]), 0)

    def test_score_never_below_floor(self) -> None:
        many = ["áudio baixo (-35 dB)"] * 10
        self.assertEqual(_quality_score("qc_warning", many), 5)


class ComplianceRiskScoreTests(unittest.TestCase):
    def _run(self, seo, shorts=None, platforms=None):
        agent = ComplianceAgent(job_id=0, emit=False)
        return asyncio.run(agent.execute(
            seo=seo, shorts=shorts or [],
            target_platforms=platforms or ["youtube", "tiktok", "instagram"],
        ))

    def test_clean_content_scores_100(self) -> None:
        seo = {
            "youtube": {"title": "A história do Brasil", "description": "doc", "tags": ["história"]},
            "tiktok": {"caption": "curto #doc"},
            "instagram": {"caption": "oi", "hashtags": ["#historia"]},
        }
        report = self._run(seo)
        self.assertEqual(report["status"], "approved")
        self.assertEqual(report["risk_score"], 100)

    def test_block_caps_risk_at_60(self) -> None:
        seo = {"youtube": {"title": "Ganhe dinheiro fácil", "description": "", "tags": ["x"]}}
        report = self._run(seo, platforms=["youtube"])
        self.assertEqual(report["status"], "blocked")
        self.assertEqual(report["risk_score"], 60)

    def test_suggestions_discount_10_each(self) -> None:
        seo = {"youtube": {"title": "TUDO EM CAIXA ALTA AQUI MESMO", "description": "", "tags": []}}
        report = self._run(seo, platforms=["youtube"])
        # caps + sem tags = 2 sugestões => 80
        self.assertEqual(report["risk_score"], 80)
