from __future__ import annotations

import unittest

from backend.error_messages import friendly_error


class FriendlyErrorTests(unittest.TestCase):
    def test_none_and_empty_pass_through_as_none(self) -> None:
        self.assertIsNone(friendly_error(None))
        self.assertIsNone(friendly_error(""))

    def test_dead_oauth_token_is_translated(self) -> None:
        msg = "Falha ao baixar video do Drive: ('invalid_grant: Token has been expired or revoked.', ...)"
        self.assertIn("Reconecte o canal", friendly_error(msg))

    def test_youtube_upload_limit_is_translated(self) -> None:
        msg = "HttpError 400 ... 'The user has exceeded the number of videos they may upload.'"
        self.assertIn("limite diário de uploads", friendly_error(msg))

    def test_unknown_error_falls_back_to_original_text(self) -> None:
        msg = "Some brand-new failure mode nobody has seen before"
        self.assertEqual(friendly_error(msg), msg)


if __name__ == "__main__":
    unittest.main()
