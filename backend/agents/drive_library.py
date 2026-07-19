"""Google Drive ready-video library service."""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime
from typing import Iterable
from urllib.parse import parse_qs, unquote_plus, urlparse

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from backend.config import settings
from backend.crypto import decrypt_credentials, encrypt_credentials
from backend.models import DriveConnection, JobStatus, PlatformAccount, ReadyVideo, VideoJob

logger = logging.getLogger("studio.drive_library")

DRIVE_SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
VIDEO_MIME_PREFIX = "video/"
AUDIO_MIME_PREFIX = "audio/"
FOLDER_MIME = "application/vnd.google-apps.folder"
SHORTCUT_MIME = "application/vnd.google-apps.shortcut"
VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v", ".mpeg", ".mpg"}


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


def extract_search_query(value: str | None) -> str | None:
    if not value:
        return None
    raw = value.strip()
    parsed = urlparse(raw)
    if "/drive/search" in parsed.path:
        query = parse_qs(parsed.query).get("q", [""])[0]
        return unquote_plus(query).strip() or None
    return None


def guess_format(name: str, mime_type: str | None = None) -> str:
    text = f"{name} {mime_type or ''}".lower()
    if any(token in text for token in ("short", "reels", "tiktok", "9x16", "vertical")):
        return "short"
    return "long"


def is_video_file(name: str, mime_type: str | None = None) -> bool:
    """Drive sometimes returns generic MIME types for shared video files."""
    mime = (mime_type or "").lower()
    if mime.startswith(VIDEO_MIME_PREFIX):
        return True
    lowered = (name or "").lower()
    return any(lowered.endswith(ext) for ext in VIDEO_EXTENSIONS)


