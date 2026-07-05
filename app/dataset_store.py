import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


SOURCE_KINDS = {"symlink_file", "owned_archive"}
ACTIVE_CACHE_STATUSES = {"downloading", "cached", "extracted", "delete_requested"}


class DatasetConflict(Exception):
    pass


class DatasetNotFound(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def data_root() -> Path:
    return Path(os.getenv("CLOUDLINK_DATA_ROOT", "./data")).expanduser().resolve()


def object_dir(name: str, version: str) -> Path:
    return data_root() / "objects" / safe_slug(name, "name") / safe_slug(version, "version")


def safe_slug(value: str, field_name: str) -> str:
    if not value or any(ch in value for ch in "/\\"):
        raise ValueError(f"{field_name} must be a non-empty slug")
    if value in {".", ".."}:
        raise ValueError(f"{field_name} must not be a relative path marker")
    return value


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def is_path_within_roots(path: str, roots: Iterable[str]) -> bool:
    source = Path(path).expanduser().resolve()
    for root_text in roots:
        root = Path(root_text).expanduser().resolve()
        if source == root or root in source.parents:
            return True
    return False


def row_to_dataset_version(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["extract_required"] = bool(item["extract_required"])
    item["manifest"] = json.loads(item["manifest"])
    return item


def row_to_worker_cache(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    return item


def ensure_dataset(
    conn: sqlite3.Connection,
    name: str,
    title: str,
    description: str,
) -> Dict[str, Any]:
    now = utc_now()
    row = conn.execute("SELECT * FROM datasets WHERE name = ?", (name,)).fetchone()
    if row is None:
        dataset_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO datasets (id, name, title, description, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (dataset_id, name, title or name, description or "", now, now),
        )
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (dataset_id,)).fetchone()
    else:
        conn.execute(
            """
            UPDATE datasets
            SET title = ?, description = ?, updated_at = ?, archived_at = NULL
            WHERE id = ?
            """,
            (title or row["title"], description if description is not None else row["description"], now, row["id"]),
        )
        row = conn.execute("SELECT * FROM datasets WHERE id = ?", (row["id"],)).fetchone()
    return dict(row)


def register_dataset_version(
    conn: sqlite3.Connection,
    *,
    name: str,
    version: str,
    title: str,
    description: str,
    source_kind: str,
    source_path: str,
    content_type: Optional[str] = None,
    archive_format: Optional[str] = None,
    extract_required: bool = False,
    manifest_extra: Optional[Dict[str, Any]] = None,
    created_by: str = "internal",
    compute_sha256: bool = False,
) -> Dict[str, Any]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError("source_kind must be symlink_file or owned_archive")
    source = Path(source_path).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(str(source))

    dataset = ensure_dataset(conn, name, title, description)
    target_dir = object_dir(name, version)
    if target_dir.exists() and any(target_dir.iterdir()):
        raise DatasetConflict("Dataset version storage already exists")
    target_dir.mkdir(parents=True, exist_ok=True)

    checksum = None
    original_path: Optional[str] = str(source)
    if source_kind == "symlink_file":
        server_path = target_dir / "source"
        server_path.symlink_to(source)
        if compute_sha256:
            checksum = sha256_file(source)
    else:
        server_path = target_dir / source.name
        shutil.move(str(source), str(server_path))
        original_path = str(source)
        checksum = sha256_file(server_path)

    now = utc_now()
    version_id = str(uuid.uuid4())
    size_bytes = server_path.stat().st_size
    manifest = {
        "dataset_id": name,
        "dataset_version_id": version_id,
        "version": version,
        "title": title or name,
        "description": description or "",
        "source_kind": source_kind,
        "server_path": str(server_path),
        "original_path": original_path,
        "size_bytes": size_bytes,
        "checksum_sha256": checksum,
        "content_type": content_type,
        "archive_format": archive_format,
        "extract_required": bool(extract_required),
        "created_by": created_by,
        "created_at": now,
    }
    if manifest_extra:
        manifest.update(manifest_extra)

    manifest_path = target_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    conn.execute(
        """
        INSERT INTO dataset_versions (
            id, dataset_id, version, source_kind, server_path, original_path,
            size_bytes, checksum_sha256, content_type, archive_format,
            extract_required, manifest, created_by, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            dataset["id"],
            version,
            source_kind,
            str(server_path),
            original_path,
            size_bytes,
            checksum,
            content_type,
            archive_format,
            1 if extract_required else 0,
            json.dumps(manifest, ensure_ascii=False),
            created_by,
            now,
        ),
    )
    return get_dataset_version(conn, version_id)


def get_dataset_version(conn: sqlite3.Connection, dataset_version_id: str) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT
            dataset_versions.*,
            datasets.name AS dataset_name,
            datasets.title AS dataset_title,
            datasets.description AS dataset_description
        FROM dataset_versions
        JOIN datasets ON datasets.id = dataset_versions.dataset_id
        WHERE dataset_versions.id = ?
          AND dataset_versions.deleted_at IS NULL
        """,
        (dataset_version_id,),
    ).fetchone()
    if row is None:
        raise DatasetNotFound(dataset_version_id)
    return row_to_dataset_version(row)


def list_dataset_versions(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            dataset_versions.*,
            datasets.name AS dataset_name,
            datasets.title AS dataset_title,
            datasets.description AS dataset_description
        FROM dataset_versions
        JOIN datasets ON datasets.id = dataset_versions.dataset_id
        WHERE dataset_versions.deleted_at IS NULL
        ORDER BY datasets.name ASC, dataset_versions.version ASC
        """
    ).fetchall()
    return [row_to_dataset_version(row) for row in rows]


def delete_dataset_version(conn: sqlite3.Connection, dataset_version_id: str) -> None:
    version = get_dataset_version(conn, dataset_version_id)
    active = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM worker_dataset_caches
        WHERE dataset_version_id = ?
          AND status IN (?, ?, ?, ?)
        """,
        (dataset_version_id, *sorted(ACTIVE_CACHE_STATUSES)),
    ).fetchone()["count"]
    if active:
        raise DatasetConflict("Dataset version has active worker cache")

    server_path = Path(version["server_path"])
    target_dir = server_path.parent
    if version["source_kind"] == "symlink_file":
        if server_path.is_symlink() or server_path.exists():
            server_path.unlink()
    elif version["source_kind"] == "owned_archive":
        if server_path.exists():
            server_path.unlink()
    manifest_path = target_dir / "manifest.json"
    if manifest_path.exists():
        manifest_path.unlink()
    try:
        target_dir.rmdir()
        target_dir.parent.rmdir()
    except OSError:
        pass

    conn.execute(
        "DELETE FROM worker_dataset_caches WHERE dataset_version_id = ?",
        (dataset_version_id,),
    )
    conn.execute("DELETE FROM dataset_versions WHERE id = ?", (dataset_version_id,))


def upsert_worker_cache(
    conn: sqlite3.Connection,
    *,
    worker_id: str,
    dataset_version_id: str,
    status: str,
    local_archive_path: Optional[str] = None,
    local_extracted_path: Optional[str] = None,
    size_bytes: int = 0,
    extracted_size_bytes: int = 0,
    checksum_sha256: Optional[str] = None,
    data_root_path: Optional[str] = None,
    last_error: Optional[str] = None,
    last_used_at: Optional[str] = None,
) -> Dict[str, Any]:
    get_dataset_version(conn, dataset_version_id)
    now = utc_now()
    conn.execute(
        """
        INSERT INTO worker_dataset_caches (
            worker_id, dataset_version_id, status, local_archive_path,
            local_extracted_path, size_bytes, extracted_size_bytes,
            checksum_sha256, data_root_path, last_used_at, last_error, updated_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(worker_id, dataset_version_id) DO UPDATE SET
            status = excluded.status,
            local_archive_path = CASE
                WHEN excluded.status = 'deleted' THEN excluded.local_archive_path
                ELSE COALESCE(excluded.local_archive_path, worker_dataset_caches.local_archive_path)
            END,
            local_extracted_path = CASE
                WHEN excluded.status = 'deleted' THEN excluded.local_extracted_path
                ELSE COALESCE(excluded.local_extracted_path, worker_dataset_caches.local_extracted_path)
            END,
            size_bytes = excluded.size_bytes,
            extracted_size_bytes = excluded.extracted_size_bytes,
            checksum_sha256 = COALESCE(excluded.checksum_sha256, worker_dataset_caches.checksum_sha256),
            data_root_path = COALESCE(excluded.data_root_path, worker_dataset_caches.data_root_path),
            last_used_at = COALESCE(excluded.last_used_at, worker_dataset_caches.last_used_at),
            last_error = excluded.last_error,
            updated_at = excluded.updated_at
        """,
        (
            worker_id,
            dataset_version_id,
            status,
            local_archive_path,
            local_extracted_path,
            size_bytes,
            extracted_size_bytes,
            checksum_sha256,
            data_root_path,
            last_used_at,
            last_error,
            now,
        ),
    )
    row = conn.execute(
        """
        SELECT * FROM worker_dataset_caches
        WHERE worker_id = ? AND dataset_version_id = ?
        """,
        (worker_id, dataset_version_id),
    ).fetchone()
    return row_to_worker_cache(row)


def list_worker_caches(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            worker_dataset_caches.*,
            datasets.name AS dataset_name,
            dataset_versions.version AS dataset_version
        FROM worker_dataset_caches
        JOIN dataset_versions ON dataset_versions.id = worker_dataset_caches.dataset_version_id
        JOIN datasets ON datasets.id = dataset_versions.dataset_id
        ORDER BY datasets.name ASC, dataset_versions.version ASC, worker_dataset_caches.worker_id ASC
        """
    ).fetchall()
    return [row_to_worker_cache(row) for row in rows]


def list_worker_caches_for_worker(
    conn: sqlite3.Connection,
    worker_id: str,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            worker_dataset_caches.*,
            datasets.name AS dataset_name,
            dataset_versions.version AS dataset_version,
            dataset_versions.size_bytes AS expected_size_bytes,
            dataset_versions.checksum_sha256 AS expected_checksum_sha256,
            dataset_versions.extract_required AS extract_required,
            dataset_versions.archive_format AS archive_format
        FROM worker_dataset_caches
        JOIN dataset_versions ON dataset_versions.id = worker_dataset_caches.dataset_version_id
        JOIN datasets ON datasets.id = dataset_versions.dataset_id
        WHERE worker_dataset_caches.worker_id = ?
          AND dataset_versions.deleted_at IS NULL
        ORDER BY worker_dataset_caches.updated_at DESC
        """,
        (worker_id,),
    ).fetchall()
    return [row_to_worker_cache(row) for row in rows]


def request_worker_cache_delete(
    conn: sqlite3.Connection,
    dataset_version_id: str,
    worker_id: Optional[str] = None,
) -> int:
    get_dataset_version(conn, dataset_version_id)
    if worker_id:
        cursor = conn.execute(
            """
            UPDATE worker_dataset_caches
            SET status = 'delete_requested', updated_at = ?
            WHERE dataset_version_id = ? AND worker_id = ?
            """,
            (utc_now(), dataset_version_id, worker_id),
        )
    else:
        cursor = conn.execute(
            """
            UPDATE worker_dataset_caches
            SET status = 'delete_requested', updated_at = ?
            WHERE dataset_version_id = ?
            """,
            (utc_now(), dataset_version_id),
        )
    return cursor.rowcount


def pending_delete_requests(
    conn: sqlite3.Connection,
    worker_id: str,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            worker_dataset_caches.*,
            datasets.name AS dataset_name,
            dataset_versions.version AS dataset_version
        FROM worker_dataset_caches
        JOIN dataset_versions ON dataset_versions.id = worker_dataset_caches.dataset_version_id
        JOIN datasets ON datasets.id = dataset_versions.dataset_id
        WHERE worker_dataset_caches.worker_id = ?
          AND worker_dataset_caches.status = 'delete_requested'
        ORDER BY worker_dataset_caches.updated_at ASC
        """,
        (worker_id,),
    ).fetchall()
    return [row_to_worker_cache(row) for row in rows]
