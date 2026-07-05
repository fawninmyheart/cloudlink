import sqlite3
from typing import Iterator

from app.config import get_settings


SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    payload TEXT NOT NULL,
    result TEXT,
    error TEXT,
    logs TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    locked_by TEXT,
    locked_until TEXT,
    lease_id TEXT,
    resource_request TEXT,
    resource_reservation TEXT,
    resource_rejection TEXT,
    submitter_id TEXT,
    group_id TEXT,
    error_code TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_tasks_claimable
ON tasks (status, type, locked_until, created_at);

CREATE TABLE IF NOT EXISTS worker_nodes (
    worker_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    supported_types TEXT NOT NULL,
    install_platform TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    hardware_profile TEXT,
    runtime_profile TEXT,
    capacity_state TEXT,
    worker_secret_hash TEXT,
    dataset_root_checks TEXT,
    max_concurrent_tasks INTEGER NOT NULL DEFAULT 1,
    active_task_count INTEGER NOT NULL DEFAULT 0,
    configured_job_root TEXT,
    configured_dataset_roots TEXT,
    configured_reserve_overrides TEXT,
    registered_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    last_claimed_at TEXT,
    last_error TEXT
);

CREATE TABLE IF NOT EXISTS datasets (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    archived_at TEXT
);

CREATE TABLE IF NOT EXISTS dataset_versions (
    id TEXT PRIMARY KEY,
    dataset_id TEXT NOT NULL,
    version TEXT NOT NULL,
    source_kind TEXT NOT NULL,
    server_path TEXT NOT NULL,
    original_path TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    checksum_sha256 TEXT,
    content_type TEXT,
    archive_format TEXT,
    extract_required INTEGER NOT NULL DEFAULT 0,
    manifest TEXT NOT NULL,
    created_by TEXT NOT NULL,
    created_at TEXT NOT NULL,
    deleted_at TEXT,
    UNIQUE(dataset_id, version),
    FOREIGN KEY(dataset_id) REFERENCES datasets(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_dataset_versions_dataset
ON dataset_versions (dataset_id, version);

CREATE TABLE IF NOT EXISTS worker_dataset_caches (
    worker_id TEXT NOT NULL,
    dataset_version_id TEXT NOT NULL,
    status TEXT NOT NULL,
    local_archive_path TEXT,
    local_extracted_path TEXT,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    extracted_size_bytes INTEGER NOT NULL DEFAULT 0,
    checksum_sha256 TEXT,
    data_root_path TEXT,
    last_used_at TEXT,
    last_error TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY(worker_id, dataset_version_id),
    FOREIGN KEY(dataset_version_id) REFERENCES dataset_versions(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_worker_dataset_caches_version
ON worker_dataset_caches (dataset_version_id, status);

CREATE TABLE IF NOT EXISTS task_artifacts (
    id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    lease_id TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    display_name TEXT NOT NULL,
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    meaning TEXT NOT NULL DEFAULT '',
    content_type TEXT,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    status TEXT NOT NULL,
    required INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(task_id, relative_path),
    FOREIGN KEY(task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_task_artifacts_task
ON task_artifacts (task_id, relative_path);

CREATE TABLE IF NOT EXISTS admin_credentials (
    username TEXT PRIMARY KEY,
    password_hash TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS worker_install_invites (
    token_hash TEXT PRIMARY KEY,
    token_preview TEXT NOT NULL,
    worker_id TEXT NOT NULL,
    display_name TEXT NOT NULL,
    platform TEXT NOT NULL,
    public_base_url TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    used_at TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_worker_install_invites_expires
ON worker_install_invites (expires_at, used_at);
"""


def ensure_column(conn: sqlite3.Connection, table: str, column: str, ddl: str) -> None:
    columns = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")


def backfill_worker_install_platforms(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        UPDATE worker_nodes
        SET install_platform = (
            SELECT worker_install_invites.platform
            FROM worker_install_invites
            WHERE worker_install_invites.worker_id = worker_nodes.worker_id
            ORDER BY COALESCE(worker_install_invites.used_at, worker_install_invites.created_at) DESC
            LIMIT 1
        )
        WHERE (install_platform IS NULL OR install_platform = '')
          AND EXISTS (
            SELECT 1
            FROM worker_install_invites
            WHERE worker_install_invites.worker_id = worker_nodes.worker_id
          )
        """
    )


def backfill_legacy_task_submitters(conn: sqlite3.Connection) -> None:
    settings = get_settings()
    if len(settings.codex_tokens) != 1:
        return
    submitter_id = next(iter(settings.codex_tokens.keys()))
    conn.execute(
        """
        UPDATE tasks
        SET submitter_id = ?
        WHERE submitter_id IS NULL OR submitter_id = ''
        """,
        (submitter_id,),
    )


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(
        get_settings().database_path,
        timeout=30,
        isolation_level=None,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        ensure_column(conn, "tasks", "lease_id", "lease_id TEXT")
        ensure_column(conn, "tasks", "title", "title TEXT NOT NULL DEFAULT ''")
        ensure_column(
            conn,
            "tasks",
            "description",
            "description TEXT NOT NULL DEFAULT ''",
        )
        ensure_column(conn, "tasks", "resource_request", "resource_request TEXT")
        ensure_column(conn, "tasks", "resource_reservation", "resource_reservation TEXT")
        ensure_column(conn, "tasks", "resource_rejection", "resource_rejection TEXT")
        ensure_column(conn, "tasks", "submitter_id", "submitter_id TEXT")
        ensure_column(conn, "tasks", "group_id", "group_id TEXT")
        ensure_column(conn, "tasks", "error_code", "error_code TEXT")
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_tasks_submitter
            ON tasks (submitter_id, status, created_at)
            """
        )
        ensure_column(conn, "worker_nodes", "install_platform", "install_platform TEXT")
        ensure_column(conn, "worker_nodes", "hardware_profile", "hardware_profile TEXT")
        ensure_column(conn, "worker_nodes", "runtime_profile", "runtime_profile TEXT")
        ensure_column(conn, "worker_nodes", "capacity_state", "capacity_state TEXT")
        ensure_column(conn, "worker_nodes", "worker_secret_hash", "worker_secret_hash TEXT")
        ensure_column(conn, "worker_nodes", "dataset_root_checks", "dataset_root_checks TEXT")
        ensure_column(
            conn,
            "worker_nodes",
            "max_concurrent_tasks",
            "max_concurrent_tasks INTEGER NOT NULL DEFAULT 1",
        )
        ensure_column(
            conn,
            "worker_nodes",
            "active_task_count",
            "active_task_count INTEGER NOT NULL DEFAULT 0",
        )
        ensure_column(conn, "worker_nodes", "configured_job_root", "configured_job_root TEXT")
        ensure_column(
            conn,
            "worker_nodes",
            "configured_dataset_roots",
            "configured_dataset_roots TEXT",
        )
        ensure_column(
            conn,
            "worker_nodes",
            "configured_reserve_overrides",
            "configured_reserve_overrides TEXT",
        )
        ensure_column(
            conn,
            "worker_dataset_caches",
            "data_root_path",
            "data_root_path TEXT",
        )
        backfill_worker_install_platforms(conn)
        backfill_legacy_task_submitters(conn)


def get_connection() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()