def is_audio_file(name: str, mime_type: str | None = None) -> bool:
    mime = (mime_type or "").lower()
    if mime.startswith(AUDIO_MIME_PREFIX):
        return True
    lowered = (name or "").lower()
    return any(lowered.endswith(ext) for ext in (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"))


def normalize_drive_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    ascii_text = "".join(ch for ch in text if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", ascii_text.lower()).strip()


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
            import google_auth_httplib2
            import httplib2
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
            # httplib2 has NO default socket timeout — a stalled connect/read (dead
            # peer, blackholed route) blocks forever otherwise, which is exactly what
            # froze the shared render/publish worker loop (download_for_job runs
            # synchronously on that loop, not in a thread — see publisher.py).
            #
            # 30s, not 300s: a single next_chunk() call can otherwise stall for
            # minutes on its own, blowing past download_for_job's 240s wall-clock
            # loop deadline before that deadline ever gets checked again (see the
            # matching fix + full reasoning in uploaders/youtube.py's _service()).
            http = google_auth_httplib2.AuthorizedHttp(creds, http=httplib2.Http(timeout=30))
            return build("drive", "v3", http=http, cache_discovery=False)
        if settings.google_drive_api_key:
            http = httplib2.Http(timeout=30)
            return build("drive", "v3", developerKey=settings.google_drive_api_key, http=http, cache_discovery=False)
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
        return self._index_folder_id(
            svc,
            folder_id,
            niche=niche,
            content_type=content_type,
            video_format=video_format,
            account_id=account_id,
            recursive=recursive,
        )

    def index_matching_folders(
        self,
        query: str,
        *,
        niche: str | None = None,
        content_type: str = "auto",
        video_format: str | None = None,
        account_id: int | None = None,
        recursive: bool = True,
    ) -> dict:
        query = (query or "").strip()
        if not query:
            raise ValueError("Informe uma pasta do Drive ou um termo de busca.")
        svc = self._service()
        folder_ids = self._find_folders_by_name(svc, query)
        if not folder_ids:
            raise ValueError(f"Nenhuma pasta do Drive encontrada para: {query}")
        total = {
            "folder_id": ",".join(folder_ids),
            "matched_folders": len(folder_ids),
            "imported": 0,
            "updated": 0,
            "seen": 0,
            "files_seen": 0,
            "ignored_audio": 0,
            "ignored_non_video": 0,
            "stale": 0,
        }
        seen_file_ids: set[str] = set()
        for folder_id in folder_ids:
            result = self._index_folder_id(
                svc,
                folder_id,
                niche=niche or query,
                content_type=content_type,
                video_format=video_format,
                account_id=account_id,
                recursive=recursive,
            )
            for key in ("imported", "updated", "seen", "files_seen", "ignored_audio", "ignored_non_video"):
                total[key] += int(result.get(key) or 0)
            seen_file_ids.update(result.get("seen_file_ids") or [])
        total["stale"] = self._mark_stale_account_videos(account_id, seen_file_ids, folder_ids=folder_ids)
        return total

    def index_niche_tree(
        self,
        root_folder_id_or_url: str,
        *,
        niche: str | None,
        content_type: str = "auto",
        video_format: str | None = None,
        account_id: int | None = None,
        recursive: bool = True,
    ) -> dict:
        """Index the selected niche folder, or find it below a package/root folder.

        Users often paste the shared package/root folder and type a niche like
        "VIDEOS RELIGIOSOS". The actual Drive folder may be named
        "VÍDEOS RELIGIOSOS", then contain subfolders such as "Videos" and
        "Cortes séries". This resolves the niche folder first and then indexes
        every video below that folder.
        """
        folder_id = extract_folder_id(root_folder_id_or_url)
        if not folder_id:
            raise ValueError("Pasta do Drive invalida.")
        svc = self._service()
        roots = self._resolve_niche_roots(svc, folder_id, niche)
        if not roots:
            roots = [(folder_id, self._get_folder_name(svc, folder_id) or "")]
        total = {
            "folder_id": ",".join(folder_id for folder_id, _ in roots),
            "matched_folders": len(roots),
            "imported": 0,
            "updated": 0,
            "seen": 0,
            "files_seen": 0,
            "ignored_audio": 0,
            "ignored_non_video": 0,
            "stale": 0,
        }
        seen_file_ids: set[str] = set()
        for resolved_folder_id, resolved_name in roots:
            result = self._index_folder_id(
                svc,
                resolved_folder_id,
                niche=niche or resolved_name,
                content_type=content_type,
                video_format=video_format,
                account_id=account_id,
                recursive=recursive,
            )
            for key in ("imported", "updated", "seen", "files_seen", "ignored_audio", "ignored_non_video"):
                total[key] += int(result.get(key) or 0)
            seen_file_ids.update(result.get("seen_file_ids") or [])
        total["stale"] = self._mark_stale_account_videos(
            account_id, seen_file_ids, folder_ids=[folder_id for folder_id, _ in roots]
        )
        return total

    def _index_folder_id(
        self,
        svc,
        folder_id: str,
        *,
        niche: str | None,
        content_type: str,
        video_format: str | None,
        account_id: int | None,
        recursive: bool,
    ) -> dict:
        seen: set[str] = set()
        imported = 0
        updated = 0
        files_seen = 0
        ignored_audio = 0
        ignored_non_video = 0
        for item, path in self._walk_folder(svc, folder_id, recursive=recursive):
            files_seen += 1
            item_name = item.get("name") or "video"
            item_mime = item.get("mimeType")
            if is_audio_file(item_name, item_mime):
                ignored_audio += 1
                continue
            if not is_video_file(item_name, item_mime):
                ignored_non_video += 1
                continue
            seen.add(item["id"])
            folder_parts = path[:-1] if path else []
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
            # A row that's mid-download/publish ('reserved') or already published
            # ('used') must keep its assigned account/niche/format so an in-flight
            # job or historical record isn't reassigned out from under it by a
            # later or concurrent re-index sweep.
            if row.status not in ("reserved", "used"):
                row.drive_folder_id = folder_id
                row.name = item_name
                row.mime_type = item_mime
                row.niche = niche or self._guess_niche(folder_parts or path)
                row.folder_path = " / ".join(folder_parts)
                row.content_type = content_type or "auto"
                row.video_format = video_format or guess_format(row.name, row.mime_type)
                row.account_id = account_id
                row.size_bytes = int(item["size"]) if (item.get("size") or "").isdigit() else None
                row.metadata_json = {
                    **(row.metadata_json or {}),
                    "webViewLink": item.get("webViewLink"),
                    "modifiedTime": item.get("modifiedTime"),
                    "drive_path": path,
                    "folder_parts": folder_parts,
                }
                if row.status == "missing":
                    row.status = "available"
        self.db.commit()
        return {
            "folder_id": folder_id,
            "imported": imported,
            "updated": updated,
            "seen": len(seen),
            "seen_file_ids": list(seen),
            "files_seen": files_seen,
            "ignored_audio": ignored_audio,
            "ignored_non_video": ignored_non_video,
        }

    def _mark_stale_account_videos(
        self, account_id: int | None, seen_file_ids: set[str], *, folder_ids: list[str] | None = None
    ) -> int:
        if not account_id or self.db is None:
            return 0
        # Scope to the folder(s) this call actually walked (row.drive_folder_id is the
        # root folder_id passed to _index_folder_id). Without this, a call covering
        # only part of the account's Drive tree (e.g. a re-sync after drive_folder_url
        # changed to a different folder) would wipe rows/reservations belonging to
        # folders it never looked at.
        stale_statuses = {"available", "reserved", "rejected", "error", "missing"}
        query = select(ReadyVideo).where(ReadyVideo.account_id == account_id)
        if folder_ids:
            query = query.where(ReadyVideo.drive_folder_id.in_(folder_ids))
        rows = self.db.execute(query).scalars().all()
        changed = 0
        for row in rows:
            if row.drive_file_id in seen_file_ids or row.status == "used" or row.status not in stale_statuses:
                continue
            row.status = "missing"
            row.reserved_job_id = None
            row.reserved_at = None
            changed += 1
        if changed:
            self.db.commit()
        return changed

    def clear_account_inventory(self, account_id: int) -> dict:
        rows = self.db.execute(
            select(ReadyVideo).where(ReadyVideo.account_id == account_id)
        ).scalars().all()
        cleared = 0
        kept_used = 0
        for row in rows:
            if row.status == "used":
                kept_used += 1
                continue
            if row.status != "missing":
                row.status = "missing"
                row.reserved_job_id = None
                row.reserved_at = None
                cleared += 1
        self.db.commit()
        return {"cleared": cleared, "kept_used": kept_used}

    def _find_folders_by_name(self, svc, query: str) -> list[str]:
        escaped = query.replace("\\", "\\\\").replace("'", "\\'")
        page_token = None
        folders: list[str] = []
        while True:
            resp = svc.files().list(
                q=(
                    f"mimeType='{FOLDER_MIME}' and trashed=false and "
                    f"name contains '{escaped}'"
                ),
                fields="nextPageToken, files(id,name,mimeType)",
                orderBy="name_natural",
                pageSize=100,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            folders.extend(item["id"] for item in resp.get("files", []) if item.get("id"))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return folders

    def _get_folder_metadata(self, svc, folder_id: str) -> dict:
        try:
            return svc.files().get(
                fileId=folder_id,
                fields="id,name,mimeType,parents,shortcutDetails(targetId,targetMimeType)",
                supportsAllDrives=True,
            ).execute()
        except Exception:  # noqa: BLE001 - best-effort metadata only
            return {}

    def _get_folder_name(self, svc, folder_id: str) -> str | None:
        resp = self._get_folder_metadata(svc, folder_id)
        if not resp:
            return None
        return resp.get("name")

    def _resolve_niche_roots(self, svc, folder_id: str, niche: str | None) -> list[tuple[str, str]]:
        wanted = normalize_drive_name(niche)
        if not wanted:
            return [(folder_id, self._get_folder_name(svc, folder_id) or "")]

        ancestor_roots = self._resolve_niche_from_ancestors(svc, folder_id, wanted)
        if ancestor_roots:
            return ancestor_roots

        exact: list[tuple[str, str]] = []
        partial: list[tuple[str, str]] = []
        for item, path in self._walk_folders(svc, folder_id):
            item_name = item.get("name") or ""
            normalized = normalize_drive_name(item_name)
            row = (item["id"], item_name)
            if normalized == wanted:
                exact.append(row)
            elif wanted in normalized:
                partial.append(row)
        return exact or partial

    def _resolve_niche_from_ancestors(self, svc, folder_id: str, wanted: str) -> list[tuple[str, str]]:
        exact: list[tuple[str, str]] = []
        partial: list[tuple[str, str]] = []
        queue: list[tuple[str, int]] = [(folder_id, 0)]
        visited: set[str] = set()
        while queue:
            current_id, depth = queue.pop(0)
            if current_id in visited or depth > 8:
                continue
            visited.add(current_id)
            meta = self._get_folder_metadata(svc, current_id)
            current_name = meta.get("name") or ""
            normalized = normalize_drive_name(current_name)
            row = (current_id, current_name)
            if normalized == wanted:
                exact.append(row)
            elif wanted in normalized:
                partial.append(row)
            for parent_id in meta.get("parents") or []:
                if parent_id not in visited:
                    queue.append((parent_id, depth + 1))
        return exact or partial

    def _walk_folders(self, svc, folder_id: str, *, path: list[str] | None = None, _visited: set[str] | None = None):
        path = path or []
        # Shortcuts can legally point back at an ancestor (or themselves); track
        # visited folder ids so such cycles terminate instead of recursing forever.
        _visited = _visited if _visited is not None else set()
        if folder_id in _visited:
            return
        _visited.add(folder_id)
        page_token = None
        while True:
            resp = svc.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields=(
                    "nextPageToken, files("
                    "id,name,mimeType,shortcutDetails(targetId,targetMimeType)"
                    ")"
                ),
                orderBy="folder,name_natural",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for item in resp.get("files", []):
                item_path = [*path, item.get("name") or ""]
                mime_type = item.get("mimeType")
                shortcut = item.get("shortcutDetails") or {}
                if mime_type == SHORTCUT_MIME and shortcut.get("targetId"):
                    if shortcut.get("targetMimeType") == FOLDER_MIME:
                        folder_item = {**item, "id": shortcut["targetId"], "mimeType": FOLDER_MIME}
                        yield folder_item, item_path
                        yield from self._walk_folders(svc, shortcut["targetId"], path=item_path, _visited=_visited)
                elif mime_type == FOLDER_MIME:
                    yield item, item_path
                    yield from self._walk_folders(svc, item["id"], path=item_path, _visited=_visited)
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

    def _walk_folder(self, svc, folder_id: str, *, recursive: bool, path: list[str] | None = None, _visited: set[str] | None = None):
        path = path or []
        # Guard against shortcut cycles (a shortcut pointing at an ancestor/itself)
        # the same way _resolve_niche_from_ancestors does, so recursion terminates.
        _visited = _visited if _visited is not None else set()
        if folder_id in _visited:
            return
        _visited.add(folder_id)
        page_token = None
        while True:
            resp = svc.files().list(
                q=f"'{folder_id}' in parents and trashed=false",
                fields=(
                    "nextPageToken, files("
                    "id,name,mimeType,size,modifiedTime,webViewLink,"
                    "shortcutDetails(targetId,targetMimeType)"
                    ")"
                ),
                orderBy="folder,name_natural",
                pageSize=1000,
                pageToken=page_token,
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            ).execute()
            for item in resp.get("files", []):
                item_path = [*path, item.get("name") or ""]
                mime_type = item.get("mimeType")
                shortcut = item.get("shortcutDetails") or {}
                if mime_type == SHORTCUT_MIME and shortcut.get("targetId"):
                    target_mime = shortcut.get("targetMimeType")
                    if target_mime == FOLDER_MIME:
                        if recursive:
                            yield from self._walk_folder(svc, shortcut["targetId"], recursive=recursive, path=item_path, _visited=_visited)
                    else:
                        yield {
                            **item,
                            "id": shortcut["targetId"],
                            "mimeType": target_mime or mime_type,
                            "metadata_shortcut_id": item.get("id"),
                        }, item_path
                elif mime_type == FOLDER_MIME:
                    if recursive:
                        yield from self._walk_folder(svc, item["id"], recursive=recursive, path=item_path, _visited=_visited)
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
            # Escape LIKE metacharacters so a literal '%' or '_' in the niche
            # string can't alter the match (backslash is the escape char).
            escaped_niche = niche.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(
                or_(
                    ReadyVideo.niche.is_(None),
                    ReadyVideo.niche.ilike(f"%{escaped_niche}%", escape="\\"),
                )
            )
        stmt = (
            select(ReadyVideo)
            .where(and_(*conditions))
            .order_by(ReadyVideo.account_id.desc(), ReadyVideo.created_at.asc(), ReadyVideo.id.asc())
            .limit(10)
        )
        candidates = self.db.execute(stmt).scalars().all()
        if not candidates:
            return None
        # Defensive dedupe: a ready video already PUBLISHED for THIS account must
        # never be reserved again. Confirmed in production that the same Drive
        # file got published twice to the same channel (e.g. reserved a second
        # time after a status reset elsewhere) — this closes that hole at the
        # one chokepoint every reservation goes through, regardless of how the
        # row became reservable again.
        already_published_ids = {
            (job.video_context or {}).get("ready_video_id")
            for job in self.db.execute(
                select(VideoJob).where(
                    VideoJob.account_id == account.id,
                    VideoJob.mode == "from_ready_video",
                    VideoJob.status == JobStatus.PUBLISHED,
                )
            ).scalars().all()
        }
        row = next((c for c in candidates if c.id not in already_published_ids), None)
        if not row:
            return None
        # Atomic claim (mirrors backend/scheduler.py's `_create_theme_job` /
        # `_job_quota_reset` pattern): the plain SELECT above can race with
        # another scheduler tick/replica reading the same still-"available"
        # row before either commits, so both would reserve the same Drive
        # file and each create its own VideoJob -> duplicate uploads with an
        # identical title. Re-check `status == "available"` in the UPDATE's
        # WHERE clause and bail out if another transaction won the claim
        # first (rowcount == 0).
        result = self.db.execute(
            update(ReadyVideo)
            .where(ReadyVideo.id == row.id, ReadyVideo.status == "available")
            .values(status="reserved", reserved_at=datetime.utcnow())
        )
        self.db.flush()
        if result.rowcount == 0:
            return None
        self.db.refresh(row)
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
        # Hard wall-clock deadline: a dead/revoked OAuth refresh_token makes
        # google-auth-httplib2 log endless "Refreshing credentials due to a 401
        # response" retries and hand next_chunk() back (status, False) WITHOUT
        # ever raising — this loop then spins forever, burning real CPU/network,
        # and no outer asyncio.wait_for can help because the underlying OS thread
        # never yields (see the matching fix in uploaders/youtube.py upload_video
        # for the full story). Bound it explicitly so a dead token surfaces as a
        # normal exception instead of hanging the publish pipeline indefinitely.
        import time as _time

        try:
            with dest.open("wb") as fh:
                downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024 * 8)
                done = False
                deadline = _time.monotonic() + 240
                while not done:
                    if _time.monotonic() > deadline:
                        raise TimeoutError(
                            "Download do Drive travado (sem progresso) — provável token "
                            "OAuth inválido ou falha de rede persistente."
                        )
                    _, done = downloader.next_chunk()
        except BaseException:
            # A failed/interrupted download must not leave a truncated file behind:
            # the size>0 cache check above would treat it as complete on the next
            # retry (scheduler.py job creation / publisher.py _ensure_ready_video_local).
            dest.unlink(missing_ok=True)
            raise
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
