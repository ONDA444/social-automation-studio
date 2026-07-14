from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import MagicMock

from backend.agents.trending_moment import TrendingMomentAgent


class PytrendsLanguageTest(unittest.TestCase):
    def test_pytrends_uses_given_language_not_hardcoded_pt_br(self):
        """_pytrends must honor the agent's `language` param for hl=, not always pt-BR."""
        fake_trendreq = MagicMock()
        fake_module = types.ModuleType("pytrends.request")
        fake_module.TrendReq = fake_trendreq
        pytrends_pkg = types.ModuleType("pytrends")
        pytrends_pkg.request = fake_module
        sys.modules["pytrends"] = pytrends_pkg
        sys.modules["pytrends.request"] = fake_module
        try:
            TrendingMomentAgent._pytrends("cats", "en-US", "US")
        finally:
            del sys.modules["pytrends"]
            del sys.modules["pytrends.request"]

        fake_trendreq.assert_called_once()
        _, kwargs = fake_trendreq.call_args
        self.assertEqual(kwargs.get("hl"), "en-US")


if __name__ == "__main__":
    unittest.main()
