"""Copilot — every answer must be grounded in REAL rows, never invented."""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoAnalytics, VideoJob
from backend.routers.copilot import (
    AskRequest,
    _answer_best_videos,
    _answer_failures,
    _answer_pending_approvals,
    _answer_publish_today,
    _answer_queue_status,
    _detect_intent,
    ask,
)


def _make_db():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)()


def _job(db, **kw) -> VideoJob:
    job = VideoJob(title=kw.pop("title", "Video teste"), **kw)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


class IntentDetectionTests(unittest.TestCase):
    def test_common_ptbr_phrasings(self) -> None:
        cases = {
            "Quais vídeos estão aguardando aprovação?": "pending_approvals",
            "Por que esse job falhou?": "failures",
            "Qual formato está funcionando melhor?": "best_format",
            "Qual conteúdo teve melhor desempenho?": "best_videos",
            "Quais vídeos devo publicar hoje?": "publish_today",
            "Como está a fila agora?": "queue_status",
            "Como está a saúde do sistema?": "system_health",
            "blá blá blá": "overview",
        }
        for q, expected in cases.items():
            self.assertEqual(_detect_intent(q), expected, q)


class CopilotAnswersTests(unittest.TestCase):
    def test_pending_approvals_lists_real_jobs(self) -> None:
        db = _make_db()
        _job(db, title="Aguardando um", status=JobStatus.AWAITING_APPROVAL, qc_status="qc_passed")
        _job(db, title="Publicado", status=JobStatus.PUBLISHED)
        res = _answer_pending_approvals(db)
        self.assertIn("Aguardando um", res.answer)
        self.assertNotIn("Publicado", res.answer)
        self.assertEqual(res.actions[0]["to"], "/approvals")

    def test_pending_approvals_empty_is_honest(self) -> None:
        db = _make_db()
        res = _answer_pending_approvals(db)
        self.assertIn("Nenhum", res.answer)

    def test_failures_report_stage_and_reason(self) -> None:
        db = _make_db()
        _job(db, title="Quebrou", status=JobStatus.ERROR, current_agent="narrator",
             error_message="boom")
        res = _answer_failures(db)
        self.assertIn("narrator", res.answer)
        self.assertIn("boom", res.answer)

    def test_publish_today_only_approved_due(self) -> None:
        db = _make_db()
        _job(db, title="Pronto", status=JobStatus.APPROVED,
             scheduled_at=datetime.utcnow() - timedelta(hours=1))
        _job(db, title="Futuro", status=JobStatus.APPROVED,
             scheduled_at=datetime.utcnow() + timedelta(days=3))
        _job(db, title="Errado", status=JobStatus.ERROR)
        res = _answer_publish_today(db)
        self.assertIn("Pronto", res.answer)
        self.assertNotIn("Futuro", res.answer)
        self.assertNotIn("Errado", res.answer)

    def test_queue_status_counts(self) -> None:
        db = _make_db()
        _job(db, status=JobStatus.PUBLISHED)
        _job(db, title="Dois", status=JobStatus.PUBLISHED)
        _job(db, title="Tres", status=JobStatus.ERROR)
        res = _answer_queue_status(db)
        self.assertIn("2", res.answer)
        self.assertIn("1", res.answer)
        self.assertIn("publicados", res.answer)

    def test_best_videos_uses_real_analytics(self) -> None:
        db = _make_db()
        acct = PlatformAccount(platform="youtube", display_name="Canal")
        db.add(acct)
        db.commit()
        winner = _job(db, title="Campeao", status=JobStatus.PUBLISHED, account_id=acct.id)
        loser = _job(db, title="Flop", status=JobStatus.PUBLISHED, account_id=acct.id)
        db.add(VideoAnalytics(job_id=winner.id, platform="youtube", views=500,
                              collected_at=datetime.utcnow()))
        db.add(VideoAnalytics(job_id=loser.id, platform="youtube", views=10,
                              collected_at=datetime.utcnow()))
        db.commit()
        res = _answer_best_videos(db, acct.id)
        self.assertIn("Campeao", res.answer)
        self.assertIn("500", res.answer)
        # o vídeo com 10 views não entra no top-5 à frente do campeão
        self.assertLess(res.answer.index("Campeao"), res.answer.index("Flop")
                        if "Flop" in res.answer else 10**9)

    def test_ask_endpoint_never_raises(self) -> None:
        db = _make_db()
        res = ask(AskRequest(question="status?"), db=db)
        self.assertTrue(res.answer)

