from __future__ import annotations

import unittest
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from backend import scheduler


class SlotsDueTodayTests(unittest.TestCase):
    """Pure pacing function: how many of today's posting slots have already
    arrived. Bugs here directly silence or flood a channel's automation, so
    the edge cases (malformed config, bad timezone) matter as much as the
    happy path."""

    def test_all_malformed_hhmm_times_yield_zero_due_forever(self) -> None:
        """A malformed hhmm_times list must never crash — but it also means
        `times` ends up empty, so due is always 0 and the channel silently
        stops posting until the config is fixed."""
        late_utc = datetime(2026, 1, 1, 23, 0, tzinfo=timezone.utc)
        due = scheduler._slots_due_today(["not-a-time", "25:99xyz"], per_day=3, tz_name="UTC", now_utc=late_utc)
        self.assertEqual(due, 0)

    def test_invalid_timezone_falls_back_to_sao_paulo(self) -> None:
        now_utc = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
        with_bogus_tz = scheduler._slots_due_today(["10:00"], per_day=1, tz_name="Not/ARealZone", now_utc=now_utc)
        with_explicit_fallback = scheduler._slots_due_today(
            ["10:00"], per_day=1, tz_name="America/Sao_Paulo", now_utc=now_utc
        )
        self.assertEqual(with_bogus_tz, with_explicit_fallback)

    def test_missing_timezone_falls_back_to_sao_paulo(self) -> None:
        now_utc = datetime(2026, 1, 1, 15, 0, tzinfo=timezone.utc)
        with_none = scheduler._slots_due_today(["10:00"], per_day=1, tz_name=None, now_utc=now_utc)
        with_explicit = scheduler._slots_due_today(
            ["10:00"], per_day=1, tz_name="America/Sao_Paulo", now_utc=now_utc
        )
        self.assertEqual(with_none, with_explicit)

    def test_per_day_larger_than_configured_slots_is_bounded_by_actual_slots(self) -> None:
        """per_day is a CAP, not a promise of that many slots — with only 2
        real hhmm_times configured, due can never exceed 2 even if per_day=10."""
        now_utc = datetime(2026, 1, 1, 23, 0, tzinfo=ZoneInfo("America/Sao_Paulo")).astimezone(timezone.utc)
        due = scheduler._slots_due_today(["08:00", "20:00"], per_day=10, tz_name="America/Sao_Paulo", now_utc=now_utc)
        self.assertEqual(due, 2)

    def test_only_slots_at_or_before_now_count_as_due(self) -> None:
        tz = ZoneInfo("America/Sao_Paulo")
        # 12:30 local: the 08:00 slot is due, 20:00 is not yet.
        now_local = datetime(2026, 1, 1, 12, 30, tzinfo=tz)
        due = scheduler._slots_due_today(
            ["08:00", "20:00"], per_day=2, tz_name="America/Sao_Paulo", now_utc=now_local.astimezone(timezone.utc)
        )
        self.assertEqual(due, 1)

    def test_no_hhmm_times_defaults_to_a_single_19_00_slot(self) -> None:
        tz = ZoneInfo("America/Sao_Paulo")
        before = datetime(2026, 1, 1, 10, 0, tzinfo=tz)
        after = datetime(2026, 1, 1, 20, 0, tzinfo=tz)
        self.assertEqual(
            scheduler._slots_due_today([], per_day=1, tz_name="America/Sao_Paulo",
                                        now_utc=before.astimezone(timezone.utc)),
            0,
        )
        self.assertEqual(
            scheduler._slots_due_today([], per_day=1, tz_name="America/Sao_Paulo",
                                        now_utc=after.astimezone(timezone.utc)),
            1,
        )

    def test_per_day_zero_or_negative_is_floored_to_one_slot(self) -> None:
        tz = ZoneInfo("America/Sao_Paulo")
        after_first_slot = datetime(2026, 1, 1, 21, 0, tzinfo=tz)
        due = scheduler._slots_due_today(
            ["08:00", "20:00"], per_day=0, tz_name="America/Sao_Paulo",
            now_utc=after_first_slot.astimezone(timezone.utc),
        )
        # per_day floored to 1 -> only the earliest slot (08:00) is considered.
        self.assertEqual(due, 1)


if __name__ == "__main__":
    unittest.main()
