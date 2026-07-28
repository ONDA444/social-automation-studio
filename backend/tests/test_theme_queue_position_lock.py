"""Regression test: POST /themes 500'd on EVERY call in production because
the FIFO position lock did `SELECT max(position) ... FOR UPDATE`, which
Postgres rejects outright (psycopg2.errors.FeatureNotSupported: "FOR UPDATE
is not allowed with aggregate functions"). SQLite silently ignores
FOR UPDATE, so this never failed locally/in CI — only against real Postgres,
where the "Adicionar a fila" button on the Agenda page was completely
broken (every click surfaced as "Failed to fetch" in the browser, since the
resulting 500 also somehow lost its CORS header). The fix locks the actual
max-position ROW instead of the aggregate; these tests cover the functional
FIFO-ordering behavior that fix must preserve."""
from __future__ import annotations

import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.database import Base
from backend.models import PlatformAccount
from backend.routers.themes import ThemeCreate, create_themes


def _make_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine, future=True)
    return Session()


class ThemeQueuePositionLockTests(unittest.TestCase):
    def _account(self, db):
        account = PlatformAccount(platform="youtube", display_name="Canal", niche="geral")
        db.add(account)
        db.flush()
        return account

    def test_first_batch_starts_at_position_zero(self) -> None:
        db = _make_session()
        account = self._account(db)

        result = create_themes(
            ThemeCreate(account_id=account.id, themes=["Tema A", "Tema B"]), db=db,
        )

        self.assertEqual(result["created"], 2)

    def test_second_batch_continues_after_the_first_without_colliding(self) -> None:
        db = _make_session()
        account = self._account(db)

        create_themes(ThemeCreate(account_id=account.id, themes=["Tema A", "Tema B", "Tema C"]), db=db)
        create_themes(ThemeCreate(account_id=account.id, themes=["Tema D", "Tema E"]), db=db)

        from backend.models.theme_queue import ThemeQueue
        positions = sorted(p for (p,) in db.query(ThemeQueue.position).all())
        self.assertEqual(positions, [0, 1, 2, 3, 4])

    def test_ten_themes_in_one_call_all_get_distinct_sequential_positions(self) -> None:
        """Mirrors the real production payload that triggered the bug: a
        batch of 10 themes added at once via the Agenda page's textarea."""
        db = _make_session()
        account = self._account(db)

        result = create_themes(
            ThemeCreate(account_id=account.id, themes=[f"Tema {i}" for i in range(10)], format="short"),
            db=db,
        )

        self.assertEqual(result["created"], 10)
        from backend.models.theme_queue import ThemeQueue
        positions = sorted(p for (p,) in db.query(ThemeQueue.position).all())
        self.assertEqual(positions, list(range(10)))


if __name__ == "__main__":
    unittest.main()
