from __future__ import annotations

import logging
import unittest
from unittest.mock import patch

from backend.config import settings
from backend.crypto import decrypt_credentials, encrypt_credentials


class CryptoRoundTripTests(unittest.TestCase):
    def test_encrypt_then_decrypt_returns_original_data(self) -> None:
        data = {"refresh_token": "rt-123", "client_id": "abc", "n": 1}
        token = encrypt_credentials(data)
        self.assertEqual(decrypt_credentials(token), data)

    def test_encrypted_token_is_not_plaintext(self) -> None:
        token = encrypt_credentials({"refresh_token": "super-secret-value"})
        self.assertNotIn("super-secret-value", token)

    def test_decrypt_none_returns_empty_dict(self) -> None:
        self.assertEqual(decrypt_credentials(None), {})

    def test_decrypt_empty_string_returns_empty_dict(self) -> None:
        self.assertEqual(decrypt_credentials(""), {})

    def test_decrypt_garbage_token_returns_empty_dict(self) -> None:
        self.assertEqual(decrypt_credentials("not-a-valid-fernet-token"), {})


class CryptoSecretKeyRotationTests(unittest.TestCase):
    def test_token_from_old_secret_key_fails_open_after_rotation(self) -> None:
        with patch.object(settings, "secret_key", "old-secret-key"):
            token = encrypt_credentials({"refresh_token": "rt-123"})
        with patch.object(settings, "secret_key", "new-secret-key"):
            self.assertEqual(decrypt_credentials(token), {})

    def test_decrypt_failure_is_logged(self) -> None:
        with patch.object(settings, "secret_key", "old-secret-key"):
            token = encrypt_credentials({"refresh_token": "rt-123"})
        with patch.object(settings, "secret_key", "new-secret-key"):
            with self.assertLogs("backend.crypto", level="ERROR") as captured:
                decrypt_credentials(token)
        self.assertTrue(
            any("decrypt_credentials" in line for line in captured.output)
        )

    def test_decrypt_none_does_not_log(self) -> None:
        logger = logging.getLogger("backend.crypto")
        with patch.object(logger, "error") as mock_error:
            decrypt_credentials(None)
        mock_error.assert_not_called()


if __name__ == "__main__":
    unittest.main()
