import os
from dataclasses import dataclass
from typing import List, Mapping, Optional


class WorkerConfigError(Exception):
    pass


@dataclass(frozen=True)
class WorkerConfig:
    base_url: str
    worker_secret: str
    worker_id: str
    supported_types: List[str]
    api_timeout_seconds: float
    api_retries: int
    api_retry_base_seconds: float
    api_retry_max_seconds: float
    artifact_upload_retries: int
    artifact_retry_base_seconds: float
    artifact_retry_max_seconds: float
    poll_interval_seconds: float
    heartbeat_seconds: float
    dataset_api_timeout_seconds: float
    dataset_download_timeout_seconds: float
    maintenance_interval_seconds: float
    max_concurrent_tasks: int = 1
    reserve_cpu_cores: Optional[float] = None
    reserve_memory_bytes: Optional[int] = None
    reserve_disk_bytes: Optional[int] = None
    reserve_job_disk_bytes: Optional[int] = None
    reserve_dataset_disk_bytes: Optional[int] = None
    reserve_gpu_memory_bytes: Optional[int] = None


def csv_list(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _env(env: Mapping[str, str], name: str, default: Optional[str] = None) -> str:
    value = env.get(name, default if default is not None else "")
    return str(value).strip()


def _required(env: Mapping[str, str], name: str) -> str:
    value = _env(env, name)
    if not value:
        raise WorkerConfigError(f"{name} is required")
    return value


def _positive_float(env: Mapping[str, str], name: str, default: str) -> float:
    raw = _env(env, name, default)
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkerConfigError(f"{name} must be a number") from exc
    if value <= 0:
        raise WorkerConfigError(f"{name} must be positive")
    return value


def _non_negative_int(env: Mapping[str, str], name: str, default: str) -> int:
    raw = _env(env, name, default)
    try:
        value = int(raw)
    except ValueError as exc:
        raise WorkerConfigError(f"{name} must be an integer") from exc
    if value < 0:
        raise WorkerConfigError(f"{name} must be non-negative")
    return value


def _positive_int(env: Mapping[str, str], name: str, default: str) -> int:
    value = _non_negative_int(env, name, default)
    if value <= 0:
        raise WorkerConfigError(f"{name} must be positive")
    return value


def _optional_float(env: Mapping[str, str], name: str) -> Optional[float]:
    raw = _env(env, name, "")
    if not raw:
        return None
    try:
        value = float(raw)
    except ValueError as exc:
        raise WorkerConfigError(f"{name} must be a number") from exc
    if value < 0:
        raise WorkerConfigError(f"{name} must be non-negative")
    return value


def _optional_gb_bytes(env: Mapping[str, str], name: str) -> Optional[int]:
    value = _optional_float(env, name)
    if value is None:
        return None
    return int(value * 1024**3)


def load_worker_config(env: Optional[Mapping[str, str]] = None) -> WorkerConfig:
    source = os.environ if env is None else env
    supported_types = csv_list(
        _env(source, "WORKER_SUPPORTED_TYPES", "echo_test,generate_daily_report")
    )
    if not supported_types:
        raise WorkerConfigError("WORKER_SUPPORTED_TYPES cannot be empty")

    worker_id = _env(source, "WORKER_ID", "local-worker-1")
    if not worker_id:
        raise WorkerConfigError("WORKER_ID cannot be empty")

    return WorkerConfig(
        base_url=_required(source, "CLOUD_API_BASE_URL").rstrip("/"),
        worker_secret=_required(source, "WORKER_SECRET"),
        worker_id=worker_id,
        supported_types=supported_types,
        api_timeout_seconds=_positive_float(source, "WORKER_API_TIMEOUT_SECONDS", "20"),
        api_retries=_non_negative_int(source, "WORKER_API_RETRIES", "3"),
        api_retry_base_seconds=_positive_float(
            source,
            "WORKER_API_RETRY_BASE_SECONDS",
            "1",
        ),
        api_retry_max_seconds=_positive_float(
            source,
            "WORKER_API_RETRY_MAX_SECONDS",
            "15",
        ),
        artifact_upload_retries=_non_negative_int(
            source,
            "CLOUDLINK_ARTIFACT_UPLOAD_RETRIES",
            "6",
        ),
        artifact_retry_base_seconds=_positive_float(
            source,
            "CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS",
            "2",
        ),
        artifact_retry_max_seconds=_positive_float(
            source,
            "CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS",
            "60",
        ),
        poll_interval_seconds=_positive_float(
            source,
            "WORKER_POLL_INTERVAL_SECONDS",
            "5",
        ),
        heartbeat_seconds=_positive_float(source, "WORKER_HEARTBEAT_SECONDS", "30"),
        dataset_api_timeout_seconds=_positive_float(
            source,
            "CLOUDLINK_DATASET_API_TIMEOUT_SECONDS",
            "20",
        ),
        dataset_download_timeout_seconds=_positive_float(
            source,
            "CLOUDLINK_DATASET_DOWNLOAD_TIMEOUT_SECONDS",
            "300",
        ),
        maintenance_interval_seconds=_positive_float(
            source,
            "WORKER_MAINTENANCE_INTERVAL_SECONDS",
            "60",
        ),
        max_concurrent_tasks=_positive_int(
            source,
            "WORKER_MAX_CONCURRENT_TASKS",
            "1",
        ),
        reserve_cpu_cores=_optional_float(source, "WORKER_RESERVE_CPU_CORES"),
        reserve_memory_bytes=_optional_gb_bytes(source, "WORKER_RESERVE_MEMORY_GB"),
        reserve_disk_bytes=_optional_gb_bytes(source, "WORKER_RESERVE_DISK_GB"),
        reserve_job_disk_bytes=_optional_gb_bytes(source, "WORKER_RESERVE_JOB_DISK_GB"),
        reserve_dataset_disk_bytes=_optional_gb_bytes(
            source,
            "WORKER_RESERVE_DATASET_DISK_GB",
        ),
        reserve_gpu_memory_bytes=_optional_gb_bytes(
            source,
            "WORKER_RESERVE_GPU_MEMORY_GB",
        ),
    )
