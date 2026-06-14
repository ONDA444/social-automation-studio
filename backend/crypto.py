"""
Fernet credential encryption.

The Fernet key is derived from SECRET_KEY so OAuth tokens are never stored in
plaintext. Rotating SECRET_KEY invalidates stored credentials (accounts must
re-auth) — expected behaviour.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from backend.config import settings


def _fernet() -> Fernet:
    digest = hashlib.sha256(settings.secret_key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_credentials(data: dict[str, Any]) -> str:
    return _fernet().encrypt(json.dumps(data).encode()).decode()


def decrypt_credentials(token: str | None) -> dict[str, Any]:
    if not token:
        return {}
    try:
        return json.loads(_fernet().decrypt(token.encode()).decode())
    except (InvalidToken, ValueError):
        return {}
