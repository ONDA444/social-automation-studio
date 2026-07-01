"""Google Drive ready-video library service."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Iterable

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from backend.config import settings
from backend.crypto import decrypt_credentials, encrypt_credentials
from backend.models import DriveConnection, PlatformAccount, ReadyVideo

logger = logging.getLogger("studio.drive_library")

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
VIDEO_MIME_PREFIX = "video/"
FOLDER_MIME = "application/vnd.google-apps.folder"


def extract_folder_id(value: str | None) -> str | None:
    """Accept a raw Drive id or a folders/... URL."""
    if not value:
        return None
    value = value.strip()
    patterns = [
        r"/folders/([A-Za-z0-9_-]+)",
        r"[?&]id=([A-Za-z0-9_-]+)",
    ]
    for pattern in patterns:
        m = re.search(pattern, value)
        if m:
            return m.group(1)
    return value if re.fullmatch(r"[A-Za-z0-9_-]{10,}", value) else None


def guess_format(name: str, mime_type: str | None = None) -> str:
    text = f"{name} {mime_type or ''}".lower()
    if any(token in text for token in ("short", "reels", "tiktok", "9x16", "vertical")):
        return "short"
    return "long"


class DriveLibraryService:
    def __init__(self, db: Session) -> None:
        self.db = db

    # ---- connection -----------------------------------------------------
    def get_connection(self) -> DriveConnection:
        conn = self.db.execute(select(DriveConnection).order_by(DriveConnection.id)).scalars().first()
        if conn:
            return conn
        conn = DriveConnection(label="default", status="disconnected")
        self.db.add(conn)
        self.db.commit()
        self.db.refresh(conn)
        return conn

    def build_auth_url(self) -> dict:
        if not settings.google_client_id or not settings.google_client_secret:
            return {"ok": False, "error": "GOOGLE_CLIENT_ID/GOOGLE_CLIENT_SECRET nao configurados."}
        flow = self._flow()
        auth_url, _ = flow.authorization_url(
            access_type="offline",
            prompt="consent",
        )
        return {"ok": True, "auth_url": auth_url}

    def exchange_code(self, code: str) -> dict:
        flow = self._flow()
        flow.fetch_token(code=code)
        creds = flow.credentials
        conn = self.get_connection()
        conn.credentials_encrypted = encrypt_credentials(self._credentials_to_dict(creds))
        conn.status = "connected"
        self.db.commit()
        return conn.to_dict()

    def disconnect(self) -> dict:
        conn = self.get_connection()
        conn.credentials_encrypted = None
        conn.status = "disconnected"
        self.db.commit()
        return conn.to_dict()

    def status(self) -> dict:
        conn = self.get_connection()
        return {
            **conn.to_dict(),
            "api_key_configured": bool(settings.google_drive_api_key),
        }

    def _flow(self):
        try:
            from google_auth_oauthlib.flow import Flow
        except ModuleNotFoundError as exc:
            raise RuntimeError("google-auth-oauthlib nao instalado. Rode pip install -r backend/requirements.txt.") from exc
        config = {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [settings.google_drive_redirect_uri],
            }
        }
        return Flow.from_client_config(config, scopes=DRIVE_SCOPES, redirect_uri=settings.google_drive_redirect_uri)

    @staticmethod
    def _credentials_to_dict(creds) -> dict:
        return {
            "token": creds.token,
            "refresh_token": creds.refresh_token,
            "token_uri": creds.token_uri,
            "client_id": creds.client_id,
            "client_secret": creds.client_secret,
            "scopes": creds.scopes,
        }

    def _service(self):
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from googleapiclient.discovery import build
        except ModuleNotFoundError as exc:
            raise RuntimeError("Google Drive libs nao instaladas. Rode pip install -r backend/requirements.txt.") from exc
        conn = self.get_connection()
        raw = decrypt_credentials(conn.credentials_encrypted)
        if raw:
            creds = Credentials(**raw)
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                conn.credentials_encrypted = encrypt_credentials(self._credentials_to_dict(creds))
                conn.status = "connected"
                self.db.commit()
            return build("drive", "v3", credentials=creds, cache_discovery=False)
        if settings.google_drive_api_key:
            return build("drive", "v3", developerKey=settings.google_drive_api_key, cache_discovery=False)
        raise RuntimeError("Drive nao conectado. Conecte em /drive-library/auth/start ou configure GOOGLE_DRIVE_API_KEY.")

    # ---- indexing -------------------------------------------------------
    def index_folder(
        self,
        folder_id_or_url: str,
        *,
        niche: str | None = None,
        content_type: str = "auto",
        video_format: str | None = None,
        account_id: int | None = None,
        recursive: bool = True,
    ) -> dict:
        folder_id = extract_folder_id(folder_id_or_url)
        if not folder_id:
            raise ValueError("Pasta do Drive invalida.")
        svc = self._service()
        seen: set[str] = set()
        imported = 0
        updated = 0
        for item, path in self._walk_folder(svc, folder_id, recursive=recursive):
            if not (item.get("mimeType") or "").startswith(VIDEO_MIME_PREFIX):
                continue
            seen.add(item["id"])
            existing = self.db.execute(
                select(ReadyVideo).where(ReadyVideo.drive_file_id == item["id"])
            ).scalars().first()
            if existing:
                row = existing
                updated += 1
            else:
                row = ReadyVideo(drive_file_id=item["id"])
                self.db.add(row)
                imported += 1
            row.drive_folder_id = folder_id
            row.name = item.get("name") or "video"
            row.mime_type = item.get("mimeType")
            row.niche = niche or self._guess_niche(path)
            row.folder_path = " / ".join(path)
            row.content_type = content_type or "auto"
            row.video_format = video_format or guess_format(row.name, row.mime_type)
            row.account_id = account_id
            row.size_bytes = int(item["size"]) if item.get("size", "").isdigit() else None
            row.metadata_json = {
                **(row.metadata_json or {}),
                "webViewLink": item.get("webViewLink"),
                "modifiedTime": item.get("modifiedTime"),
                "drive_path": path,
            }
            if row.status == "missing":
                row.status = "available"
        self.db.commit()
        return {"folder_id": folder_id, "imported": imported, "updated": updated, "seen": len(seen)}

    def _walk_folder(self, svc, folder_id: str, *, recursive: bool, path: list[str] | None = None):
        path = path or []
        page_token = None
        while True:
            resp = svc.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields="nextPageToken, files(id,name,mimeType,size,modifiedTime,webViewLink)",
                orderBy="folder,name_natural",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for item in resp.get("files", []):
                item_path = [*path, item.get("name") or ""]
                if item.get("mimeType") == FOLDER_MIME:
                    if recursive:
                        yield from self._walk_folder(svc, item["id"], recursive=recursive, path=item_path)
                else:
                    yield item, item_path
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    @staticmethod
    def _guess_niche(path: Iterable[str]) -> str | None:
        parts = [p.strip() for p in path if p and p.strip()]
        return parts[0] if parts else None

    # ---- selection/download --------------------------------------------
    def reserve_next(
        self,
        account: PlatformAccount,
        *,
        content_type: str,
        video_format: str,
        fallback_format: str | None = None,
    ) -> ReadyVideo | None:
        row = self._reserve_matching(account, content_type=content_type, video_format=video_format)
        if row or not fallback_format or fallback_format == video_format:
            return row
        return self._reserve_matching(account, content_type=content_type, video_format=fallback_format)

    def _reserve_matching(self, account: PlatformAccount, *, content_type: str, video_format: str) -> ReadyVideo | None:
        niche = (account.drive_niche or account.niche or "").strip().lower()
        conditions = [
            ReadyVideo.status == "available",
            or_(ReadyVideo.account_id.is_(None), ReadyVideo.account_id == account.id),
            or_(ReadyVideo.content_type == content_type, ReadyVideo.content_type == "auto"),
            ReadyVideo.video_format == video_format,
        ]
        if niche:
            conditions.append(or_(ReadyVideo.niche.is_(None), ReadyVideo.niche.ilike(f"%{niche}%")))
        stmt = (
            select(ReadyVideo)
            .where(and_(*conditions))
            .order_by(ReadyVideo.account_id.desc(), ReadyVideo.created_at.asc(), ReadyVideo.id.asc())
            .limit(1)
        )
        row = self.db.execute(stmt).scalars().first()
        if not row:
            return None
        row.status = "reserved"
        row.reserved_at = datetime.utcnow()
        self.db.flush()
        return row

    def download_for_job(self, ready: ReadyVideo, job_id: int) -> str:
        try:
            from googleapiclient.http import MediaIoBaseDownload
        except ModuleNotFoundError as exc:
            raise RuntimeError("google-api-python-client nao instalado. Rode pip install -r backend/requirements.txt.") from exc
        base = settings.abs_path(settings.temp_dir) / "ready_videos" / f"job_{job_id}"
        base.mkdir(parents=True, exist_ok=True)
        safe_name = re.sub(r"[^A-Za-z0-9._-]+", "_", ready.name)[:180] or f"ready_{ready.id}.mp4"
        dest = base / safe_name
        if dest.exists() and dest.stat().st_size > 0:
            ready.local_path = str(dest)
            self.db.commit()
            return str(dest)
        request = self._service().files().get_media(fileId=ready.drive_file_id, supportsAllDrives=True)
        with dest.open("wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024 * 8)
            done = False
            while not done:
                _, done = downloader.next_chunk()
        ready.local_path = str(dest)
        self.db.commit()
        return str(dest)

    def mark_used(self, ready_id: int, job_id: int) -> None:
        row = self.db.get(ReadyVideo, ready_id)
        if not row:
            return
        row.status = "used"
        row.used_job_id = job_id
        row.used_at = datetime.utcnow()
        self.db.commit()
