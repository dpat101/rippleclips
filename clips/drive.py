"""Google Drive: find episode folders, download videos, upload finished clips.

Signs in as the Google account that owns The Ripple Effect folder (see SETUP.md).
"""
from __future__ import annotations

import io
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from . import config

FOLDER_MIME = "application/vnd.google-apps.folder"


@dataclass
class DriveFile:
    id: str
    name: str
    mime_type: str
    modified: datetime
    size: int = 0


def episode_number(folder_name: str) -> int | None:
    """'TRE Ep52' -> 52. Anything else -> None."""
    m = re.match(config.EPISODE_FOLDER_PATTERN, folder_name.strip(), re.IGNORECASE)
    return int(m.group(1)) if m else None


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def is_settled(files: list[DriveFile], now: datetime, settle_hours: float) -> bool:
    """True once nothing in the folder has changed for settle_hours (uploads have finished)."""
    if not files:
        return False
    newest = max(f.modified for f in files)
    return (now - newest).total_seconds() >= settle_hours * 3600


def safe_name(text: str, limit: int = 60) -> str:
    text = re.sub(r"[\\/:*?\"<>|\n\r\t]+", " ", text).strip()
    text = re.sub(r"\s+", " ", text)
    return text[:limit].rstrip(" .") or "clip"


class Drive:
    def __init__(self, service=None):
        self.svc = service or self._connect()

    @staticmethod
    def _connect():
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        if not config.GOOGLE_TOKEN_FILE.exists():
            raise SystemExit("Not signed in to Google yet: run `python -m clips.google_signin` (SETUP.md).")
        creds = Credentials.from_authorized_user_file(str(config.GOOGLE_TOKEN_FILE), config.GOOGLE_SCOPES)
        if not creds.valid and creds.refresh_token:
            creds.refresh(Request())
            config.GOOGLE_TOKEN_FILE.write_text(creds.to_json())
            config.GOOGLE_TOKEN_FILE.chmod(0o600)
        return build("drive", "v3", credentials=creds, cache_discovery=False)

    def _list(self, q: str) -> list[DriveFile]:
        out, token = [], None
        while True:
            resp = self.svc.files().list(
                q=q, pageToken=token, pageSize=200,
                fields="nextPageToken, files(id, name, mimeType, modifiedTime, size)",
                supportsAllDrives=True, includeItemsFromAllDrives=True,
            ).execute()
            for f in resp.get("files", []):
                out.append(DriveFile(f["id"], f["name"], f["mimeType"], parse_time(f["modifiedTime"]), int(f.get("size", 0) or 0)))
            token = resp.get("nextPageToken")
            if not token:
                return out

    def root_id(self) -> str:
        if config.DRIVE_ROOT_ID:
            return config.DRIVE_ROOT_ID
        name = config.DRIVE_ROOT_NAME.replace("'", "\\'")
        found = self._list(f"name = '{name}' and mimeType = '{FOLDER_MIME}' and trashed = false")
        if len(found) != 1:
            raise SystemExit(f"Expected one Drive folder called {config.DRIVE_ROOT_NAME!r}, found {len(found)}. Set DRIVE_ROOT_ID in ~/.rippleclips-env.")
        return found[0].id

    def subfolders(self, parent_id: str) -> list[DriveFile]:
        return self._list(f"'{parent_id}' in parents and mimeType = '{FOLDER_MIME}' and trashed = false")

    def children(self, parent_id: str) -> list[DriveFile]:
        return self._list(f"'{parent_id}' in parents and trashed = false")

    def episodes(self) -> list[tuple[int, DriveFile]]:
        eps = []
        for folder in self.subfolders(self.root_id()):
            n = episode_number(folder.name)
            if n is not None and n >= config.START_EPISODE:
                eps.append((n, folder))
        return sorted(eps, key=lambda e: e[0])

    def download(self, file_id: str, dest: Path) -> Path:
        from googleapiclient.http import MediaIoBaseDownload

        dest.parent.mkdir(parents=True, exist_ok=True)
        req = self.svc.files().get_media(fileId=file_id, supportsAllDrives=True)
        with io.FileIO(dest, "wb") as fh:
            dl = MediaIoBaseDownload(fh, req, chunksize=32 * 1024 * 1024)
            done = False
            while not done:
                _, done = dl.next_chunk()
        return dest

    def folder(self, parent_id: str, name: str) -> str:
        """Find or create a subfolder."""
        safe = name.replace("'", "\\'")
        found = self._list(f"'{parent_id}' in parents and name = '{safe}' and mimeType = '{FOLDER_MIME}' and trashed = false")
        if found:
            return found[0].id
        made = self.svc.files().create(
            body={"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]},
            fields="id", supportsAllDrives=True,
        ).execute()
        return made["id"]

    def upload(self, path: Path, parent_id: str, mime_type: str, name: str | None = None) -> dict:
        from googleapiclient.http import MediaFileUpload

        media = MediaFileUpload(str(path), mimetype=mime_type, resumable=True, chunksize=16 * 1024 * 1024)
        return self.svc.files().create(
            body={"name": name or path.name, "parents": [parent_id]},
            media_body=media, fields="id, webViewLink", supportsAllDrives=True,
        ).execute()

    def move(self, file_id: str, folder_id: str) -> None:
        """Move a file into another folder (out of every folder it is in now)."""
        got = self.svc.files().get(fileId=file_id, fields="parents, name", supportsAllDrives=True).execute()
        old = [p for p in got.get("parents", []) if p != folder_id]
        if folder_id in got.get("parents", []) and not old:
            return
        self.svc.files().update(fileId=file_id, addParents=folder_id, removeParents=",".join(old),
                                fields="id", supportsAllDrives=True).execute()

    def parent(self, file_id: str) -> str:
        """The folder a file is in."""
        got = self.svc.files().get(fileId=file_id, fields="parents", supportsAllDrives=True).execute()
        return (got.get("parents") or [""])[0]

    def public_link(self, file_id: str) -> str:
        """Let anyone with the link view the file, and return a direct download link to it.

        Used to hand a clip to ContentStudio by link, so it does not fill ContentStudio's media storage.
        """
        self.svc.permissions().create(fileId=file_id, body={"type": "anyone", "role": "reader"},
                                      fields="id", supportsAllDrives=True).execute()
        return f"https://drive.usercontent.google.com/download?id={file_id}&export=download&confirm=t"


def now_utc() -> datetime:
    return datetime.now(timezone.utc)
