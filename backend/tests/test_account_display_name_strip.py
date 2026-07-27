from __future__ import annotations

import unittest

from backend.routers.accounts import AccountCreate, AccountUpdate


class DisplayNameStripTests(unittest.TestCase):
    """Regression test: a display_name saved with a leading/trailing space
    (e.g. pasted from elsewhere) rendered visually identical to the
    unspaced version once inside PlatformCard.jsx's <h3> (HTML collapses
    whitespace) — confirmed in production as the root cause of an
    indistinguishable duplicate "Beic Conste" account that looked identical
    to the real one on screen but was actually a second, empty row."""

    def test_create_strips_leading_and_trailing_whitespace(self) -> None:
        payload = AccountCreate(platform="youtube", display_name=" Beic Conste")
        self.assertEqual(payload.display_name, "Beic Conste")

    def test_create_strips_trailing_whitespace_too(self) -> None:
        payload = AccountCreate(platform="youtube", display_name="ONDA HUB  ")
        self.assertEqual(payload.display_name, "ONDA HUB")

    def test_update_strips_whitespace_when_present(self) -> None:
        payload = AccountUpdate(display_name=" ONDA HUB")
        self.assertEqual(payload.display_name, "ONDA HUB")

    def test_update_leaves_absent_display_name_as_none(self) -> None:
        payload = AccountUpdate(niche="comedia")
        self.assertIsNone(payload.display_name)


if __name__ == "__main__":
    unittest.main()
