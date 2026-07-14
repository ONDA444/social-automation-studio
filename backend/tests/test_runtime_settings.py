from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend import runtime_settings


class RuntimeSettingsCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        # Reset module-level cache state before each test.
        runtime_settings._CACHE = {}
        runtime_settings._CACHE_AT = 0.0

    def test_cache_is_used_when_table_is_empty(self) -> None:
        """When app_settings has zero rows, _load_raw must still cache and
        avoid re-querying the DB on the next call within the TTL."""
        mock_db = MagicMock()
        mock_db.query.return_value.all.return_value = []  # empty table

        with patch.object(runtime_settings, "SessionLocal", return_value=mock_db):
            runtime_settings.get("monetization_cta")
            runtime_settings.get("monetization_cta")

        self.assertEqual(mock_db.query.call_count, 1)


if __name__ == "__main__":
    unittest.main()
