from __future__ import annotations

import unittest

from backend.llm import _balanced_object, _repair_truncated, extract_json


class ExtractJsonPlainTests(unittest.TestCase):
    def test_plain_object_parses(self) -> None:
        self.assertEqual(extract_json('{"a": 1, "b": "x"}'), {"a": 1, "b": "x"})

    def test_leading_prose_preamble_is_skipped(self) -> None:
        text = 'We need to produce JSON. Here it is:\n{"title": "Hook", "score": 9}'
        self.assertEqual(extract_json(text), {"title": "Hook", "score": 9})

    def test_trailing_prose_after_object_is_ignored(self) -> None:
        text = '{"title": "Hook"}\n\nLet me know if you need anything else.'
        self.assertEqual(extract_json(text), {"title": "Hook"})

    def test_trailing_comma_before_closing_brace_is_tolerated(self) -> None:
        text = '{"a": 1, "b": 2,}'
        self.assertEqual(extract_json(text), {"a": 1, "b": 2})

    def test_trailing_comma_before_closing_bracket_is_tolerated(self) -> None:
        text = '{"items": [1, 2, 3,]}'
        self.assertEqual(extract_json(text), {"items": [1, 2, 3]})

    def test_raises_value_error_when_no_brace_present(self) -> None:
        with self.assertRaises(ValueError):
            extract_json("no json here at all")

    def test_empty_string_raises(self) -> None:
        with self.assertRaises(ValueError):
            extract_json("")


class ExtractJsonMarkdownFenceTests(unittest.TestCase):
    def test_closed_json_fence_is_stripped(self) -> None:
        text = '```json\n{"a": 1}\n```'
        self.assertEqual(extract_json(text), {"a": 1})

    def test_unclosed_fence_from_truncation_is_still_parsed(self) -> None:
        text = '```json\n{"a": 1, "b": 2}'
        self.assertEqual(extract_json(text), {"a": 1, "b": 2})

    def test_generic_fence_without_json_tag_is_stripped(self) -> None:
        text = '```\n{"a": 1}\n```'
        self.assertEqual(extract_json(text), {"a": 1})


class ExtractJsonBraceInStringTests(unittest.TestCase):
    def test_brace_characters_inside_string_values_do_not_confuse_balancing(self) -> None:
        text = '{"code": "if (x) { return 1; }", "ok": true}'
        self.assertEqual(extract_json(text), {"code": "if (x) { return 1; }", "ok": True})

    def test_escaped_quote_inside_string_does_not_end_string_early(self) -> None:
        text = r'{"quote": "she said \"hi\"", "n": 2}'
        self.assertEqual(extract_json(text), {"quote": 'she said "hi"', "n": 2})


class ExtractJsonMultipleCandidatesTests(unittest.TestCase):
    def test_first_brace_that_is_stray_prose_falls_through_to_real_object(self) -> None:
        # A '{' appearing inside prose before the real JSON object balances but
        # is not valid JSON (unquoted keys) — the parser must keep scanning for
        # the next '{' instead of giving up.
        text = 'Think of it like a set { of ideas }, then: {"real": true}'
        self.assertEqual(extract_json(text), {"real": True})

    def test_first_balanced_but_invalid_json_object_is_skipped(self) -> None:
        # First '{...}' is balanced (braces match) but isn't valid JSON
        # (single-quoted keys) — extraction should move on to the next '{'.
        text = "{'not': 'valid json'} then the actual answer: {\"valid\": 1}"
        self.assertEqual(extract_json(text), {"valid": 1})


class BalancedObjectTests(unittest.TestCase):
    def test_returns_matching_substring(self) -> None:
        text = '{"a": {"b": 1}} trailing'
        self.assertEqual(_balanced_object(text, 0), '{"a": {"b": 1}}')

    def test_returns_none_when_never_closed(self) -> None:
        text = '{"a": 1, "b": [1, 2'
        self.assertIsNone(_balanced_object(text, 0))

    def test_brace_inside_string_is_not_counted(self) -> None:
        text = '{"s": "}}}"}'
        self.assertEqual(_balanced_object(text, 0), text)


class RepairTruncatedTests(unittest.TestCase):
    def test_closes_open_object(self) -> None:
        self.assertEqual(_repair_truncated('{"a": 1, "b": 2'), '{"a": 1, "b": 2}')

    def test_closes_open_array_then_object(self) -> None:
        self.assertEqual(_repair_truncated('{"items": [1, 2, 3'), '{"items": [1, 2, 3]}')

    def test_closes_unterminated_string_before_containers(self) -> None:
        self.assertEqual(_repair_truncated('{"title": "cut off mid'), '{"title": "cut off mid"}')

    def test_drops_dangling_comma_before_closing(self) -> None:
        self.assertEqual(_repair_truncated('{"a": 1, "b": 2,'), '{"a": 1, "b": 2}')

    def test_ignores_text_before_first_brace(self) -> None:
        self.assertEqual(_repair_truncated('noise before {"a": 1'), '{"a": 1}')

    def test_raises_value_error_when_no_brace_present(self) -> None:
        with self.assertRaises(ValueError):
            _repair_truncated("no braces here")

    def test_result_is_valid_json_end_to_end_via_extract_json(self) -> None:
        # A response cut mid-array by a token cap: no closing anywhere.
        truncated = '{"title": "Video", "tags": ["a", "b", "c'
        self.assertEqual(
            extract_json(truncated),
            {"title": "Video", "tags": ["a", "b", "c"]},
        )

    def test_nested_truncation_closes_innermost_container_first(self) -> None:
        truncated = '{"meta": {"tags": ["x", "y"'
        self.assertEqual(extract_json(truncated), {"meta": {"tags": ["x", "y"]}})


if __name__ == "__main__":
    unittest.main()
