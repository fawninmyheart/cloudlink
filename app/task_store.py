import hashlib
import hmac
import json
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.config import Settings, get_settings
from app.dataset_store import released_dataset_ids, worker_has_verified_caches
from app.resource_model import (
    ResourceValidationError,
    fits_capacity,
    normalize_resource_request,
    subtract_reserved_profile,
)
from app.version import version_at_least


TERMINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}
VALID_DATASET_ROOT_MODES = {"active", "readonly", "disabled"}
WORKER_SECRET_SCHEME = "sha256"


class TaskConflict(Exception):
    pass


class TaskNotFound(Exception):
    pass


class WorkerNotRegistered(Exception):
    pass


class PayloadTooLarge(Exception):
    pass


class ResourceUnsatisfiable(Exception):
    def __init__(self, detail: Dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(detail.get("message", "Resource request is unsatisfiable"))


class QueueLimitExceeded(Exception):
    def __init__(self, detail: Dict[str, Any]) -> None:
        self.detail = detail
        super().__init__(detail.get("message", "Queue pending limit exceeded"))


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def json_bytes(value: Dict[str, Any]) -> int:
    return len(json.dumps(value, ensure_ascii=False).encode("utf-8"))


def result_sha256(value: Dict[str, Any]) -> str:
    encoded = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def validate_json_size(value: Dict[str, Any], settings: Settings) -> None:
    if json_bytes(value) > settings.max_json_bytes:
        raise PayloadTooLarge("JSON field is too large")


def validate_text_size(value: Optional[str], settings: Settings) -> None:
    if value is not None and len(value.encode("utf-8")) > settings.max_text_bytes:
        raise PayloadTooLarge("Text field is too large")


def row_to_task(row: sqlite3.Row) -> Dict[str, Any]:
    task = dict(row)
    task["payload"] = json.loads(task["payload"])
    task["result"] = json.loads(task["result"]) if task["result"] else None
    task["resource_request"] = (
        json.loads(task["resource_request"]) if task.get("resource_request") else None
    )
    task["resource_reservation"] = (
        json.loads(task["resource_reservation"])
        if task.get("resource_reservation")
        else None
    )
    task["resource_rejection"] = (
        json.loads(task["resource_rejection"]) if task.get("resource_rejection") else None
    )
    return task


def row_to_worker(row: sqlite3.Row, online_seconds: int) -> Dict[str, Any]:
    worker = dict(row)
    worker.pop("worker_secret_hash", None)
    worker["supported_types"] = json.loads(worker["supported_types"])
    worker["enabled"] = bool(worker["enabled"])
    worker["gpu_requested"] = bool(worker.get("gpu_requested"))
    worker["hardware_profile"] = (
        json.loads(worker["hardware_profile"]) if worker.get("hardware_profile") else None
    )
    worker["runtime_profile"] = (
        json.loads(worker["runtime_profile"]) if worker.get("runtime_profile") else None
    )
    worker["capacity_state"] = (
        json.loads(worker["capacity_state"]) if worker.get("capacity_state") else None
    )
    worker["dataset_root_checks"] = (
        json.loads(worker["dataset_root_checks"])
        if worker.get("dataset_root_checks")
        else []
    )
    worker["configured_dataset_roots"] = (
        json.loads(worker["configured_dataset_roots"])
        if worker.get("configured_dataset_roots")
        else None
    )
    worker["configured_reserve_overrides"] = (
        json.loads(worker["configured_reserve_overrides"])
        if worker.get("configured_reserve_overrides")
        else {}
    )
    worker["online"] = is_recent(worker["last_seen_at"], online_seconds)
    attach_worker_version_state(worker)
    return worker


def attach_worker_version_state(worker: Dict[str, Any]) -> None:
    settings = get_settings()
    server_version = settings.cloudlink_version
    base_minimum_worker_version = settings.minimum_worker_version
    gpu_minimum_worker_version = settings.minimum_gpu_worker_version
    minimum_worker_version = (
        gpu_minimum_worker_version
        if worker.get("gpu_requested")
        else base_minimum_worker_version
    )
    runtime_profile = worker.get("runtime_profile") or {}
    worker_version = str(runtime_profile.get("cloudlink_version") or "").strip()
    worker["server_version"] = server_version
    worker["base_minimum_worker_version"] = base_minimum_worker_version
    worker["gpu_minimum_worker_version"] = gpu_minimum_worker_version
    worker["minimum_worker_version"] = minimum_worker_version
    worker["required_version"] = minimum_worker_version
    worker["worker_version"] = worker_version or None
    unsupported_platform = worker_uses_unsupported_platform(worker)
    worker["version_status"] = (
        "unsupported_platform"
        if unsupported_platform
        else "ok"
        if version_at_least(worker_version, minimum_worker_version)
        else "needs_update"
    )
    worker["needs_update"] = worker["version_status"] != "ok"


def worker_uses_unsupported_platform(worker: Dict[str, Any]) -> bool:
    install_platform = str(worker.get("install_platform") or "").strip().lower()
    if install_platform == "windows":
        return True
    runtime = worker.get("runtime_profile") or {}
    runtime_system = str(runtime.get("system") or "").strip().lower()
    return runtime_system.startswith("windows") or runtime_system == "win32"


def worker_is_schedulable(worker: Dict[str, Any]) -> bool:
    return (
        bool(worker.get("enabled"))
        and worker.get("lifecycle_status", "active") == "active"
        and bool(worker.get("online"))
        and not bool(worker.get("needs_update"))
    )


def hash_worker_secret(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return f"{WORKER_SECRET_SCHEME}${digest}"


def verify_worker_secret(secret: str, encoded: str) -> bool:
    try:
        scheme, digest = encoded.split("$", 1)
    except ValueError:
        return False
    if scheme != WORKER_SECRET_SCHEME:
        return False
    return hmac.compare_digest(hash_worker_secret(secret), encoded)


def get_worker_secret_hash(conn: sqlite3.Connection, worker_id: str) -> str:
    row = conn.execute(
        "SELECT worker_secret_hash FROM worker_nodes WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    if row is None:
        raise WorkerNotRegistered(worker_id)
    return row["worker_secret_hash"] or ""


def normalize_dataset_roots(roots: Optional[Iterable[Dict[str, Any]]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    seen = set()
    active_seen = False
    for item in roots or []:
        if not isinstance(item, dict):
            raise TaskConflict("dataset_roots entries must be objects")
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        mode = str(item.get("mode") or "active").strip().lower()
        if mode not in VALID_DATASET_ROOT_MODES:
            raise TaskConflict("dataset root mode must be active, readonly, or disabled")
        if mode == "active":
            if active_seen:
                mode = "readonly"
            else:
                active_seen = True
        label = str(item.get("label") or "").strip()
        root = {"path": path, "mode": mode}
        if label:
            root["label"] = label
        normalized.append(root)
        seen.add(path)

    if normalized and not any(root["mode"] == "active" for root in normalized):
        for root in normalized:
            if root["mode"] != "disabled":
                root["mode"] = "active"
                break
    return normalized


def runtime_dataset_roots(worker: Dict[str, Any]) -> List[Dict[str, str]]:
    runtime = worker.get("runtime_profile") or {}
    roots: List[Dict[str, Any]] = []
    if isinstance(runtime.get("dataset_roots"), list):
        roots.extend(item for item in runtime["dataset_roots"] if isinstance(item, dict))
    dataset_root = str(runtime.get("dataset_root") or "").strip()
    if dataset_root and not any(str(item.get("path") or "").strip() == dataset_root for item in roots):
        roots.append(
            {
                "path": dataset_root,
                "mode": "active",
            }
        )
    return normalize_dataset_roots(roots)


def effective_dataset_roots(worker: Dict[str, Any]) -> List[Dict[str, str]]:
    configured = worker.get("configured_dataset_roots") or []
    if configured:
        return normalize_dataset_roots(configured)
    return runtime_dataset_roots(worker)


def effective_worker_settings(worker: Dict[str, Any]) -> Dict[str, Any]:
    runtime = worker.get("runtime_profile") or {}
    return {
        "max_concurrent_tasks": int(worker.get("max_concurrent_tasks") or 1),
        "job_root": worker.get("configured_job_root") or runtime.get("job_root"),
        "dataset_roots": effective_dataset_roots(worker),
        "reserve_overrides": worker.get("configured_reserve_overrides") or {},
    }


def preserve_existing_dataset_roots(
    conn: sqlite3.Connection,
    worker: Dict[str, Any],
    submitted_roots: List[Dict[str, str]],
) -> List[Dict[str, str]]:
    roots = list(submitted_roots)
    seen = {root["path"] for root in roots}
    for old_root in effective_dataset_roots(worker):
        old_path = old_root["path"]
        if old_path in seen or old_root.get("mode") == "disabled":
            continue
        if not worker_has_dataset_cache_under_root(conn, worker["worker_id"], old_path):
            continue
        roots.append(
            {
                "path": old_path,
                "mode": "readonly",
                "label": old_root.get("label") or "历史数据盘",
            }
        )
        seen.add(old_path)
    return normalize_dataset_roots(roots)


def worker_has_dataset_cache_under_root(
    conn: sqlite3.Connection,
    worker_id: str,
    root_path: str,
) -> bool:
    root = root_path.rstrip("/")
    if not root:
        return False
    row = conn.execute(
        """
        SELECT 1
        FROM worker_dataset_caches
        WHERE worker_id = ?
          AND status IN ('cached', 'extracted', 'downloading')
          AND (
              data_root_path = ?
              OR local_archive_path = ?
              OR local_extracted_path = ?
              OR local_archive_path LIKE ?
              OR local_extracted_path LIKE ?
          )
        LIMIT 1
        """,
        (
            worker_id,
            root,
            root,
            root,
            f"{root}/%",
            f"{root}/%",
        ),
    ).fetchone()
    return row is not None


def is_recent(timestamp: Optional[str], seconds: int) -> bool:
    if not timestamp:
        return False
    seen_at = datetime.fromisoformat(timestamp)
    return datetime.now(timezone.utc) - seen_at <= timedelta(seconds=seconds)


def create_task(
    conn: sqlite3.Connection,
    task_type: str,
    payload: Dict[str, Any],
    title: str = "",
    description: str = "",
    submitter_id: Optional[str] = None,
    group_id: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    now = utc_now()
    expire_pending_tasks(conn, now, settings.queue_timeout_seconds)
    validate_json_size(payload, settings)
    validate_text_size(title, settings)
    validate_text_size(description, settings)
    check_pending_limit(conn, settings)
    resource_request = normalize_task_resource_request(task_type, payload)
    runtime = str(payload.get("runtime") or "python-auto").strip()
    gpu_required = bool((resource_request.get("gpu") or {}).get("required"))
    if task_type == "script_job" and (runtime == "pytorch-cuda") != gpu_required:
        raise ResourceUnsatisfiable(
            {
                "code": "gpu_runtime_declaration_invalid",
                "message": (
                    "pytorch-cuda runtime and resource_request.gpu.required=true "
                    "must be declared together."
                ),
            }
        )
    if has_resource_requirements(resource_request) or runtime == "pytorch-cuda":
        rejection = resource_request_rejection(
            conn,
            task_type,
            resource_request,
            settings,
            payload=payload,
        )
        if rejection is not None:
            raise ResourceUnsatisfiable(rejection)

    dataset_rejection = dataset_availability_rejection(
        conn,
        task_type,
        payload,
        resource_request,
        settings,
    )
    if dataset_rejection is not None:
        raise ResourceUnsatisfiable(dataset_rejection)

    task_id = str(uuid.uuid4())
    normalized_submitter = normalize_submitter_id(submitter_id)
    normalized_group = normalize_group_id(group_id or task_context_value(payload, "group_id"))
    conn.execute(
        """
        INSERT INTO tasks (
            id, type, status, title, description, payload,
            resource_request, submitter_id, group_id,
            created_at, updated_at, retry_count
        )
        VALUES (?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, ?, 0)
        """,
        (
            task_id,
            task_type,
            title,
            description,
            json.dumps(payload, ensure_ascii=False),
            json.dumps(resource_request, ensure_ascii=False),
            normalized_submitter,
            normalized_group,
            now,
            now,
        ),
    )
    return get_task(conn, task_id)


def normalize_submitter_id(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def normalize_group_id(value: Optional[str]) -> Optional[str]:
    text = str(value or "").strip()
    return text or None


def task_context_value(payload: Dict[str, Any], key: str) -> Optional[str]:
    context = payload.get("task_context")
    if not isinstance(context, dict):
        return None
    value = context.get(key)
    if value is None:
        return None
    return str(value)


def pending_count(conn: sqlite3.Connection) -> int:
    row = conn.execute(
        "SELECT COUNT(*) AS count FROM tasks WHERE status = 'pending'"
    ).fetchone()
    return int(row["count"])


def check_pending_limit(conn: sqlite3.Connection, settings: Settings) -> None:
    count = pending_count(conn)
    if count < settings.max_pending_tasks:
        return
    raise QueueLimitExceeded(
        {
            "code": "max_pending_exceeded",
            "message": "Queue pending task limit has been reached.",
            "pending_count": count,
            "max_pending": settings.max_pending_tasks,
        }
    )


def get_task(conn: sqlite3.Connection, task_id: str) -> Dict[str, Any]:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise TaskNotFound(task_id)
    return row_to_task(row)


def list_tasks(
    conn: sqlite3.Connection,
    limit: int = 100,
    submitter_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where = ""
    params: List[Any] = []
    if submitter_id is not None:
        where = "WHERE submitter_id = ?"
        params.append(submitter_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT * FROM tasks
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row_to_task(row) for row in rows]


def row_to_task_summary(row: sqlite3.Row) -> Dict[str, Any]:
    return dict(row)


def list_task_summaries(
    conn: sqlite3.Connection,
    limit: int = 100,
    submitter_id: Optional[str] = None,
) -> List[Dict[str, Any]]:
    where = ""
    params: List[Any] = []
    if submitter_id is not None:
        where = "WHERE submitter_id = ?"
        params.append(submitter_id)
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT
            id, type, status,
            created_at, updated_at, started_at, finished_at,
            locked_by, retry_count, submitter_id, group_id, error_code,
            cancel_requested_at, cancel_reason
        FROM tasks
        {where}
        ORDER BY created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [row_to_task_summary(row) for row in rows]


def task_summary(
    conn: sqlite3.Connection,
    submitter_id: Optional[str] = None,
) -> Dict[str, int]:
    summary = {
        status: 0
        for status in ["pending", "running", "success", "failed", "timeout", "cancelled"]
    }
    where = ""
    params: List[Any] = []
    if submitter_id is not None:
        where = "WHERE submitter_id = ?"
        params.append(submitter_id)
    rows = conn.execute(
        f"SELECT status, COUNT(*) AS count FROM tasks {where} GROUP BY status",
        params,
    ).fetchall()
    for row in rows:
        summary[row["status"]] = row["count"]
    summary["total"] = sum(summary.values())
    return summary


def register_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    display_name: str,
    supported_types: Iterable[str],
    enabled: bool,
    install_platform: Optional[str] = None,
    hardware_profile: Optional[Dict[str, Any]] = None,
    runtime_profile: Optional[Dict[str, Any]] = None,
    capacity_state: Optional[Dict[str, Any]] = None,
    max_concurrent_tasks: int = 1,
    active_task_count: int = 0,
    worker_secret_hash: Optional[str] = None,
    gpu_requested: bool = False,
    gpu_environment_path: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    allowed_supported = sorted(set(supported_types) & settings.allowed_task_types)
    if not allowed_supported:
        raise TaskConflict("Worker must support at least one allowed task type")
    if max_concurrent_tasks < 1:
        raise TaskConflict("max_concurrent_tasks must be at least 1")
    if active_task_count < 0:
        raise TaskConflict("active_task_count must be non-negative")

    now = utc_now()
    conn.execute(
        """
        INSERT INTO worker_nodes (
            worker_id, display_name, supported_types, install_platform, enabled,
            hardware_profile, runtime_profile, capacity_state, worker_secret_hash,
            max_concurrent_tasks, active_task_count,
            registered_at, updated_at, lifecycle_status, lifecycle_updated_at,
            gpu_requested, gpu_environment_path
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?, ?)
        ON CONFLICT(worker_id) DO UPDATE SET
            display_name = excluded.display_name,
            supported_types = excluded.supported_types,
            install_platform = COALESCE(excluded.install_platform, worker_nodes.install_platform),
            enabled = excluded.enabled,
            hardware_profile = excluded.hardware_profile,
            runtime_profile = excluded.runtime_profile,
            capacity_state = excluded.capacity_state,
            worker_secret_hash = COALESCE(excluded.worker_secret_hash, worker_nodes.worker_secret_hash),
            max_concurrent_tasks = excluded.max_concurrent_tasks,
            active_task_count = excluded.active_task_count,
            lifecycle_status = 'active',
            lifecycle_updated_at = excluded.lifecycle_updated_at,
            gpu_requested = excluded.gpu_requested,
            gpu_environment_path = excluded.gpu_environment_path,
            updated_at = excluded.updated_at
        """,
        (
            worker_id,
            display_name or worker_id,
            json.dumps(allowed_supported, ensure_ascii=False),
            install_platform,
            1 if enabled else 0,
            json.dumps(hardware_profile, ensure_ascii=False)
            if hardware_profile is not None
            else None,
            json.dumps(runtime_profile, ensure_ascii=False)
            if runtime_profile is not None
            else None,
            json.dumps(capacity_state, ensure_ascii=False)
            if capacity_state is not None
            else None,
            worker_secret_hash,
            max_concurrent_tasks,
            active_task_count,
            now,
            now,
            now,
            1 if gpu_requested else 0,
            gpu_environment_path,
        ),
    )
    return get_worker(conn, worker_id)


def get_worker(conn: sqlite3.Connection, worker_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM worker_nodes WHERE worker_id = ?",
        (worker_id,),
    ).fetchone()
    if row is None:
        raise WorkerNotRegistered(worker_id)
    return row_to_worker(row, get_settings().worker_online_seconds)


def list_workers(conn: sqlite3.Connection) -> List[Dict[str, Any]]:
    settings = get_settings()
    rows = conn.execute(
        """
        SELECT * FROM worker_nodes
        ORDER BY enabled DESC, worker_id ASC
        """
    ).fetchall()
    return [
        worker_with_running_resource_summary(
            conn,
            row_to_worker(row, settings.worker_online_seconds),
        )
        for row in rows
    ]


def update_worker_concurrency(
    conn: sqlite3.Connection,
    worker_id: str,
    max_concurrent_tasks: int,
) -> Dict[str, Any]:
    if max_concurrent_tasks < 1:
        raise TaskConflict("max_concurrent_tasks must be at least 1")
    get_worker(conn, worker_id)
    conn.execute(
        """
        UPDATE worker_nodes
        SET max_concurrent_tasks = ?,
            updated_at = ?
        WHERE worker_id = ?
        """,
        (max_concurrent_tasks, utc_now(), worker_id),
    )
    return get_worker(conn, worker_id)


def update_worker_settings(
    conn: sqlite3.Connection,
    worker_id: str,
    *,
    max_concurrent_tasks: int,
    job_root: Optional[str] = None,
    dataset_roots: Optional[Iterable[Dict[str, Any]]] = None,
    reserve_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if max_concurrent_tasks < 1:
        raise TaskConflict("max_concurrent_tasks must be at least 1")
    worker = get_worker(conn, worker_id)
    configured_job_root = str(job_root or "").strip() or worker.get("configured_job_root")
    if dataset_roots is None:
        normalized_roots = worker.get("configured_dataset_roots") or []
    else:
        normalized_roots = preserve_existing_dataset_roots(
            conn,
            worker,
            normalize_dataset_roots(dataset_roots),
        )
    normalized_reserve = (
        worker.get("configured_reserve_overrides") or {}
        if reserve_overrides is None
        else normalize_reserve_overrides(reserve_overrides)
    )
    conn.execute(
        """
        UPDATE worker_nodes
        SET max_concurrent_tasks = ?,
            configured_job_root = ?,
            configured_dataset_roots = ?,
            configured_reserve_overrides = ?,
            updated_at = ?
        WHERE worker_id = ?
        """,
        (
            max_concurrent_tasks,
            configured_job_root,
            json.dumps(normalized_roots, ensure_ascii=False),
            json.dumps(normalized_reserve, ensure_ascii=False),
            utc_now(),
            worker_id,
        ),
    )
    return get_worker(conn, worker_id)


def normalize_reserve_overrides(value: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(value, dict):
        raise TaskConflict("reserve_overrides must be an object")
    normalized: Dict[str, Any] = {}
    for key in (
        "cpu_cores",
        "memory_bytes",
        "job_disk_bytes",
        "dataset_disk_bytes",
        "gpu_memory_bytes",
    ):
        raw = value.get(key)
        if raw in (None, ""):
            continue
        try:
            number = float(raw)
        except (TypeError, ValueError) as exc:
            raise TaskConflict(f"{key} must be a number") from exc
        if number < 0:
            raise TaskConflict(f"{key} must be non-negative")
        if key == "cpu_cores":
            if number != int(number):
                raise TaskConflict("cpu_cores must be an integer")
            normalized[key] = int(number)
        else:
            if number != int(number):
                raise TaskConflict(f"{key} must be an integer")
            normalized[key] = int(number)
    return normalized


def touch_worker(
    conn: sqlite3.Connection,
    worker_id: str,
    supported_types: Optional[Iterable[str]] = None,
    last_error: Optional[str] = None,
    claimed: bool = False,
    hardware_profile: Optional[Dict[str, Any]] = None,
    runtime_profile: Optional[Dict[str, Any]] = None,
    capacity_state: Optional[Dict[str, Any]] = None,
    dataset_root_checks: Optional[List[Dict[str, Any]]] = None,
    max_concurrent_tasks: Optional[int] = None,
    active_task_count: Optional[int] = None,
) -> Dict[str, Any]:
    worker = get_worker(conn, worker_id)
    if not worker["enabled"]:
        raise WorkerNotRegistered(worker_id)

    now = utc_now()
    updates = ["last_seen_at = ?", "updated_at = ?", "last_error = ?"]
    values: List[Any] = [now, now, last_error]
    if claimed:
        updates.append("last_claimed_at = ?")
        values.append(now)
    if hardware_profile is not None:
        updates.append("hardware_profile = ?")
        values.append(json.dumps(hardware_profile, ensure_ascii=False))
    if runtime_profile is not None:
        updates.append("runtime_profile = ?")
        values.append(json.dumps(runtime_profile, ensure_ascii=False))
    if capacity_state is not None:
        updates.append("capacity_state = ?")
        values.append(json.dumps(capacity_state, ensure_ascii=False))
    if dataset_root_checks is not None:
        updates.append("dataset_root_checks = ?")
        values.append(json.dumps(dataset_root_checks, ensure_ascii=False))
    if max_concurrent_tasks is not None:
        if max_concurrent_tasks < 1:
            raise TaskConflict("max_concurrent_tasks must be at least 1")
        updates.append("max_concurrent_tasks = ?")
        values.append(max_concurrent_tasks)
    if active_task_count is not None:
        if active_task_count < 0:
            raise TaskConflict("active_task_count must be non-negative")
        updates.append("active_task_count = ?")
        values.append(active_task_count)
    values.append(worker_id)
    conn.execute(
        f"UPDATE worker_nodes SET {', '.join(updates)} WHERE worker_id = ?",
        values,
    )
    return get_worker(conn, worker_id)


def expire_locked_tasks(conn: sqlite3.Connection, now: str) -> None:
    conn.execute(
        """
        UPDATE tasks
        SET status = 'timeout',
            error_code = 'worker_lost',
            error = 'Worker lease expired; task was not automatically retried',
            updated_at = ?,
            finished_at = ?,
            locked_until = NULL
        WHERE status = 'running'
          AND locked_until IS NOT NULL
          AND locked_until <= ?
        """,
        (now, now, now),
    )


def resume_task_delivery(
    conn: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    previous_lease_id: str,
) -> Dict[str, Any]:
    task = get_task(conn, task_id)
    if task["type"] != "script_job":
        raise TaskConflict("Only script_job delivery can be resumed")
    if task.get("locked_by") != worker_id:
        raise TaskConflict("Task is not owned by this worker")
    recoverable_terminal = (
        task["status"] == "failed"
        and task.get("error_code") == "artifact_upload_failed"
    ) or (
        task["status"] == "timeout"
        and task.get("error_code") == "worker_lost"
    )
    same_running_lease = (
        task["status"] == "running"
        and task.get("lease_id") == previous_lease_id
    )
    artifact_lease = conn.execute(
        """
        SELECT 1 FROM task_artifacts
        WHERE task_id = ? AND worker_id = ? AND lease_id = ?
        LIMIT 1
        """,
        (task_id, worker_id, previous_lease_id),
    ).fetchone()
    preserved_terminal_lease = (
        recoverable_terminal
        and (
            task.get("lease_id") == previous_lease_id
            or artifact_lease is not None
        )
    )
    if not same_running_lease and not preserved_terminal_lease:
        raise TaskConflict("Task delivery lease cannot be resumed")

    now = utc_now()
    lease_id = str(uuid.uuid4())
    locked_until = (
        parse_utc(now) + timedelta(seconds=get_settings().task_lock_seconds)
    ).isoformat()
    conn.execute(
        """
        UPDATE tasks
        SET status = 'running',
            error = NULL,
            error_code = NULL,
            logs = NULL,
            updated_at = ?,
            finished_at = NULL,
            locked_until = ?,
            lease_id = ?,
            resource_reservation = NULL
        WHERE id = ?
        """,
        (now, locked_until, lease_id, task_id),
    )
    conn.execute(
        """
        UPDATE task_artifacts
        SET lease_id = ?,
            expires_at = NULL,
            updated_at = ?
        WHERE task_id = ? AND worker_id = ?
        """,
        (lease_id, now, task_id, worker_id),
    )
    return {
        "status": "ok",
        "lease_id": lease_id,
        "locked_until": locked_until,
        "payload": task["payload"],
    }


def expire_pending_tasks(
    conn: sqlite3.Connection,
    now: str,
    queue_timeout_seconds: int,
) -> None:
    deadline = (
        datetime.fromisoformat(now) - timedelta(seconds=queue_timeout_seconds)
    ).isoformat()
    conn.execute(
        """
        UPDATE tasks
        SET status = 'timeout',
            error_code = 'queue_timeout',
            error = 'Queue timeout before worker claim',
            updated_at = ?,
            finished_at = ?
        WHERE status = 'pending'
          AND created_at <= ?
        """,
        (now, now, deadline),
    )


def queue_status(conn: sqlite3.Connection) -> Dict[str, Any]:
    settings = get_settings()
    now = utc_now()
    expire_pending_tasks(conn, now, settings.queue_timeout_seconds)
    expire_locked_tasks(conn, now)
    expire_unavailable_dataset_tasks(conn, settings)
    counts = task_summary(conn)
    oldest = conn.execute(
        """
        SELECT created_at
        FROM tasks
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 1
        """
    ).fetchone()
    oldest_age = None
    if oldest is not None:
        oldest_age = max(0, int((parse_utc(now) - parse_utc(oldest["created_at"])).total_seconds()))
    workers = list_workers(conn)
    online_workers = [worker for worker in workers if worker_is_schedulable(worker)]
    return {
        "pending_count": int(counts.get("pending", 0)),
        "running_count": int(counts.get("running", 0)),
        "max_pending": settings.max_pending_tasks,
        "queue_timeout_seconds": settings.queue_timeout_seconds,
        "oldest_pending_age_seconds": oldest_age,
        "online_worker_count": len(online_workers),
        "worker_count": len(workers),
        "resource_totals": aggregate_worker_resources(workers),
    }


def aggregate_worker_resources(workers: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals: Dict[str, Any] = {
        "scheduler": {
            "cpu_cores": 0,
            "memory_bytes": 0,
            "job_disk_bytes": 0,
            "dataset_disk_bytes": 0,
            "gpu_count": 0,
            "gpu_memory_bytes": 0,
        },
        "available": {
            "cpu_cores": 0,
            "memory_bytes": 0,
            "job_disk_bytes": 0,
            "dataset_disk_bytes": 0,
            "gpu_count": 0,
            "gpu_memory_bytes": 0,
        },
        "reserved": {
            "cpu_cores": 0,
            "memory_bytes": 0,
            "job_disk_bytes": 0,
            "dataset_disk_bytes": 0,
            "gpu_count": 0,
            "gpu_memory_bytes": 0,
        },
    }
    for worker in workers:
        if not worker_is_schedulable(worker):
            continue
        scheduler = (worker.get("hardware_profile") or {}).get("scheduler") or {}
        available = worker.get("capacity_state") or {}
        reserved = worker.get("reserved_resources") or {}
        for key in ("cpu_cores", "memory_bytes", "job_disk_bytes", "dataset_disk_bytes"):
            totals["scheduler"][key] += int(scheduler.get(key) or 0)
            totals["available"][key] += int(available.get(key) or 0)
            totals["reserved"][key] += int(reserved.get(key) or 0)
        scheduler_gpus = scheduler.get("gpu_devices") or []
        available_gpus = available.get("gpu_devices") or []
        totals["scheduler"]["gpu_count"] += len(scheduler_gpus)
        totals["scheduler"]["gpu_memory_bytes"] += sum(
            int(device.get("memory_bytes") or 0) for device in scheduler_gpus
        )
        totals["available"]["gpu_count"] += sum(
            1 for device in available_gpus if int(device.get("memory_bytes") or 0) > 0
        )
        totals["available"]["gpu_memory_bytes"] += sum(
            int(device.get("memory_bytes") or 0) for device in available_gpus
        )
        totals["reserved"]["gpu_count"] += int(reserved.get("gpu_count") or 0)
        totals["reserved"]["gpu_memory_bytes"] += int(
            reserved.get("gpu_memory_bytes") or 0
        )
    return totals


def normalize_task_resource_request(
    task_type: str,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    if task_type != "script_job":
        return normalize_resource_request({})
    return normalize_resource_request(payload.get("resource_request") or {})


def has_resource_requirements(request: Dict[str, Any]) -> bool:
    gpu = request.get("gpu") or {}
    return any(
        [
            request.get("cpu_cores", 0) > 0,
            request.get("memory_bytes", 0) > 0,
            request.get("job_disk_bytes", 0) > 0,
            request.get("dataset_disk_bytes", 0) > 0,
            gpu.get("required"),
        ]
    )


def worker_ideal_capacity(worker: Dict[str, Any]) -> Dict[str, Any]:
    profile = worker.get("hardware_profile") or {}
    scheduler = profile.get("scheduler") or {}
    return {
        "cpu_cores": _whole_cpu_capacity(scheduler.get("cpu_cores")),
        "memory_bytes": scheduler.get("memory_bytes"),
        "job_disk_bytes": scheduler.get("job_disk_bytes"),
        "dataset_disk_bytes": scheduler.get("dataset_disk_bytes"),
        "gpu_devices": scheduler.get("gpu_devices") or [],
    }


def _whole_cpu_capacity(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return None


def _capacity_alias(state: Dict[str, Any], canonical: str, *aliases: str) -> Any:
    for key in (canonical, *aliases):
        if key in state:
            return state[key]
    return None


def worker_current_capacity(worker: Dict[str, Any]) -> Dict[str, Any]:
    ideal = worker_ideal_capacity(worker)
    state = worker.get("capacity_state") or {}
    capacity: Dict[str, Any] = {"gpu_devices": state.get("gpu_devices") or ideal.get("gpu_devices") or []}
    capacity["cpu_cores"] = _whole_cpu_capacity(ideal.get("cpu_cores"))
    aliases = {
        "memory_bytes": ("memory_available_bytes",),
        "job_disk_bytes": ("job_disk_free_bytes",),
        "dataset_disk_bytes": ("dataset_disk_free_bytes",),
    }
    for key, key_aliases in aliases.items():
        state_value = _capacity_alias(state, key, *key_aliases)
        ideal_value = ideal.get(key)
        if state_value is None:
            capacity[key] = ideal_value
        elif ideal_value is None:
            capacity[key] = state_value
        else:
            capacity[key] = min(float(state_value), float(ideal_value))
        if capacity.get(key) is not None:
            capacity[key] = int(capacity[key])
    return capacity


def subtract_running_reservations(
    conn: sqlite3.Connection,
    worker_id: str,
    capacity: Dict[str, Any],
) -> Dict[str, Any]:
    adjusted = dict(capacity)
    now = utc_now()
    rows = conn.execute(
        """
        SELECT resource_reservation
        FROM tasks
        WHERE status = 'running'
          AND locked_by = ?
          AND resource_reservation IS NOT NULL
          AND (locked_until IS NULL OR locked_until > ?)
        """,
        (worker_id, now),
    ).fetchall()
    for row in rows:
        reservation = json.loads(row["resource_reservation"])
        for key in ("cpu_cores", "memory_bytes", "job_disk_bytes", "dataset_disk_bytes"):
            if adjusted.get(key) is not None:
                adjusted[key] = max(0, adjusted[key] - reservation.get(key, 0))
        adjusted["gpu_devices"] = _subtract_gpu_reservation(
            adjusted.get("gpu_devices") or [],
            reservation.get("gpu") or {},
        )
    return adjusted


def _subtract_gpu_reservation(
    devices: List[Dict[str, Any]],
    gpu_request: Dict[str, Any],
) -> List[Dict[str, Any]]:
    adjusted = [dict(device) for device in devices]
    if not gpu_request.get("required"):
        return adjusted
    requested_count = max(1, int(gpu_request.get("count") or 1))
    requested_memory = max(0, int(gpu_request.get("memory_bytes") or 0))
    candidates = sorted(
        range(len(adjusted)),
        key=lambda index: int(adjusted[index].get("memory_bytes") or 0),
    )
    selected: List[int] = []
    for index in candidates:
        if int(adjusted[index].get("memory_bytes") or 0) >= requested_memory:
            selected.append(index)
            if len(selected) >= requested_count:
                break
    if len(selected) < requested_count:
        return adjusted
    for index in selected:
        adjusted[index]["memory_bytes"] = max(
            0,
            int(adjusted[index].get("memory_bytes") or 0) - requested_memory,
        )
    return adjusted


def running_task_count(conn: sqlite3.Connection, worker_id: str) -> int:
    now = utc_now()
    row = conn.execute(
        """
        SELECT COUNT(*) AS count
        FROM tasks
        WHERE status = 'running'
          AND locked_by = ?
          AND (locked_until IS NULL OR locked_until > ?)
        """,
        (worker_id, now),
    ).fetchone()
    return int(row["count"])


def running_resource_totals(
    conn: sqlite3.Connection,
    worker_id: str,
) -> Dict[str, Any]:
    now = utc_now()
    totals = {
        "cpu_cores": 0,
        "memory_bytes": 0,
        "job_disk_bytes": 0,
        "dataset_disk_bytes": 0,
        "gpu_count": 0,
        "gpu_memory_bytes": 0,
    }
    rows = conn.execute(
        """
        SELECT resource_reservation
        FROM tasks
        WHERE status = 'running'
          AND locked_by = ?
          AND resource_reservation IS NOT NULL
          AND (locked_until IS NULL OR locked_until > ?)
        """,
        (worker_id, now),
    ).fetchall()
    for row in rows:
        reservation = json.loads(row["resource_reservation"])
        for key in ("cpu_cores", "memory_bytes", "job_disk_bytes", "dataset_disk_bytes"):
            totals[key] += int(reservation.get(key) or 0)
        gpu = reservation.get("gpu") or {}
        if gpu.get("required"):
            count = max(1, int(gpu.get("count") or 1))
            totals["gpu_count"] += count
            totals["gpu_memory_bytes"] += count * int(gpu.get("memory_bytes") or 0)
    return totals


def worker_with_running_resource_summary(
    conn: sqlite3.Connection,
    worker: Dict[str, Any],
) -> Dict[str, Any]:
    reserved = running_resource_totals(conn, worker["worker_id"])
    worker = worker_with_configured_resource_view(worker)
    capacity = dict(worker.get("capacity_state") or {})
    scheduler = (worker.get("hardware_profile") or {}).get("scheduler") or {}
    if scheduler.get("cpu_cores") is not None:
        capacity["cpu_cores"] = _whole_cpu_capacity(scheduler.get("cpu_cores"))
    for key in ("cpu_cores", "memory_bytes", "job_disk_bytes", "dataset_disk_bytes"):
        if capacity.get(key) is not None and scheduler.get(key) is not None:
            capacity[key] = min(int(capacity[key]), int(scheduler[key]))
    capacity = subtract_running_reservations(conn, worker["worker_id"], capacity)
    worker["reported_capacity_state"] = worker.get("capacity_state") or {}
    worker["capacity_state"] = capacity
    worker["reserved_resources"] = reserved
    return worker


def worker_with_configured_resource_view(worker: Dict[str, Any]) -> Dict[str, Any]:
    overrides = worker.get("configured_reserve_overrides") or {}
    hardware = worker.get("hardware_profile") or {}
    raw = hardware.get("raw") if isinstance(hardware, dict) else None
    if not overrides or not isinstance(raw, dict):
        return worker

    configured_hardware = subtract_reserved_profile(raw, overrides)
    worker["reported_hardware_profile"] = hardware
    worker["hardware_profile"] = configured_hardware
    return worker


def resource_request_rejection(
    conn: sqlite3.Connection,
    task_type: str,
    resource_request: Dict[str, Any],
    settings: Settings,
    payload: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    workers = list_workers(conn)
    candidates = [
        worker
        for worker in workers
        if worker["enabled"]
        and not worker.get("needs_update")
        and task_type in worker["supported_types"]
        and task_type in settings.allowed_task_types
        and worker_supports_runtime(worker, payload or {})
    ]
    shortages = []
    for worker in candidates:
        ok, worker_shortages = fits_capacity(
            resource_request,
            worker_ideal_capacity(worker),
        )
        if ok:
            return None
        shortages.append(
            {
                "worker_id": worker["worker_id"],
                "shortages": worker_shortages,
            }
        )
    return {
        "code": "resource_request_unsatisfiable",
        "message": "No registered worker can satisfy this resource request.",
        "resource_request": resource_request,
        "candidates": [
            {
                "worker_id": worker["worker_id"],
                "supported_types": worker["supported_types"],
                "capacity": worker_ideal_capacity(worker),
            }
            for worker in candidates
        ],
        "shortages": shortages,
    }


def worker_supports_runtime(worker: Dict[str, Any], payload: Dict[str, Any]) -> bool:
    runtime = str(payload.get("runtime") or "python-auto").strip()
    if runtime != "pytorch-cuda":
        return True
    gpu_runtime = (worker.get("runtime_profile") or {}).get("gpu_runtime") or {}
    return bool(
        worker.get("gpu_requested")
        and gpu_runtime.get("enabled")
        and gpu_runtime.get("verified")
    )


def cpu_worker_currently_available(
    conn: sqlite3.Connection,
    *,
    exclude_worker_id: str,
    task_type: str,
    resource_request: Dict[str, Any],
) -> bool:
    for candidate in list_workers(conn):
        gpu_runtime = (candidate.get("runtime_profile") or {}).get("gpu_runtime") or {}
        if (
            candidate["worker_id"] != exclude_worker_id
            and worker_is_schedulable(candidate)
            and task_type in candidate.get("supported_types", [])
            and not gpu_runtime.get("enabled")
            and running_task_count(conn, candidate["worker_id"])
            < int(candidate.get("max_concurrent_tasks") or 1)
        ):
            available = subtract_running_reservations(
                conn,
                candidate["worker_id"],
                worker_current_capacity(candidate),
            )
            if fits_capacity(resource_request, available)[0]:
                return True
    return False


def dataset_availability_rejection(
    conn: sqlite3.Connection,
    task_type: str,
    payload: Dict[str, Any],
    resource_request: Dict[str, Any],
    settings: Settings,
) -> Optional[Dict[str, Any]]:
    released = released_dataset_ids(conn, payload.get("datasets") or [])
    if not released:
        return None
    eligible = []
    for worker in list_workers(conn):
        if (
            worker_is_schedulable(worker)
            and task_type in worker.get("supported_types", [])
            and worker_supports_runtime(worker, payload)
            and worker_has_verified_caches(conn, worker["worker_id"], released)
            and fits_capacity(resource_request, worker_ideal_capacity(worker))[0]
        ):
            eligible.append(worker["worker_id"])
    if eligible:
        return None
    return {
        "code": "dataset_unavailable",
        "message": (
            "No online schedulable worker has verified caches for every released "
            "dataset required by this task."
        ),
        "released_dataset_version_ids": released,
        "eligible_worker_ids": [],
    }


def expire_unavailable_dataset_tasks(
    conn: sqlite3.Connection,
    settings: Settings,
) -> int:
    rows = conn.execute(
        "SELECT * FROM tasks WHERE status = 'pending' ORDER BY created_at ASC"
    ).fetchall()
    expired = 0
    now = utc_now()
    for row in rows:
        task = row_to_task(row)
        released = released_dataset_ids(
            conn,
            task["payload"].get("datasets") or [],
        )
        if not released:
            continue
        rejection = dataset_availability_rejection(
            conn,
            task["type"],
            task["payload"],
            task["resource_request"] or normalize_resource_request({}),
            settings,
        )
        if rejection is None:
            continue
        conn.execute(
            """
            UPDATE tasks
            SET status = 'failed',
                error_code = 'dataset_became_unavailable',
                error = ?,
                updated_at = ?,
                finished_at = ?
            WHERE id = ? AND status = 'pending'
            """,
            (
                "Required released dataset cache is no longer available",
                now,
                now,
                task["id"],
            ),
        )
        expired += 1
    return expired


def claim_task(
    conn: sqlite3.Connection,
    worker_id: str,
    supported_types: Iterable[str],
    capacity_state: Optional[Dict[str, Any]] = None,
    active_task_count: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    settings = get_settings()
    now = utc_now()

    try:
        conn.execute("BEGIN IMMEDIATE")
        worker = get_worker(conn, worker_id)
        if not worker["enabled"]:
            raise WorkerNotRegistered(worker_id)

        expire_pending_tasks(conn, now, settings.queue_timeout_seconds)
        expire_unavailable_dataset_tasks(conn, settings)
        expire_locked_tasks(conn, now)
        registered_supported = set(worker["supported_types"])
        allowed_supported = sorted(
            set(supported_types) & registered_supported & settings.allowed_task_types
        )
        touch_worker(
            conn,
            worker_id,
            supported_types,
            claimed=False,
            capacity_state=capacity_state,
            active_task_count=active_task_count,
        )
        worker = get_worker(conn, worker_id)
        if worker.get("needs_update"):
            conn.execute("COMMIT")
            return None
        if worker.get("lifecycle_status", "active") != "active":
            conn.execute("COMMIT")
            return None
        if not allowed_supported:
            conn.execute("COMMIT")
            return None
        max_concurrent = int(worker.get("max_concurrent_tasks") or 1)
        current_active = max(
            int(worker.get("active_task_count") or 0),
            running_task_count(conn, worker_id),
        )
        if current_active >= max_concurrent:
            conn.execute("COMMIT")
            return None

        placeholders = ",".join("?" for _ in allowed_supported)
        rows = conn.execute(
            f"""
            SELECT * FROM tasks
            WHERE type IN ({placeholders})
              AND status = 'pending'
            ORDER BY created_at ASC
            """,
            allowed_supported,
        ).fetchall()

        row = None
        resource_request: Dict[str, Any] = normalize_resource_request({})
        available_capacity = subtract_running_reservations(
            conn,
            worker_id,
            worker_current_capacity(worker),
        )
        for candidate in rows:
            candidate_task = row_to_task(candidate)
            if not worker_supports_runtime(worker, candidate_task["payload"]):
                continue
            request_gpu = (candidate_task["resource_request"] or {}).get("gpu") or {}
            worker_gpu = (worker.get("runtime_profile") or {}).get("gpu_runtime") or {}
            if (
                not request_gpu.get("required")
                and worker_gpu.get("enabled")
                and cpu_worker_currently_available(
                    conn,
                    exclude_worker_id=worker_id,
                    task_type=candidate_task["type"],
                    resource_request=candidate_task["resource_request"]
                    or normalize_resource_request({}),
                )
            ):
                continue
            released = released_dataset_ids(
                conn,
                candidate_task["payload"].get("datasets") or [],
            )
            if released and not worker_has_verified_caches(conn, worker_id, released):
                continue
            resource_request = (
                candidate_task["resource_request"] or normalize_resource_request({})
            )
            ideal_ok, _ideal_shortages = fits_capacity(
                resource_request,
                worker_ideal_capacity(worker),
            )
            current_ok, _current_shortages = fits_capacity(
                resource_request,
                available_capacity,
            )
            if ideal_ok and current_ok:
                row = candidate
                break
            if (
                ideal_ok
                and not current_ok
                and candidate_task["status"] == "pending"
                and pending_age_seconds(candidate_task, now)
                >= settings.starvation_protection_seconds
            ):
                conn.execute("COMMIT")
                return None

        if row is None:
            conn.execute("COMMIT")
            return None

        lease_seconds = settings.task_lock_seconds
        locked_until = (
            datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)
        ).isoformat()
        started_at = row["started_at"] or now
        lease_id = str(uuid.uuid4())
        conn.execute(
            """
            UPDATE tasks
            SET status = 'running',
                updated_at = ?,
                started_at = ?,
                finished_at = NULL,
                locked_by = ?,
                locked_until = ?,
                lease_id = ?,
                resource_reservation = ?,
                cancel_requested_at = NULL,
                cancel_reason = NULL
            WHERE id = ?
            """,
            (
                now,
                started_at,
                worker_id,
                locked_until,
                lease_id,
                json.dumps(resource_request, ensure_ascii=False),
                row["id"],
            ),
        )
        touch_worker(conn, worker_id, supported_types, claimed=True)
        conn.execute("COMMIT")
        claimed = get_task(conn, row["id"])
        return {
            "id": claimed["id"],
            "type": claimed["type"],
            "payload": claimed["payload"],
            "lease_id": claimed["lease_id"],
        }
    except Exception:
        conn.execute("ROLLBACK")
        raise


def pending_age_seconds(task: Dict[str, Any], now: str) -> int:
    return max(0, int((parse_utc(now) - parse_utc(task["created_at"])).total_seconds()))


def renew_task_lease(
    conn: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    lease_id: str,
) -> Dict[str, Any]:
    now = utc_now()
    expire_locked_tasks(conn, now)
    task = _assert_owned_running_task(conn, task_id, worker_id, lease_id)
    locked_until = (
        parse_utc(now) + timedelta(seconds=get_settings().task_lock_seconds)
    ).isoformat()
    conn.execute(
        """
        UPDATE tasks
        SET locked_until = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (locked_until, now, task_id),
    )
    return {
        "status": "ok",
        "locked_until": locked_until,
        "cancel_requested": bool(task.get("cancel_requested_at")),
        "cancel_reason": task.get("cancel_reason") or "",
    }


def set_task_artifact_expiry(
    conn: sqlite3.Connection,
    task_id: str,
    terminal_at: str,
) -> None:
    expires_at = (
        parse_utc(terminal_at)
        + timedelta(seconds=get_settings().artifact_retention_seconds)
    ).isoformat()
    conn.execute(
        """
        UPDATE task_artifacts
        SET expires_at = COALESCE(expires_at, ?),
            updated_at = ?
        WHERE task_id = ?
        """,
        (expires_at, terminal_at, task_id),
    )


def _assert_owned_running_task(
    conn: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    lease_id: str,
) -> Dict[str, Any]:
    task = get_task(conn, task_id)
    if task["status"] in TERMINAL_STATUSES:
        raise TaskConflict("Task is already finished")
    if task["status"] != "running":
        raise TaskConflict("Task is not running")
    if task["locked_by"] != worker_id:
        raise TaskConflict("Task is not locked by this worker")
    if task["lease_id"] != lease_id:
        raise TaskConflict("Task lease does not match")
    return task


def assert_task_owned_by_worker(
    conn: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    lease_id: str,
) -> Dict[str, Any]:
    return _assert_owned_running_task(conn, task_id, worker_id, lease_id)


def task_visible_to_submitter(task: Dict[str, Any], submitter_id: Optional[str]) -> bool:
    if submitter_id is None:
        return True
    return task.get("submitter_id") == submitter_id


def get_task_for_submitter(
    conn: sqlite3.Connection,
    task_id: str,
    submitter_id: Optional[str],
) -> Dict[str, Any]:
    task = get_task(conn, task_id)
    if not task_visible_to_submitter(task, submitter_id):
        raise TaskNotFound(task_id)
    return task


def cancel_task(
    conn: sqlite3.Connection,
    task_id: str,
    submitter_id: Optional[str],
    reason: str = "",
) -> Dict[str, Any]:
    settings = get_settings()
    now = utc_now()
    expire_pending_tasks(conn, now, settings.queue_timeout_seconds)
    task = get_task_for_submitter(conn, task_id, submitter_id)
    if task["status"] in TERMINAL_STATUSES:
        return task
    if task["status"] == "running":
        message = reason.strip() if reason.strip() else "Task cancelled by submitter"
        validate_text_size(message, settings)
        conn.execute(
            """
            UPDATE tasks
            SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                cancel_reason = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (now, message, now, task_id),
        )
        return get_task(conn, task_id)
    elif task["status"] != "pending":
        raise TaskConflict("Task cannot be cancelled")
    message = reason.strip() if reason.strip() else "Task cancelled by submitter"
    validate_text_size(message, settings)
    conn.execute(
        """
        UPDATE tasks
        SET status = 'cancelled',
            error_code = 'cancelled',
            error = ?,
            updated_at = ?,
            finished_at = ?,
            locked_until = NULL,
            lease_id = NULL
        WHERE id = ?
        """,
        (message, now, now, task_id),
    )
    set_task_artifact_expiry(conn, task_id, now)
    return get_task(conn, task_id)


def report_success(
    conn: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    lease_id: str,
    reported_result_sha256: Optional[str],
    result: Dict[str, Any],
    logs: Optional[str],
) -> Dict[str, Any]:
    settings = get_settings()
    validate_json_size(result, settings)
    validate_text_size(logs, settings)
    actual_result_sha256 = result_sha256(result)
    if reported_result_sha256 and not hmac.compare_digest(
        reported_result_sha256,
        actual_result_sha256,
    ):
        raise TaskConflict("Result SHA-256 does not match payload")

    existing = get_task(conn, task_id)
    if existing["status"] == "success":
        if (
            existing.get("completion_lease_id") == lease_id
            and existing.get("result_sha256") == actual_result_sha256
        ):
            return existing
        raise TaskConflict("Task already succeeded with a different completion")
    task = _assert_owned_running_task(conn, task_id, worker_id, lease_id)

    now = utc_now()
    if task.get("cancel_requested_at"):
        conn.execute(
            """
            UPDATE tasks
            SET status = 'cancelled',
                error = ?,
                error_code = 'cancelled',
                updated_at = ?,
                finished_at = ?,
                locked_until = NULL,
                lease_id = NULL
            WHERE id = ?
            """,
            (
                task.get("cancel_reason") or "Task cancelled by submitter",
                now,
                now,
                task_id,
            ),
        )
        set_task_artifact_expiry(conn, task_id, now)
        return get_task(conn, task_id)
    conn.execute(
        """
        UPDATE tasks
        SET status = 'success',
            result = ?,
            result_sha256 = ?,
            completion_lease_id = ?,
            logs = ?,
            error = NULL,
            error_code = NULL,
            updated_at = ?,
            finished_at = ?,
            locked_until = NULL,
            lease_id = NULL
        WHERE id = ?
        """,
        (
            json.dumps(result, ensure_ascii=False),
            actual_result_sha256,
            lease_id,
            logs,
            now,
            now,
            task_id,
        ),
    )
    set_task_artifact_expiry(conn, task_id, now)
    return get_task(conn, task_id)


def report_failed(
    conn: sqlite3.Connection,
    task_id: str,
    worker_id: str,
    lease_id: str,
    error: str,
    logs: Optional[str],
    error_code: Optional[str] = None,
) -> Dict[str, Any]:
    settings = get_settings()
    validate_text_size(error, settings)
    validate_text_size(logs, settings)
    task = _assert_owned_running_task(conn, task_id, worker_id, lease_id)

    now = utc_now()
    normalized_error_code = str(error_code or "").strip() or None
    if task.get("cancel_requested_at"):
        status = "cancelled"
        normalized_error_code = "cancelled"
        error = task.get("cancel_reason") or "Task cancelled by submitter"
    elif normalized_error_code == "execution_timeout":
        status = "timeout"
    elif normalized_error_code == "cancelled":
        status = "cancelled"
    else:
        status = "failed"
    conn.execute(
        """
        UPDATE tasks
        SET status = ?,
            error = ?,
            error_code = ?,
            logs = ?,
            updated_at = ?,
            finished_at = ?,
            locked_until = NULL,
            lease_id = NULL
        WHERE id = ?
        """,
        (status, error, normalized_error_code, logs, now, now, task_id),
    )
    set_task_artifact_expiry(conn, task_id, now)
    return get_task(conn, task_id)
