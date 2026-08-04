import hashlib
import json
import os
import shutil
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import quote


class TransferConflict(Exception):
    pass


class TransferNotFound(Exception):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def path_within_roots(path: Path, roots: Iterable[str]) -> bool:
    candidate = path.expanduser().resolve(strict=False)
    for raw_root in roots:
        root = Path(raw_root).expanduser().resolve(strict=False)
        if candidate == root or root in candidate.parents:
            return True
    return False


def safe_job_path(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    path = Path(text)
    if not text or path.is_absolute() or ".." in path.parts or text in {".", ""}:
        raise TransferConflict(f"{field_name} must stay inside the job directory")
    return str(path)


def normalize_input_paths(
    value: Any,
    allowed_roots: Iterable[str],
) -> List[Dict[str, Any]]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise TransferConflict("input_paths must be a list")

    normalized: List[Dict[str, Any]] = []
    seen_targets = set()
    for raw in value:
        if not isinstance(raw, dict):
            raise TransferConflict("input_paths entries must be objects")
        source = Path(str(raw.get("source_path") or "")).expanduser().resolve()
        if not path_within_roots(source, allowed_roots):
            raise TransferConflict("input source path is outside allowed transfer roots")
        if not source.is_file():
            raise TransferConflict(f"input source must be a regular file: {source}")
        target = safe_job_path(raw.get("path"), "input_paths.path")
        if target in seen_targets:
            raise TransferConflict(f"duplicate input target path: {target}")
        seen_targets.add(target)
        stat = source.stat()
        checksum = sha256_file(source)
        stat_after_hash = source.stat()
        if (
            stat_after_hash.st_size != stat.st_size
            or stat_after_hash.st_mtime_ns != stat.st_mtime_ns
        ):
            raise TransferConflict(f"input source changed while hashing: {source}")
        identity = json.dumps(
            {
                "size_bytes": stat.st_size,
                "checksum_sha256": checksum,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        cache_key = hashlib.sha256(identity).hexdigest()
        normalized.append(
            {
                "id": str(uuid.uuid4()),
                "source_path": str(source),
                "path": target,
                "filename": source.name,
                "size_bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
                "checksum_sha256": checksum,
                "cache_key": cache_key,
                "extract_required": bool(raw.get("extract_required", False)),
                "archive_format": str(raw.get("archive_format") or "").strip() or None,
            }
        )
    return normalized


def normalize_result_path(value: Optional[str], allowed_roots: Iterable[str]) -> Optional[str]:
    text = str(value or "").strip()
    if not text:
        return None
    path = Path(text).expanduser().resolve(strict=False)
    if not path_within_roots(path, allowed_roots):
        raise TransferConflict("result path is outside allowed destination roots")
    if any(path == Path(root).expanduser().resolve(strict=False) for root in allowed_roots):
        raise TransferConflict("result path must be a child of an allowed destination root")
    if path.exists():
        raise TransferConflict("result path must not already exist")
    return str(path)


def worker_input_descriptors(task: Dict[str, Any]) -> List[Dict[str, Any]]:
    descriptors = []
    for item in task.get("payload", {}).get("input_paths") or []:
        public = {key: value for key, value in item.items() if key != "source_path"}
        public["download_url"] = (
            f"/api/worker/tasks/{task['id']}/inputs/{item['id']}/download"
            f"?worker_id={quote(str(task['locked_by']), safe='')}"
            f"&lease_id={quote(str(task['lease_id']), safe='')}"
        )
        descriptors.append(public)
    return descriptors


def task_input_source(task: Dict[str, Any], input_id: str) -> Path:
    for item in task.get("payload", {}).get("input_paths") or []:
        if item.get("id") == input_id:
            source = Path(item["source_path"])
            stat = source.stat()
            if stat.st_size != int(item["size_bytes"]) or stat.st_mtime_ns != int(
                item["mtime_ns"]
            ):
                raise TransferConflict("input source changed after task submission")
            return source
    raise TransferNotFound(input_id)


def request_task_cache_release(
    conn: sqlite3.Connection,
    task: Dict[str, Any],
) -> Dict[str, Any]:
    worker_id = str(task.get("locked_by") or "").strip()
    if not worker_id:
        raise TransferConflict("task has not been assigned to a worker")
    keys = sorted(
        {
            str(item.get("cache_key") or "").strip()
            for item in task.get("payload", {}).get("input_paths") or []
            if str(item.get("cache_key") or "").strip()
        }
    )
    now = utc_now()
    for cache_key in keys:
        conn.execute(
            """
            INSERT INTO transfer_cache_release_requests (
                id, task_id, worker_id, cache_key, status, created_at
            ) VALUES (?, ?, ?, ?, 'pending', ?)
            ON CONFLICT(task_id, worker_id, cache_key) DO UPDATE SET
                status = CASE
                    WHEN transfer_cache_release_requests.status = 'completed'
                    THEN 'completed' ELSE 'pending' END,
                error = NULL
            """,
            (str(uuid.uuid4()), task["id"], worker_id, cache_key, now),
        )
    return {"task_id": task["id"], "worker_id": worker_id, "requested": len(keys)}


def pending_cache_releases(
    conn: sqlite3.Connection,
    worker_id: str,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, task_id, cache_key, created_at
        FROM transfer_cache_release_requests
        WHERE worker_id = ? AND status = 'pending'
        ORDER BY created_at ASC
        """,
        (worker_id,),
    ).fetchall()
    active_cache_keys = set()
    active_rows = conn.execute(
        """
        SELECT payload FROM tasks
        WHERE locked_by = ? AND status = 'running'
        """,
        (worker_id,),
    ).fetchall()
    for active_row in active_rows:
        payload = json.loads(active_row["payload"] or "{}")
        active_cache_keys.update(
            str(item.get("cache_key") or "")
            for item in payload.get("input_paths") or []
            if item.get("cache_key")
        )
    return [dict(row) for row in rows if row["cache_key"] not in active_cache_keys]


def complete_cache_release(
    conn: sqlite3.Connection,
    request_id: str,
    worker_id: str,
    error: Optional[str] = None,
) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM transfer_cache_release_requests
        WHERE id = ? AND worker_id = ?
        """,
        (request_id, worker_id),
    ).fetchone()
    if row is None:
        raise TransferNotFound(request_id)
    status = "failed" if error else "completed"
    conn.execute(
        """
        UPDATE transfer_cache_release_requests
        SET status = ?, completed_at = ?, error = ?
        WHERE id = ?
        """,
        (status, utc_now(), error, request_id),
    )
    return {"id": request_id, "status": status}


def publish_task_result(
    conn: sqlite3.Connection,
    task: Dict[str, Any],
    *,
    status: str,
    result: Optional[Dict[str, Any]],
    error: Optional[str],
    error_code: Optional[str],
    logs: Optional[str],
) -> Optional[Dict[str, Any]]:
    raw_destination = str(task.get("result_path") or "").strip()
    if not raw_destination:
        return None
    destination = Path(raw_destination)
    marker_name = "_cloudlink_task.json"
    if destination.exists():
        marker = destination / marker_name
        if marker.is_file():
            existing = json.loads(marker.read_text(encoding="utf-8"))
            if existing.get("task_id") == task["id"] and existing.get("status") == status:
                reconcile_published_artifacts(conn, destination, existing)
                return existing
        raise TransferConflict(f"result destination already exists: {destination}")

    rows = conn.execute(
        """
        SELECT * FROM task_artifacts
        WHERE task_id = ?
        ORDER BY relative_path ASC
        """,
        (task["id"],),
    ).fetchall()
    artifacts = [dict(row) for row in rows]
    incomplete = [item["relative_path"] for item in artifacts if item["status"] != "uploaded"]
    if incomplete:
        raise TransferConflict(f"task artifacts are not fully uploaded: {incomplete[0]}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temp = destination.parent / f".{destination.name}.cloudlink-{task['id']}.tmp"
    if temp.exists():
        shutil.rmtree(temp)
    temp.mkdir(parents=True)
    published = []
    try:
        for artifact in artifacts:
            relative = Path(artifact["relative_path"])
            target = temp / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(artifact["storage_path"], target)
            if (
                target.stat().st_size != int(artifact["size_bytes"])
                or sha256_file(target) != artifact["sha256"]
            ):
                raise TransferConflict(
                    f"published artifact verification failed: {artifact['relative_path']}"
                )
            with target.open("rb") as file:
                os.fsync(file.fileno())
            published.append(
                {
                    "artifact_id": artifact["id"],
                    "path": artifact["relative_path"],
                    "size_bytes": artifact["size_bytes"],
                    "sha256": artifact["sha256"],
                }
            )
        record = {
            "task_id": task["id"],
            "type": task["type"],
            "title": task.get("title") or "",
            "status": status,
            "worker_id": task.get("locked_by"),
            "result": result,
            "error": error,
            "error_code": error_code,
            "logs": logs,
            "artifacts": published,
            "published_at": utc_now(),
        }
        marker = temp / marker_name
        with marker.open("w", encoding="utf-8") as file:
            json.dump(record, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        directory_fd = os.open(temp, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
        os.replace(temp, destination)
        parent_fd = os.open(destination.parent, os.O_RDONLY)
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
    finally:
        if temp.exists():
            shutil.rmtree(temp)

    reconcile_published_artifacts(conn, destination, record)
    return record


def reconcile_published_artifacts(
    conn: sqlite3.Connection,
    destination: Path,
    record: Dict[str, Any],
) -> None:
    for published in record.get("artifacts") or []:
        artifact = conn.execute(
            "SELECT * FROM task_artifacts WHERE id = ?",
            (published["artifact_id"],),
        ).fetchone()
        if artifact is None:
            raise TransferConflict(
                f"published artifact record is missing: {published['artifact_id']}"
            )
        artifact = dict(artifact)
        if artifact["task_id"] != record["task_id"]:
            raise TransferConflict("published artifact belongs to another task")
        relative_path = safe_job_path(published["path"], "published artifact path")
        final_path = destination / relative_path
        if (
            not final_path.is_file()
            or final_path.stat().st_size != int(published["size_bytes"])
            or sha256_file(final_path) != published["sha256"]
        ):
            raise TransferConflict(
                f"published artifact verification failed: {published['path']}"
            )
        conn.execute(
            """
            UPDATE task_artifacts
            SET storage_path = ?, status = 'published', expires_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (str(final_path), utc_now(), artifact["id"]),
        )
        old_path = Path(artifact["storage_path"])
        if old_path != final_path:
            old_path.unlink(missing_ok=True)
        parent = old_path.parent
        while parent.name and parent != Path(parent.anchor):
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent
