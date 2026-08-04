from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.config import get_settings
from app.dataset_store import DatasetConflict, get_dataset_version


class StorageObjectGone(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _within(path: Path, root: Path) -> bool:
    resolved = path.resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    return resolved == resolved_root or resolved_root in resolved.parents


def _new_run(
    conn: sqlite3.Connection,
    cleanup_type: str,
    dry_run: bool,
    requested_by: str,
    reason: str,
    filter_data: Dict[str, Any],
) -> str:
    run_id = str(uuid.uuid4())
    conn.execute(
        """
        INSERT INTO storage_cleanup_runs (
            id, cleanup_type, dry_run, requested_by, reason, filter_json,
            status, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, 'running', ?)
        """,
        (
            run_id,
            cleanup_type,
            1 if dry_run else 0,
            requested_by,
            reason,
            json.dumps(filter_data, ensure_ascii=False),
            utc_now(),
        ),
    )
    return run_id


def _finish_run(
    conn: sqlite3.Connection,
    run_id: str,
    *,
    candidates: int,
    processed: int,
    skipped: int,
    estimated_bytes: int,
    released_bytes: int,
) -> Dict[str, Any]:
    conn.execute(
        """
        UPDATE storage_cleanup_runs
        SET candidate_count = ?, processed_count = ?, skipped_count = ?,
            estimated_bytes = ?, released_bytes = ?, status = 'completed',
            completed_at = ?
        WHERE id = ?
        """,
        (
            candidates,
            processed,
            skipped,
            estimated_bytes,
            released_bytes,
            utc_now(),
            run_id,
        ),
    )
    return dict(
        conn.execute(
            "SELECT * FROM storage_cleanup_runs WHERE id = ?",
            (run_id,),
        ).fetchone()
    )


def release_dataset_server_copy(
    conn: sqlite3.Connection,
    dataset_version_id: str,
    *,
    dry_run: bool = True,
    requested_by: str = "admin",
    reason: str = "",
) -> Dict[str, Any]:
    version = get_dataset_version(conn, dataset_version_id)
    run_id = _new_run(
        conn,
        "dataset_server_copy_release",
        dry_run,
        requested_by,
        reason,
        {"dataset_version_id": dataset_version_id},
    )
    size = int(version.get("size_bytes") or 0)
    status = version.get("server_copy_status") or "available"
    if status == "released":
        return _finish_run(
            conn,
            run_id,
            candidates=1,
            processed=0,
            skipped=1,
            estimated_bytes=0,
            released_bytes=0,
        )
    if version["source_kind"] not in {"owned_file", "owned_archive"}:
        raise DatasetConflict("Only owned dataset server copies can be released")
    path = Path(version["server_path"])
    objects_root = Path(get_settings().database_path).expanduser().resolve().parent / "objects"
    configured_root = Path(
        __import__("os").environ.get("CLOUDLINK_DATA_ROOT", str(objects_root.parent))
    ).expanduser().resolve() / "objects"
    if not _within(path, configured_root) or path.is_symlink():
        raise DatasetConflict("Dataset server copy path is not safe to release")
    conn.execute(
        """
        INSERT OR REPLACE INTO storage_cleanup_items (
            run_id, object_type, object_id, action, status,
            size_bytes, reason, created_at
        ) VALUES (?, 'dataset_version', ?, 'release_server_copy', ?, ?, ?, ?)
        """,
        (
            run_id,
            dataset_version_id,
            "preview" if dry_run else "released",
            size,
            reason,
            utc_now(),
        ),
    )
    if dry_run:
        return _finish_run(
            conn,
            run_id,
            candidates=1,
            processed=0,
            skipped=0,
            estimated_bytes=size,
            released_bytes=0,
        )
    conn.execute(
        """
        UPDATE dataset_versions
        SET server_copy_status = 'releasing'
        WHERE id = ? AND server_copy_status != 'released'
        """,
        (dataset_version_id,),
    )
    if path.exists():
        if not path.is_file():
            raise DatasetConflict("Dataset server copy is not a regular file")
        path.unlink()
    conn.execute(
        """
        UPDATE dataset_versions
        SET server_copy_status = 'released',
            server_copy_released_at = ?,
            server_copy_release_reason = ?
        WHERE id = ?
        """,
        (utc_now(), reason, dataset_version_id),
    )
    return _finish_run(
        conn,
        run_id,
        candidates=1,
        processed=1,
        skipped=0,
        estimated_bytes=size,
        released_bytes=size,
    )


def _artifact_candidates(
    conn: sqlite3.Connection,
    *,
    task_id: Optional[str] = None,
) -> Iterable[sqlite3.Row]:
    settings = get_settings()
    cutoff = (
        datetime.now(timezone.utc)
        - timedelta(seconds=settings.artifact_retention_seconds)
    ).isoformat()
    where = ["tasks.status IN ('success','failed','timeout','cancelled')"]
    now = utc_now()
    params: list[Any] = []
    if task_id:
        where.append("task_artifacts.task_id = ?")
        params.append(task_id)
    return conn.execute(
        f"""
        SELECT task_artifacts.*
        FROM task_artifacts
        JOIN tasks ON tasks.id = task_artifacts.task_id
        WHERE {' AND '.join(where)}
          AND task_artifacts.status NOT IN ('published', 'purged')
          AND (
              (task_artifacts.expires_at IS NOT NULL AND task_artifacts.expires_at <= ?)
              OR
              (task_artifacts.expires_at IS NULL AND tasks.finished_at <= ?)
          )
          AND NOT EXISTS (
              SELECT 1 FROM artifact_download_leases
              WHERE artifact_download_leases.artifact_id = task_artifacts.id
                AND artifact_download_leases.expires_at > ?
          )
        ORDER BY task_artifacts.created_at ASC
        """,
        [*params, now, cutoff, now],
    ).fetchall()


def purge_expired_artifacts(
    conn: sqlite3.Connection,
    *,
    dry_run: bool = True,
    requested_by: str = "automatic",
    reason: str = "24-hour retention expired",
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    rows = list(_artifact_candidates(conn, task_id=task_id))
    run_id = _new_run(
        conn,
        "artifact_retention_purge",
        dry_run,
        requested_by,
        reason,
        {"task_id": task_id},
    )
    estimated = sum(int(row["size_bytes"] or 0) for row in rows)
    released = 0
    processed = 0
    skipped = 0
    root = (
        Path(__import__("os").environ.get("CLOUDLINK_DATA_ROOT", "./data"))
        .expanduser()
        .resolve()
        / "artifacts"
        / "tasks"
    )
    for row in rows:
        conn.execute(
            """
            INSERT OR REPLACE INTO storage_cleanup_items (
                run_id, object_type, object_id, action, status,
                size_bytes, reason, created_at
            ) VALUES (?, 'task_artifact', ?, 'purge', ?, ?, ?, ?)
            """,
            (
                run_id,
                row["id"],
                "preview" if dry_run else "purging",
                int(row["size_bytes"] or 0),
                reason,
                utc_now(),
            ),
        )
        if dry_run:
            continue
        target = Path(row["storage_path"])
        part = target.parent / f"{target.name}.part"
        if not _within(target, root) or target.is_symlink() or part.is_symlink():
            conn.execute(
                """
                UPDATE storage_cleanup_items
                SET status = 'skipped', reason = 'Unsafe artifact storage path'
                WHERE run_id = ? AND object_id = ?
                """,
                (run_id, row["id"]),
            )
            skipped += 1
            continue
        conn.execute(
            "UPDATE task_artifacts SET status = 'purging', updated_at = ? WHERE id = ?",
            (utc_now(), row["id"]),
        )
        for path in (target, part):
            if path.exists() and path.is_file():
                released += path.stat().st_size
                path.unlink()
        try:
            target.parent.rmdir()
        except OSError:
            pass
        conn.execute(
            """
            UPDATE task_artifacts
            SET status = 'purged', purged_at = ?, purge_reason = ?,
                purged_size_bytes = ?, updated_at = ?
            WHERE id = ?
            """,
            (utc_now(), reason, int(row["size_bytes"] or 0), utc_now(), row["id"]),
        )
        conn.execute(
            """
            UPDATE storage_cleanup_items
            SET status = 'purged'
            WHERE run_id = ? AND object_id = ?
            """,
            (run_id, row["id"]),
        )
        processed += 1
    return _finish_run(
        conn,
        run_id,
        candidates=len(rows),
        processed=processed,
        skipped=skipped,
        estimated_bytes=estimated,
        released_bytes=released,
    )


def create_download_lease(
    conn: sqlite3.Connection,
    artifact_id: str,
    *,
    seconds: int = 3600,
) -> str:
    lease_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc)
    conn.execute(
        "DELETE FROM artifact_download_leases WHERE expires_at <= ?",
        (now.isoformat(),),
    )
    conn.execute(
        """
        INSERT INTO artifact_download_leases (id, artifact_id, expires_at, created_at)
        VALUES (?, ?, ?, ?)
        """,
        (
            lease_id,
            artifact_id,
            (now + timedelta(seconds=seconds)).isoformat(),
            now.isoformat(),
        ),
    )
    return lease_id
