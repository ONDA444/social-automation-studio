from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from backend.agents.keyword_research import related_search_queries


class RelatedSearchQueriesTests(unittest.TestCase):
    def test_empty_seed_returns_empty_without_network(self) -> None:
        self.assertEqual(related_search_queries(""), [])
        self.assertEqual(related_search_queries("   "), [])

    def test_network_failure_returns_empty_list(self) -> None:
        with patch("pytrends.request.TrendReq") as mock_cls:
            mock_cls.side_effect = RuntimeError("blocked by datacenter IP")
            self.assertEqual(related_search_queries("video engraçado"), [])

    def test_merges_top_and_rising_deduped(self) -> None:
        import pandas as pd

        top_df = pd.DataFrame({"query": ["termo a", "termo B", "termo a"]})
        rising_df = pd.DataFrame({"query": ["termo b", "termo c"]})
        mock_py = MagicMock()
        mock_py.related_queries.return_value = {
            "video engraçado": {"top": top_df, "rising": rising_df}
        }
        with patch("pytrends.request.TrendReq", return_value=mock_py):
            result = related_search_queries("video engraçado")
        self.assertEqual(result, ["termo a", "termo B", "termo c"])


if __name__ == "__main__":
    unittest.main()
