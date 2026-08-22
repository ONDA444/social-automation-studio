"""AutomationEngine — regras SE->ENTÃO com guardas anti-loop/anti-duplicata."""
from __future__ import annotations

import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents import automation_engine as eng
from backend.database import Base
from backend.models import AutomationRule, JobStatus, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


def _job(db, **kw) -> VideoJob:
    job = VideoJob(title="Video", **kw)
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


def _evaluate(Session, trigger, job_id):
    """evaluate() abre a própria SessionLocal — apontamos para a sessão de teste."""
    with patch.object(eng, "SessionLocal", Session):
        return eng.evaluate(trigger, job_id)


class AutomationEngineTests(unittest.TestCase):
    def test_notify_rule_fires_ws_event(self) -> None:
        Session = _make_session()
        db = Session()
        job = _job(db, status=JobStatus.AWAITING_APPROVAL)
        db.add(AutomationRule(name="avisa", trigger="job_awaiting_approval", action="notify"))
        db.commit()
        with patch.object(eng, "publish_event") as pub:
            results = _evaluate(Session, "job_awaiting_approval", job.id)
        pub.assert_called_once()
        ev = pub.call_args[0][0]
        self.assertEqual(ev["type"], "notification")
        self.assertEqual(ev["category"], "action")
        self.assertIn(str(job.id), ev["message"])
        self.assertEqual(results[0]["outcome"], "notificação enviada")
        db.close()

    def test_retry_rule_requeues_error_job(self) -> None:
        Session = _make_session()
        db = Session()
        job = _job(db, status=JobStatus.ERROR, error_message="LLM indisponível", retry_count=0)
        db.add(AutomationRule(name="retenta", trigger="job_error", action="retry_job",
                              config={"max_attempts": 2}))
        db.commit()
        with patch.object(eng, "publish_event"), \
             patch("backend.pipeline.dispatch.dispatch_job") as disp:
            results = _evaluate(Session, "job_error", job.id)
        disp.assert_called_once_with(job.id)
        self.assertIn("reenfileirado", results[0]["outcome"])
        db2 = Session()
        self.assertEqual(db2.get(VideoJob, job.id).status, JobStatus.QUEUED)
        self.assertEqual(db2.get(VideoJob, job.id).retry_count, 1)
        db2.close()
        db.close()

    def test_retry_rule_respects_max_attempts(self) -> None:
        Session = _make_session()
        db = Session()
        job = _job(db, status=JobStatus.ERROR, error_message="boom", retry_count=2)
        db.add(AutomationRule(name="retenta", trigger="job_error", action="retry_job",
                              config={"max_attempts": 2}))
        db.commit()
        with patch("backend.pipeline.dispatch.dispatch_job") as disp:
            results = _evaluate(Session, "job_error", job.id)
        disp.assert_not_called()
        self.assertIn("teto", results[0]["outcome"])
        db.close()

    def test_retry_rule_never_touches_duplicate_risk(self) -> None:
        Session = _make_session()
        db = Session()
        job = _job(db, status=JobStatus.ERROR,
                   error_message="o vídeo PODE já estar no canal (youtube). Verifique o YouTube",
                   retry_count=0)
        db.add(AutomationRule(name="retenta", trigger="job_error", action="retry_job",
                              config={"max_attempts": 3}))
        db.commit()
        with patch("backend.pipeline.dispatch.dispatch_job") as disp:
            results = _evaluate(Session, "job_error", job.id)
        disp.assert_not_called()
        self.assertIn("risco de duplicar", results[0]["outcome"])
        # continua em ERROR — ninguém mexeu
        db2 = Session()
        self.assertEqual(db2.get(VideoJob, job.id).status, JobStatus.ERROR)
        db2.close()
        db.close()

    def test_disabled_rule_does_not_run(self) -> None:
        Session = _make_session()
        db = Session()
        job = _job(db, status=JobStatus.ERROR, error_message="boom")
        db.add(AutomationRule(name="off", trigger="job_error", action="retry_job", enabled=False))
        db.commit()
        self.assertEqual(_evaluate(Session, "job_error", job.id), [])
        db.close()

    def test_runs_counter_increments(self) -> None:
        Session = _make_session()
        db = Session()
        job = _job(db, status=JobStatus.PUBLISHED)
        rule = AutomationRule(name="conta", trigger="job_published", action="notify")
        db.add(rule)
        db.commit()
        rid = rule.id
        with patch.object(eng, "publish_event"):
            _evaluate(Session, "job_published", job.id)
            _evaluate(Session, "job_published", job.id)
        db2 = Session()
        self.assertEqual(db2.get(AutomationRule, rid).runs, 2)
        db2.close()
        db.close()
