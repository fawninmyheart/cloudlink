import hashlib
import json
import secrets
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.task_store import (
    TaskConflict,
    WorkerNotRegistered,
    get_worker,
    get_worker_secret_hash,
    utc_now,
    verify_worker_secret,
)


class WorkerUninstallInviteError(Exception):
    pass


class WorkerUninstallInviteNotFound(WorkerUninstallInviteError):
    pass


class WorkerUninstallInviteExpired(WorkerUninstallInviteError):
    pass


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_worker_uninstall_invite(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    public_base_url: str,
    ttl_minutes: int,
) -> Dict[str, Any]:
    worker = get_worker(conn, worker_id)
    if worker.get("lifecycle_status") not in {"active", "uninstalling"}:
        raise TaskConflict("Worker is not eligible for scripted uninstall")
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    conn.execute(
        """
        INSERT INTO worker_uninstall_invites (
            token_hash, token_preview, worker_id, public_base_url,
            expires_at, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            token_hash(token),
            token[:8],
            worker_id,
            public_base_url.rstrip("/"),
            (now + timedelta(minutes=ttl_minutes)).isoformat(),
            now.isoformat(),
        ),
    )
    invite = get_worker_uninstall_invite(conn, token)
    invite["token"] = token
    return invite


def get_worker_uninstall_invite(
    conn: sqlite3.Connection,
    token: str,
    *,
    allow_completed: bool = False,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM worker_uninstall_invites WHERE token_hash = ?",
        (token_hash(token),),
    ).fetchone()
    if row is None:
        raise WorkerUninstallInviteNotFound("Worker uninstall invite not found")
    invite = dict(row)
    if invite.get("completed_at") and not allow_completed:
        raise WorkerUninstallInviteError("Worker uninstall has already completed")
    if datetime.now(timezone.utc) > datetime.fromisoformat(invite["expires_at"]):
        raise WorkerUninstallInviteExpired("Worker uninstall invite has expired")
    return invite


def _verify_bound_worker(
    conn: sqlite3.Connection,
    invite: Dict[str, Any],
    worker_secret: str,
) -> Dict[str, Any]:
    worker = get_worker(conn, invite["worker_id"])
    encoded = get_worker_secret_hash(conn, invite["worker_id"])
    if not encoded or not verify_worker_secret(worker_secret, encoded):
        raise WorkerUninstallInviteError("Invalid worker credentials")
    return worker


def begin_worker_uninstall(
    conn: sqlite3.Connection,
    token: str,
    worker_secret: str,
) -> Dict[str, Any]:
    invite = get_worker_uninstall_invite(conn, token)
    worker = _verify_bound_worker(conn, invite, worker_secret)
    running = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM tasks
        WHERE status = 'running' AND locked_by = ?
        """,
        (worker["worker_id"],),
    ).fetchone()["count"]
    if running:
        raise TaskConflict("Worker has active tasks and cannot be uninstalled")
    now = utc_now()
    conn.execute(
        """
        UPDATE worker_nodes
        SET enabled = 0,
            lifecycle_status = 'uninstalling',
            lifecycle_updated_at = ?,
            updated_at = ?
        WHERE worker_id = ?
        """,
        (now, now, worker["worker_id"]),
    )
    conn.execute(
        """
        UPDATE worker_uninstall_invites
        SET begun_at = COALESCE(begun_at, ?)
        WHERE token_hash = ?
        """,
        (now, token_hash(token)),
    )
    return {
        "status": "uninstalling",
        "worker_id": worker["worker_id"],
        "platform": worker.get("install_platform"),
    }


def complete_worker_uninstall(
    conn: sqlite3.Connection,
    token: str,
    worker_secret: str,
) -> Dict[str, Any]:
    invite = get_worker_uninstall_invite(conn, token)
    worker = _verify_bound_worker(conn, invite, worker_secret)
    if worker.get("lifecycle_status") != "uninstalling":
        raise TaskConflict("Worker uninstall has not started")
    now = utc_now()
    audit_id = str(uuid.uuid4())
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute(
            """
            INSERT INTO worker_lifecycle_audit (
                id, worker_id, display_name, platform, action,
                reason, details, created_at
            )
            VALUES (?, ?, ?, ?, 'uninstalled', '', ?, ?)
            """,
            (
                audit_id,
                worker["worker_id"],
                worker["display_name"],
                worker.get("install_platform"),
                json.dumps(
                    {
                        "registered_at": worker.get("registered_at"),
                        "last_seen_at": worker.get("last_seen_at"),
                    },
                    ensure_ascii=False,
                ),
                now,
            ),
        )
        conn.execute(
            """
            UPDATE worker_dataset_caches
            SET status = 'orphaned',
                last_error = 'Worker was uninstalled; local files were preserved',
                updated_at = ?
            WHERE worker_id = ?
              AND status != 'deleted'
            """,
            (now, worker["worker_id"]),
        )
        conn.execute(
            "DELETE FROM worker_install_invites WHERE worker_id = ?",
            (worker["worker_id"],),
        )
        conn.execute(
            "DELETE FROM worker_nodes WHERE worker_id = ?",
            (worker["worker_id"],),
        )
        conn.execute(
            """
            UPDATE worker_uninstall_invites
            SET completed_at = ?
            WHERE token_hash = ?
            """,
            (now, token_hash(token)),
        )
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return {"status": "removed", "worker_id": worker["worker_id"]}


def archive_lost_worker(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    reason: str,
    offline_seconds: int,
) -> Dict[str, Any]:
    worker = get_worker(conn, worker_id)
    if worker.get("online"):
        raise TaskConflict("Online worker cannot be archived as lost")
    if worker.get("lifecycle_status") != "active":
        raise TaskConflict("Worker is not active")
    last_seen = worker.get("last_seen_at") or worker.get("registered_at")
    if not last_seen:
        raise TaskConflict("Worker has no lifecycle timestamp")
    age = (
        datetime.now(timezone.utc) - datetime.fromisoformat(last_seen)
    ).total_seconds()
    if age < offline_seconds:
        raise TaskConflict("Worker has not been offline long enough")
    now = utc_now()
    conn.execute(
        """
        UPDATE worker_nodes
        SET enabled = 0,
            lifecycle_status = 'lost',
            lifecycle_updated_at = ?,
            worker_secret_hash = '',
            updated_at = ?
        WHERE worker_id = ?
        """,
        (now, now, worker_id),
    )
    conn.execute(
        """
        UPDATE worker_dataset_caches
        SET status = 'orphaned',
            last_error = 'Worker was archived as lost',
            updated_at = ?
        WHERE worker_id = ?
          AND status != 'deleted'
        """,
        (now, worker_id),
    )
    conn.execute(
        """
        INSERT INTO worker_lifecycle_audit (
            id, worker_id, display_name, platform, action,
            reason, details, created_at
        )
        VALUES (?, ?, ?, ?, 'archived_lost', ?, ?, ?)
        """,
        (
            str(uuid.uuid4()),
            worker_id,
            worker["display_name"],
            worker.get("install_platform"),
            reason.strip() or "Worker unavailable",
            json.dumps({"last_seen_at": worker.get("last_seen_at")}),
            now,
        ),
    )
    return get_worker(conn, worker_id)
