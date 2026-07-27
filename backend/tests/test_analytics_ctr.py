from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.agents.analytics import AnalyticsAgent


class TestYoutubeCtrCollection(unittest.TestCase):
    """Regression test: ctr/impressions must be populated by the YouTube collector
    (previously only watch-time metrics were fetched, so VideoAnalytics.ctr always
    stayed at its 0.0 default and thumbnail_ab_summary()'s avg_ctr was always 0)."""

    def test_youtube_watchtime_includes_ctr_and_impressions(self):
        watchtime_resp = {
            "columnHeaders": [
                {"name": "estimatedMinutesWatched"},
                {"name": "averageViewDuration"},
                {"name": "averageViewPercentage"},
                {"name": "subscribersGained"},
            ],
            "rows": [[120, 45.0, 60.0, 3]],
        }
        ctr_resp = {
            "columnHeaders": [
                {"name": "impressions"},
                {"name": "impressionsClickThroughRate"},
            ],
            "rows": [[1000, 4.5]],
        }

        fake_ya = MagicMock()
        fake_ya.reports.return_value.query.return_value.execute.side_effect = [
            watchtime_resp,
            ctr_resp,
        ]

        with patch("googleapiclient.discovery.build", return_value=fake_ya), \
             patch("backend.uploaders.youtube._credentials", return_value=object()):
            result, error = AnalyticsAgent._youtube_watchtime("vid123", {"token": "x"})

        self.assertIsNotNone(result)
        self.assertIsNone(error)
        self.assertEqual(result["impressions"], 1000)
        self.assertEqual(result["ctr"], 4.5)
        self.assertEqual(result["watch_minutes"], 120)



class TestThumbnailVariantDetection(unittest.TestCase):
    """Regression test: thumbnail_variant was hardcoded to "A" on every VideoAnalytics
    row, so thumbnail_ab_summary() could never see a "B" group. AnalyticsAgent should
    instead read back which variant was actually published from the job's stored
    thumbnail filename (thumb_A.png / thumb_B.png, see visuals.py)."""

    def test_variant_b_detected_from_thumbnail_path(self):
        self.assertEqual(
            AnalyticsAgent._thumbnail_variant("/assets/42/thumb_B.png"), "B"
        )

    def test_variant_a_detected_from_thumbnail_path(self):
        self.assertEqual(
            AnalyticsAgent._thumbnail_variant("/assets/42/thumb_A.png"), "A"
        )

    def test_missing_path_defaults_to_a(self):
        self.assertEqual(AnalyticsAgent._thumbnail_variant(None), "A")


if __name__ == "__main__":
    unittest.main()
