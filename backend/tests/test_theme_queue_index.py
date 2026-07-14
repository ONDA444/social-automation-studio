"""Regression test: theme_queue must have a composite index on
(account_id, status), since the scheduler's hot-path FIFO lookup
(backend/scheduler.py) and the queue router (backend/routers/themes.py)
both filter ThemeQueue by account_id == X AND status == 'pending' on every
cycle. Without a composite index, only `status` is indexed and the query
degrades to a full scan of all pending rows across every account as the
table grows.
"""
import unittest

from backend.models.theme_queue import ThemeQueue


class ThemeQueueIndexTests(unittest.TestCase):
    def test_has_composite_index_on_account_id_and_status(self):
        indexed_column_sets = {
            tuple(col.name for col in index.columns)
            for index in ThemeQueue.__table__.indexes
        }
        self.assertIn(
            ("account_id", "status"),
            indexed_column_sets,
            "Expected a composite index covering (account_id, status) on "
            "theme_queue to keep the scheduler's per-account pending lookup "
            "fast as the table grows.",
        )


if __name__ == "__main__":
    unittest.main()
