from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.routers.accounts import AccountUpdate, update_account


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class CopyrightStrikesTests(unittest.TestCase):
    """Regression coverage for the manual copyright-strikes log: there is no
    reliable YouTube API for this (checked by hand in YouTube Studio), so the
    operator records what they saw via PATCH /accounts/{id} and the system
    surfaces it -- real incident: "Anime fut" sitting at 2 of 3 active
    strikes with nothing in the system reflecting that."""

    def test_defaults_are_zero_and_none_when_never_set(self) -> None:
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Canal sem strikes")
        db.add(acct)
        db.commit()
        db.refresh(acct)

        self.assertEqual(acct.copyright_strikes, 0)
        self.assertIsNone(acct.copyright_notes)
        data = acct.to_dict()
        self.assertEqual(data["copyright_strikes"], 0)
        self.assertIsNone(data["copyright_notes"])

    def test_patch_sets_strikes_and_notes(self) -> None:
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Anime fut")
        db.add(acct)
        db.commit()
        db.refresh(acct)

        result = update_account(
            acct.id,
            AccountUpdate(
                copyright_strikes=2,
                copyright_notes="2 de 3 advertências ativas em 06/08 -- checado no YouTube Studio.",
            ),
            db=db,
        )

        self.assertEqual(result["copyright_strikes"], 2)
        self.assertEqual(
            result["copyright_notes"],
            "2 de 3 advertências ativas em 06/08 -- checado no YouTube Studio.",
        )
        db.refresh(acct)
        self.assertEqual(acct.copyright_strikes, 2)

    def test_patch_never_stores_negative_strikes(self) -> None:
        db = _make_session()
        acct = PlatformAccount(platform="youtube", display_name="Canal qualquer")
        db.add(acct)
        db.commit()
        db.refresh(acct)

        result = update_account(acct.id, AccountUpdate(copyright_strikes=-5), db=db)

        self.assertEqual(result["copyright_strikes"], 0)

    def test_patch_leaves_strikes_untouched_when_absent(self) -> None:
        db = _make_session()
        acct = PlatformAccount(
            platform="youtube", display_name="Canal com strikes", copyright_strikes=1
        )
        db.add(acct)
        db.commit()
        db.refresh(acct)

        result = update_account(acct.id, AccountUpdate(niche="futebol"), db=db)

        self.assertEqual(result["copyright_strikes"], 1)


if __name__ == "__main__":
    unittest.main()
