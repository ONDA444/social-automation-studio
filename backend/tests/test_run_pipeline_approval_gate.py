from __future__ import annotations

import asyncio
import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.orchestrator import run_pipeline
from backend.database import Base
from backend.models import JobStatus, PlatformAccount, VideoJob


def _make_sessionmaker():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine, future=True)


class _StubAgent:
    """Generic stand-in for every pipeline stage: swallows any args/kwargs and
    returns a preset result from `execute()` without doing real work."""

    _RESULT: dict = {}

    def __init__(self, job_id, ctx):
        self.job_id = job_id
        self.ctx = ctx

    async def execute(self, *args, **kwargs):
        return self._RESULT


def _stub(result=None):
    return type("Stub", (_StubAgent,), {"_RESULT": result if result is not None else {}})


class _ScriptStub(_StubAgent):
    async def execute(self, *args, **kwargs):
        return {"content_type": "film_recap_ai_images", "hook": "oi"}


class _EchoScriptStub(_StubAgent):
    """Hook/retention agents receive `script=...` and hand it back unchanged."""

    async def execute(self, *args, **kwargs):
        return kwargs.get("script", {})


class _VideoEditorStub(_StubAgent):
    async def execute(self, *args, **kwargs):
        return {"main_video_path": "/tmp/main.mp4", "duration": 12.0}


class _QCPassStub(_StubAgent):
    async def execute(self, *args, **kwargs):
        return {"status": "qc_passed", "warnings": []}


class _CompliancePassStub(_StubAgent):
    async def execute(self, *args, **kwargs):
        return {"status": "ok", "blocks": []}


def _make_job(db, *, account_id=None, require_approval=False, target_platforms=None):
    job = VideoJob(
        title="Video teste pipeline",
        mode="from_title",
        content_type="film_recap_ai_images",
        video_format="long",
        account_id=account_id,
        status=JobStatus.QUEUED,
        target_platforms=target_platforms or ["youtube"],
        video_context={"require_approval": require_approval} if require_approval else {},
    )
    db.add(job)
    db.commit()
    db.refresh(job)
    return job


class RunPipelineApprovalGateTests(unittest.TestCase):
    """run_pipeline() is the sole place deciding APPROVED (auto-publish) vs
    AWAITING_APPROVAL (human review). This exercises that decision end-to-end
    with every costly agent stage replaced by a stub, so a regression in the
    gating logic (e.g. auto-publishing a manually created job) is caught."""

    def _patch_stages(self):
        return [
            patch("backend.agents.orchestrator.ResearchAgent", _stub({})),
            patch("backend.agents.orchestrator.ScriptwriterAgent", _ScriptStub),
            patch("backend.agents.orchestrator.HookOptimizerAgent", _EchoScriptStub),
            patch("backend.agents.orchestrator.RetentionEngineerAgent", _EchoScriptStub),
            patch("backend.agents.orchestrator.PackagingStrategistAgent", _stub({})),
            patch("backend.agents.orchestrator.NarratorAgent", _stub({})),
            patch("backend.agents.orchestrator.VisualsAgent", _stub({})),
            patch("backend.agents.orchestrator.EditingDirectorAgent", _stub({})),
            patch("backend.agents.orchestrator.MusicCuratorAgent", _stub({})),
            patch("backend.agents.orchestrator.CaptionAgent", _stub({})),
            patch("backend.agents.orchestrator.VideoEditorAgent", _VideoEditorStub),
            patch("backend.agents.orchestrator.ShortsFactoryAgent", _stub({})),
            patch("backend.agents.orchestrator.ShortsHookAgent", _stub({})),
            patch("backend.agents.orchestrator.ShortsStrategistAgent", _stub({})),
            patch("backend.agents.orchestrator.SEOAgent", _stub({"youtube": {}})),
            patch("backend.agents.orchestrator.QualityControlAgent", _QCPassStub),
            patch("backend.agents.orchestrator.ComplianceAgent", _CompliancePassStub),
            patch("backend.agents.performance.PerformanceInsights.prompt_block", return_value=""),
        ]

    def test_manually_created_job_stops_at_awaiting_approval_even_with_auto_publish_on(self) -> None:
        Session = _make_sessionmaker()
        db = Session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.commit()
        job = _make_job(db, account_id=account.id, require_approval=True)
        job_id = job.id
        db.close()

        patchers = self._patch_stages()
        for p in patchers:
            p.start()
        try:
            with patch("backend.agents.orchestrator.SessionLocal", Session), \
                 patch("backend.agents.orchestrator.settings.auto_publish", True):
                result = asyncio.run(run_pipeline(job_id))
        finally:
            for p in patchers:
                p.stop()

        self.assertEqual(result["status"], "awaiting_approval")
        db2 = Session()
        refreshed = db2.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.AWAITING_APPROVAL)
        self.assertEqual(refreshed.approval_status, "pending")
        db2.close()

    def test_channel_job_without_require_approval_auto_approves_when_auto_publish_on(self) -> None:
        Session = _make_sessionmaker()
        db = Session()
        account = PlatformAccount(platform="youtube", display_name="Canal Teste", niche="geral")
        db.add(account)
        db.commit()
        job = _make_job(db, account_id=account.id, require_approval=False)
        job_id = job.id
        db.close()

        patchers = self._patch_stages()
        for p in patchers:
            p.start()
        try:
            with patch("backend.agents.orchestrator.SessionLocal", Session), \
                 patch("backend.agents.orchestrator.settings.auto_publish", True), \
                 patch("backend.pipeline.dispatch.dispatch_publish", return_value={"ok": True}):
                result = asyncio.run(run_pipeline(job_id))
        finally:
            for p in patchers:
                p.stop()

        self.assertEqual(result["status"], "approved_auto")
        db2 = Session()
        refreshed = db2.get(VideoJob, job_id)
        self.assertIn(refreshed.status, (JobStatus.APPROVED, JobStatus.PUBLISHING))
        self.assertEqual(refreshed.approval_status, "approved")
        db2.close()

    def test_no_account_job_stops_at_awaiting_approval_regardless_of_auto_publish(self) -> None:
        Session = _make_sessionmaker()
        db = Session()
        job = _make_job(db, account_id=None, require_approval=False)
        job_id = job.id
        db.close()

        patchers = self._patch_stages()
        for p in patchers:
            p.start()
        try:
            with patch("backend.agents.orchestrator.SessionLocal", Session), \
                 patch("backend.agents.orchestrator.settings.auto_publish", True):
                result = asyncio.run(run_pipeline(job_id))
        finally:
            for p in patchers:
                p.stop()

        self.assertEqual(result["status"], "awaiting_approval")
        db2 = Session()
        refreshed = db2.get(VideoJob, job_id)
        self.assertEqual(refreshed.status, JobStatus.AWAITING_APPROVAL)
        db2.close()


if __name__ == "__main__":
    unittest.main()
