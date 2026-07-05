import os
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Dict, Set, Tuple

from app.version import CLOUDLINK_VERSION, MINIMUM_WORKER_VERSION

DEFAULT_ALLOWED_TYPES = "echo_test,generate_daily_report,script_job"
INSECURE_PLACEHOLDER_VALUES = {
    "",
    "please-change-me",
    "please-change-me-legacy-worker-fallback",
    "change-this-internal-secret",
    "change-this-local-codex-token",
    "change-this-admin-password",
    "changeme",
    "password",
    "admin",
}


@dataclass(frozen=True)
class Settings:
    cloudlink_version: str
    minimum_worker_version: str
    database_path: str
    worker_secret: str
    internal_api_secret: str
    codex_token: str
    public_base_url: str
    admin_username: str
    admin_password: str
    worker_install_invite_ttl_minutes: int
    task_lock_seconds: int
    task_max_retries: int
    max_pending_tasks: int
    queue_timeout_seconds: int
    starvation_protection_seconds: int
    worker_online_seconds: int
    allowed_task_types: Set[str]
    max_json_bytes: int
    max_text_bytes: int
    codex_submitter_id: str
    codex_tokens: Dict[str, str]
    allowed_dataset_source_roots: Tuple[str, ...]
    allow_insecure_worker_install: bool


def _csv_set(value: str) -> Set[str]:
    return {item.strip() for item in value.split(",") if item.strip()}


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _path_tuple_env(name: str, default_paths: Tuple[str, ...]) -> Tuple[str, ...]:
    raw = os.getenv(name, "").strip()
    paths = raw.split(os.pathsep) if raw else list(default_paths)
    resolved = []
    for item in paths:
        text = item.strip()
        if not text:
            continue
        resolved.append(str(Path(text).expanduser().resolve()))
    return tuple(resolved)


def _codex_tokens() -> Dict[str, str]:
    raw = os.getenv("CLOUDLINK_CODEX_TOKENS", "").strip()
    if raw:
        decoded = json.loads(raw)
        if not isinstance(decoded, dict):
            raise ValueError("CLOUDLINK_CODEX_TOKENS must be a JSON object")
        return {
            str(submitter_id): str(token)
            for submitter_id, token in decoded.items()
            if str(submitter_id).strip() and str(token).strip()
        }
    token = os.getenv("CLOUDLINK_CODEX_TOKEN", "").strip()
    if not token:
        return {}
    submitter_id = os.getenv("CLOUDLINK_CODEX_SUBMITTER_ID", "codex").strip() or "codex"
    return {submitter_id: token}


def _is_insecure_placeholder(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in INSECURE_PLACEHOLDER_VALUES:
        return True
    return normalized.startswith("<") and normalized.endswith(">")


def _require_deploy_secret(name: str, value: str) -> str:
    cleaned = value.strip()
    if _is_insecure_placeholder(cleaned):
        raise ValueError(
            f"{name} must be set to a real secret before starting Cloudlink"
        )
    return cleaned


def _reject_placeholder_secret(name: str, value: str) -> str:
    cleaned = value.strip()
    if cleaned and _is_insecure_placeholder(cleaned):
        raise ValueError(f"{name} must not use an example placeholder value")
    return cleaned


def _validate_codex_tokens(tokens: Dict[str, str]) -> Dict[str, str]:
    for submitter_id, token in tokens.items():
        if _is_insecure_placeholder(token):
            raise ValueError(
                f"CLOUDLINK_CODEX_TOKENS entry for {submitter_id!r} "
                "must not use an example placeholder value"
            )
    return tokens


def get_settings() -> Settings:
    database_path = os.getenv("CLOUDLINK_DATABASE_PATH", "./cloudlink_tasks.db")
    Path(database_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    data_root = str(Path(os.getenv("CLOUDLINK_DATA_ROOT", "./data")).expanduser().resolve())
    default_dataset_source_roots = (str(Path(data_root) / "imports"),)
    worker_secret = _reject_placeholder_secret(
        "WORKER_SECRET",
        os.getenv("WORKER_SECRET", ""),
    )
    internal_api_secret = _require_deploy_secret(
        "INTERNAL_API_SECRET",
        os.getenv("INTERNAL_API_SECRET", ""),
    )
    codex_token = _reject_placeholder_secret(
        "CLOUDLINK_CODEX_TOKEN",
        os.getenv("CLOUDLINK_CODEX_TOKEN", ""),
    )
    admin_password = _require_deploy_secret(
        "ADMIN_PASSWORD",
        os.getenv("ADMIN_PASSWORD", ""),
    )
    codex_tokens = _validate_codex_tokens(_codex_tokens())

    return Settings(
        cloudlink_version=os.getenv("CLOUDLINK_VERSION", CLOUDLINK_VERSION).strip()
        or CLOUDLINK_VERSION,
        minimum_worker_version=os.getenv(
            "CLOUDLINK_MINIMUM_WORKER_VERSION",
            MINIMUM_WORKER_VERSION,
        ).strip()
        or MINIMUM_WORKER_VERSION,
        database_path=database_path,
        worker_secret=worker_secret,
        internal_api_secret=internal_api_secret,
        codex_token=codex_token,
        public_base_url=os.getenv("CLOUDLINK_PUBLIC_BASE_URL", "").rstrip("/"),
        admin_username=os.getenv("ADMIN_USERNAME", "admin"),
        admin_password=admin_password,
        worker_install_invite_ttl_minutes=int(
            os.getenv("WORKER_INSTALL_INVITE_TTL_MINUTES", "30")
        ),
        task_lock_seconds=int(os.getenv("TASK_LOCK_SECONDS", "1800")),
        task_max_retries=int(os.getenv("TASK_MAX_RETRIES", "1")),
        max_pending_tasks=_positive_int_env("CLOUDLINK_MAX_PENDING_TASKS", 10),
        queue_timeout_seconds=_positive_int_env("CLOUDLINK_QUEUE_TIMEOUT_SECONDS", 21600),
        starvation_protection_seconds=_positive_int_env(
            "CLOUDLINK_STARVATION_PROTECTION_SECONDS",
            900,
        ),
        worker_online_seconds=int(os.getenv("WORKER_ONLINE_SECONDS", "180")),
        allowed_task_types=_csv_set(
            os.getenv("TASK_ALLOWED_TYPES", DEFAULT_ALLOWED_TYPES)
        ),
        max_json_bytes=int(os.getenv("TASK_MAX_JSON_BYTES", str(1024 * 1024))),
        max_text_bytes=int(os.getenv("TASK_MAX_TEXT_BYTES", str(1024 * 1024))),
        codex_submitter_id=os.getenv("CLOUDLINK_CODEX_SUBMITTER_ID", "codex").strip()
        or "codex",
        codex_tokens=codex_tokens,
        allowed_dataset_source_roots=_path_tuple_env(
            "CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS",
            default_dataset_source_roots,
        ),
        allow_insecure_worker_install=_bool_env(
            "CLOUDLINK_ALLOW_INSECURE_WORKER_INSTALL",
            False,
        ),
    )
