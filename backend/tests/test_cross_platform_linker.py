from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.agents.cross_platform_linker import mirror_after_publish
from backend.database import Base
from backend.models import PlatformAccount, VideoJob


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class MirrorAfterPublishRetryTests(unittest.TestCase):
    def test_retry_does_not_create_duplicate_mirror_for_same_platform(self) -> None:
        db = _make_session()
        source_account = PlatformAccount(
            platform="youtube",
            display_name="Canal Fonte",
            niche="geral",
            mirror_to_linked=True,
        )
        db.add(source_account)
        db.flush()

        instagram_account = PlatformAccount(
            platform="instagram", display_name="Instagram Linkado", niche="geral", status="active"
        )
        db.add(instagram_account)
        db.flush()

        source_account.linked_accounts = {"instagram": instagram_account.id}
        db.add(source_account)
        db.flush()

        source_job = VideoJob(
            title="Video multi-plataforma",
            mode="from_title",
            content_type="film_recap_ai_images",
            video_format="long",
            account_id=source_account.id,
            target_platforms=["youtube", "tiktok"],
        )
        db.add(source_job)
        db.commit()

        # First call (e.g. after YouTube succeeds) creates the Instagram mirror.
        created_first = mirror_after_publish(db, source_job)
        self.assertEqual(len(created_first), 1)

        # Retry of the same partially-failed job (TikTok still failing, so the
        # job gets reprocessed) must NOT create a second duplicate mirror.
        created_second = mirror_after_publish(db, source_job)
        self.assertEqual(created_second, [])

        mirrors = (
            db.query(VideoJob)
            .filter(VideoJob.is_mirror.is_(True), VideoJob.mirror_source_id == source_job.id)
            .all()
        )
        self.assertEqual(len(mirrors), 1)


if __name__ == "__main__":
    unittest.main()
