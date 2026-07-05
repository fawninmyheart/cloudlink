import hashlib
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional


VALID_INSTALL_PLATFORMS = {"macos", "linux", "windows"}


class WorkerInstallInviteError(Exception):
    pass


class WorkerInstallInviteNotFound(WorkerInstallInviteError):
    pass


class WorkerInstallInviteExpired(WorkerInstallInviteError):
    pass


class WorkerInstallInviteUsed(WorkerInstallInviteError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def row_to_invite(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def create_worker_install_invite(
    conn: sqlite3.Connection,
    *,
    platform: str,
    worker_id: Optional[str],
    display_name: Optional[str],
    public_base_url: str,
    ttl_minutes: int,
) -> Dict[str, Any]:
    normalized_platform = platform.strip().lower()
    if normalized_platform not in VALID_INSTALL_PLATFORMS:
        raise WorkerInstallInviteError("platform must be macos, linux, or windows")
    resolved_worker_id = (worker_id or "").strip()
    if not resolved_worker_id:
        raise WorkerInstallInviteError("worker_id is required")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    resolved_display_name = (display_name or "").strip() or resolved_worker_id
    conn.execute(
        """
        INSERT INTO worker_install_invites (
            token_hash, token_preview, worker_id, display_name, platform,
            public_base_url, expires_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            token_hash(token),
            token[:8],
            resolved_worker_id,
            resolved_display_name,
            normalized_platform,
            public_base_url.rstrip("/"),
            (now + timedelta(minutes=ttl_minutes)).isoformat(),
            now.isoformat(),
        ),
    )
    invite = get_worker_install_invite(conn, token)
    invite["token"] = token
    return invite


def get_worker_install_invite(
    conn: sqlite3.Connection,
    token: str,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM worker_install_invites WHERE token_hash = ?",
        (token_hash(token),),
    ).fetchone()
    if row is None:
        raise WorkerInstallInviteNotFound("Worker install invite not found")
    invite = row_to_invite(row)
    if invite.get("used_at"):
        raise WorkerInstallInviteUsed("Worker install invite has already been used")
    expires_at = datetime.fromisoformat(invite["expires_at"])
    if datetime.now(timezone.utc) > expires_at:
        raise WorkerInstallInviteExpired("Worker install invite has expired")
    return invite


def mark_worker_install_invite_used(
    conn: sqlite3.Connection,
    token: str,
) -> None:
    conn.execute(
        """
        UPDATE worker_install_invites
        SET used_at = ?
        WHERE token_hash = ?
        """,
        (utc_now(), token_hash(token)),
    )