class CopilotAutomationAndJobDetailTests(unittest.TestCase):
    def test_intent_detection_new_intents(self) -> None:
        self.assertEqual(_detect_intent("Quais automações rodaram hoje?"), "automations")
        self.assertEqual(_detect_intent("Em que etapa está o job 12?"), "job_detail")
        self.assertEqual(_detect_intent("O que aconteceu com o video #7?"), "job_detail")

    def test_automations_answer_lists_rules(self) -> None:
        from backend.models import AutomationRule

        db = _make_db()
        db.add(AutomationRule(name="Avisa aprovação", trigger="job_awaiting_approval",
                              action="notify", runs=3))
        db.add(AutomationRule(name="Retenta erro", trigger="job_error", action="retry_job",
                              enabled=False))
        db.commit()
        res = ask(AskRequest(question="quais automações rodaram hoje?"), db=db)
        self.assertIn("Avisa aprovação", res.answer)
        self.assertIn("3×", res.answer)
        self.assertIn("inativa", res.answer)

    def test_job_detail_reports_real_state(self) -> None:
        db = _make_db()
        job = _job(db, title="Teste etapa", status=JobStatus.PROCESSING,
                   current_agent="visuals", progress=64)
        res = ask(AskRequest(question=f"em que etapa está o job {job.id}?"), db=db)
        self.assertIn("visuals", res.answer)
        self.assertIn("64%", res.answer)
        self.assertIn("produção", res.answer)

    def test_job_detail_unknown_id_is_honest(self) -> None:
        db = _make_db()
        res = ask(AskRequest(question="o que aconteceu com o job 999?"), db=db)
        self.assertIn("Não encontrei", res.answer)


class CopilotLLMPolishTests(unittest.TestCase):
    """O polimento via LLM é opcional: sem chave, a resposta determinística
    volta intacta; com LLM falhando/mutilando números, também."""

    def _pending_resp(self):
        db = _make_db()
        _job(db, title="Aguardando um", status=JobStatus.AWAITING_APPROVAL,
             qc_status="qc_passed")
        return _answer_pending_approvals(db)

    def test_no_llm_keeps_deterministic(self) -> None:
        from unittest.mock import patch

        from backend.routers import copilot
        resp = self._pending_resp()
        with patch("backend.llm.available", return_value=False):
            out = copilot._maybe_polish(resp, "quais aguardando?")
        self.assertFalse(out.enhanced)
        self.assertEqual(out.answer, resp.answer)

    def test_overview_is_never_polished(self) -> None:
        from unittest.mock import patch

        from backend.routers import copilot
        db = _make_db()
        resp = copilot._answer_overview(db)
        with patch("backend.llm.available", return_value=True) as avail:
            out = copilot._maybe_polish(resp, "oi")
        avail.assert_not_called()
        self.assertFalse(out.enhanced)

    def test_polish_applies_when_llm_returns_good_text(self) -> None:
        from unittest.mock import patch

        from backend.routers import copilot
        resp = self._pending_resp()

        async def fake_complete(prompt, system=None, max_tokens=2048, fast=False):
            # mantém o número "1" presente na resposta original
            return "Há **1** vídeo aguardando revisão, com o título Aguardando um. " \
                   "Dá uma olhada quando puder!"

        with patch("backend.llm.available", return_value=True), \
                patch("backend.llm.complete", side_effect=fake_complete), \
                patch.object(copilot, "_POLISH_TIMEOUT_S", 5):
            out = copilot._maybe_polish(resp, "quais aguardando?")
        self.assertTrue(out.enhanced)
        self.assertIn("Aguardando um", out.answer)

    def test_polish_rejected_when_number_dropped(self) -> None:
        from unittest.mock import patch

        from backend.routers import copilot
        resp = self._pending_resp()

        async def bad_complete(prompt, system=None, max_tokens=2048, fast=False):
            return "Tem alguns vídeos esperando sua revisão, sem número exato aqui."

        with patch("backend.llm.available", return_value=True), \
                patch("backend.llm.complete", side_effect=bad_complete):
            out = copilot._maybe_polish(resp, "quais aguardando?")
        self.assertFalse(out.enhanced)
        self.assertEqual(out.answer, resp.answer)

    def test_polish_failure_falls_back_silently(self) -> None:
        from unittest.mock import patch

        from backend.routers import copilot
        resp = self._pending_resp()

        async def boom(prompt, system=None, max_tokens=2048, fast=False):
            raise RuntimeError("provider down")

        with patch("backend.llm.available", return_value=True), \
                patch("backend.llm.complete", side_effect=boom):
            out = copilot._maybe_polish(resp, "quais aguardando?")
        self.assertFalse(out.enhanced)
        self.assertEqual(out.answer, resp.answer)
