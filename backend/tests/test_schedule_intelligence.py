from __future__ import annotations

import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from backend.agents.content_calendar import ContentCalendarAgent
from backend.routers.schedule import ScheduleConfigIn


class _Rows:
    def __init__(self, rows):
        self._rows = rows

    def all(self):
        return self._rows


class _Db:
    def __init__(self, rows):
        self._rows = rows

    def execute(self, *_args, **_kwargs):
        return _Rows(self._rows)


class ScheduleIntelligenceTests(unittest.TestCase):
    def test_smart_times_bucket_by_publish_time_not_collection_time(self) -> None:
        rows = [
            # 22:00 UTC is 19:00 in America/Sao_Paulo; collected_at is 00:00 UTC.
            (datetime(2026, 7, 1, 22, 0), datetime(2026, 7, 1, 21, 30), datetime(2026, 7, 2, 0, 0), 120),
            (datetime(2026, 7, 2, 22, 0), datetime(2026, 7, 2, 21, 30), datetime(2026, 7, 3, 0, 0), 90),
            (datetime(2026, 7, 3, 22, 0), datetime(2026, 7, 3, 21, 30), datetime(2026, 7, 4, 0, 0), 80),
        ]
        cal = ContentCalendarAgent(_Db(rows))

        learned = cal._smart_times(1, ZoneInfo("America/Sao_Paulo"))

        self.assertEqual(learned[0], "19:00")
        self.assertNotEqual(learned[0], "21:00")

    def test_best_times_are_spread_for_multiple_daily_posts(self) -> None:
        self.assertEqual(ContentCalendarAgent.best_times(3), ["12:00", "19:00", "21:00"])

    def test_schedule_payload_rejects_bad_values(self) -> None:
        with self.assertRaises(ValidationError):
            ScheduleConfigIn(mode="magic", videos_per_day=99, post_times=["9pm"])

        cfg = ScheduleConfigIn(mode="smart", videos_per_day=3, post_times=["19:00", "19:00", "21:00"])
        self.assertEqual(cfg.post_times, ["19:00", "21:00"])


if __name__ == "__main__":
    unittest.main()
