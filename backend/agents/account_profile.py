"""
AccountProfileAgent — workspace/account management (one queue/schedule/config
per connected channel) plus credential and quota handling.

Implemented as a thin service over the DB (used by routers + publisher).
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from backend.crypto import decrypt_credentials, encrypt_credentials
from backend.models import PlatformAccount, VideoJob

# Default daily quota guidance per platform.
DEFAULT_QUOTA = {"youtube": 10000, "tiktok": 1000, "instagram": 1000}
# Cost of one upload (YouTube Data API: ~1600 units; ~6 uploads/day).
UPLOAD_COST = {"youtube": 1600, "tiktok": 1, "instagram": 1}


class AccountProfileService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- CRUD ----
    def create(self, **fields) -> PlatformAccount:
        platform = fields.get("platform", "youtube")
        fields.setdefault("quota_limit", DEFAULT_QUOTA.get(platform, 1000))
        acct = PlatformAccount(**fields)
        self.db.add(acct)
        self.db.commit()
        self.db.refresh(acct)
        return acct

    def get(self, account_id: int) -> PlatformAccount | None:
        return self.db.get(PlatformAccount, account_id)

    def list(self, platform: str | None = None) -> list[PlatformAccount]:
        stmt = select(PlatformAccount)
        if platform:
            stmt = stmt.where(PlatformAccount.platform == platform)
        return list(self.db.execute(stmt).scalars().all())

    # ---- credentials ----
    def set_credentials(self, account_id: int, creds: dict) -> None:
        acct = self.get(account_id)
        if not acct:
            raise ValueError("account not found")
        previous = decrypt_credentials(acct.credentials_encrypted)
        if acct.platform == "youtube" and previous and not creds.get("refresh_token"):
            for key in ("refresh_token", "token_uri", "client_id", "client_secret"):
                if previous.get(key) and not creds.get(key):
                    creds[key] = previous[key]
        acct.credentials_encrypted = encrypt_credentials(creds)
        # A successful (re)connect means the account is usable again. Reactivate
        # from any connection-blocking state — not just "auth_error". Otherwise a
        # channel that was "disconnected" (via the Disconnect button) would store
        # fresh tokens but stay "disconnected", so the pipeline would never
        # auto-publish to it (get_active_account / can_upload require "active").
        if acct.status in ("auth_error", "disconnected", "paused"):
            acct.status = "active"
        self.db.commit()

    def get_credentials(self, account_id: int) -> dict:
        acct = self.get(account_id)
        return decrypt_credentials(acct.credentials_encrypted) if acct else {}

    def has_valid_credentials(self, account: PlatformAccount) -> bool:
        """True if the account has usable connected credentials.

        For YouTube a refresh_token is required (without it google-auth cannot
        refresh and the upload fails with a generic RefreshError); for the other
        platforms a non-empty credentials dict is enough.
        """
        creds = decrypt_credentials(account.credentials_encrypted)
        if not creds:
            return False
        if account.platform == "youtube":
            return bool(creds.get("refresh_token"))
        return True

    # ---- selection / quota ----
    def get_active_account(self, platform: str) -> PlatformAccount | None:
        """Active account on a platform with the most remaining quota.

        Accounts without valid connected credentials are ignored so we never
        pick an empty profile over one that can actually upload.
        """
        accts = [
            a for a in self.list(platform)
            if a.status == "active" and self.has_valid_credentials(a)
        ]
        if not accts:
            return None
        return max(accts, key=lambda a: (a.quota_limit - a.quota_used_today))

    def can_upload(self, account_id: int) -> bool:
        acct = self.get(account_id)
        if not acct or acct.status != "active":
            return False
        cost = UPLOAD_COST.get(acct.platform, 1)
        return (acct.quota_used_today + cost) <= acct.quota_limit

    def record_upload(self, account_id: int) -> None:
        acct = self.get(account_id)
        if not acct:
            return
        acct.quota_used_today += UPLOAD_COST.get(acct.platform, 1)
        if acct.quota_used_today >= acct.quota_limit:
            acct.status = "quota_exceeded"
        self.db.commit()

    def reset_daily_quota(self) -> int:
        """Called after midnight to reset quota and re-activate paused accounts."""
        n = 0
        for acct in self.list():
            acct.quota_used_today = 0
            if acct.status == "quota_exceeded":
                acct.status = "active"
            n += 1
        self.db.commit()
        return n

    # ---- status ----
    def pause(self, account_id: int, reason: str = "paused") -> None:
        acct = self.get(account_id)
        if acct:
            acct.status = reason
            self.db.commit()

    def mark_auth_error(self, account_id: int) -> None:
        self.pause(account_id, "auth_error")

    # ---- linking ----
    def link_accounts(self, source_id: int, links: dict, mirror: bool = True,
                      mirror_format: str = "shorts_30s") -> PlatformAccount | None:
        acct = self.get(source_id)
        if not acct:
            return None
        acct.linked_accounts = {**(acct.linked_accounts or {}), **links}
        acct.mirror_to_linked = mirror
        acct.mirror_format = mirror_format
        self.db.commit()
        self.db.refresh(acct)
        return acct

    def get_workspace_queue(self, account_id: int) -> list[VideoJob]:
        stmt = select(VideoJob).where(VideoJob.account_id == account_id).order_by(
            VideoJob.created_at.desc()
        )
        return list(self.db.execute(stmt).scalars().all())
