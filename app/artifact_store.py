import hashlib
import os
import sqlite3
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.task_store import utc_now


class ArtifactConflict(Exception):
    pass


class ArtifactNotFound(Exception):
    pass


def data_root() -> Path:
    return Path(os.getenv("CLOUDLINK_DATA_ROOT", "/opt/cloudlink/data")).expanduser().resolve()


def safe_relative_path(value: str) -> Path:
    if not value.strip():
        raise ArtifactConflict("artifact relative_path must stay inside outputs")
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ArtifactConflict("artifact relative_path must stay inside outputs")
    if path.name in {"", ".", ".."}:
        raise ArtifactConflict("artifact relative_path must stay inside outputs")
    return path


def artifact_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    item = dict(row)
    item["required"] = bool(item["required"])
    return item


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def get_artifact(conn: sqlite3.Connection, artifact_id: str) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM task_artifacts WHERE id = ?",
        (artifact_id,),
    ).fetchone()
    if row is None:
        raise ArtifactNotFound(artifact_id)
    return artifact_to_dict(row)


def get_artifact_for_task_path(
    conn: sqlite3.Connection,
    task_id: str,
    relative_path: str,
) -> Dict[str, Any]:
    row = conn.execute(
        """
        SELECT * FROM task_artifacts
        WHERE task_id = ? AND relative_path = ?
        """,
        (task_id, str(safe_relative_path(relative_path))),
    ).fetchone()
    if row is None:
        raise ArtifactNotFound(relative_path)
    return artifact_to_dict(row)


def list_task_artifacts(
    conn: sqlite3.Connection,
    task_id: str,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT * FROM task_artifacts
        WHERE task_id = ?
        ORDER BY relative_path ASC
        """,
        (task_id,),
    ).fetchall()
    return [artifact_to_dict(row) for row in rows]


def list_recent_artifacts(
    conn: sqlite3.Connection,
    limit: int = 200,
) -> List[Dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            task_artifacts.*,
            tasks.title AS task_title,
            tasks.description AS task_description,
            tasks.status AS task_status
        FROM task_artifacts
        JOIN tasks ON tasks.id = task_artifacts.task_id
        ORDER BY task_artifacts.created_at DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [artifact_to_dict(row) for row in rows]


def create_artifact_record(
    conn: sqlite3.Connection,
    *,
    task_id: str,
    worker_id: str,
    lease_id: str,
    relative_path: str,
    title: str,
    description: str,
    meaning: str,
    content_type: Optional[str],
    size_bytes: int,
    sha256: str,
    required: bool = True,
) -> Dict[str, Any]:
    safe_path = safe_relative_path(relative_path)
    artifact_id = str(uuid.uuid4())
    display_name = safe_path.name
    storage_path = (
        data_root()
        / "artifacts"
        / "tasks"
        / task_id
        / artifact_id
        / display_name
    )
    now = utc_now()
    artifact_values = {
        "worker_id": worker_id,
        "lease_id": lease_id,
        "relative_path": str(safe_path),
        "display_name": display_name,
        "title": title or display_name,
        "description": description or "",
        "meaning": meaning or "",
        "content_type": content_type,
        "size_bytes": int(size_bytes),
        "sha256": sha256,
        "required": 1 if required else 0,
    }
    try:
        conn.execute(
            """
            INSERT INTO task_artifacts (
                id, task_id, worker_id, lease_id, relative_path, display_name,
                title, description, meaning, content_type, size_bytes, sha256,
                storage_path, status, required, created_at, updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'created', ?, ?, ?)
            """,
            (
                artifact_id,
                task_id,
                artifact_values["worker_id"],
                artifact_values["lease_id"],
                artifact_values["relative_path"],
                artifact_values["display_name"],
                artifact_values["title"],
                artifact_values["description"],
                artifact_values["meaning"],
                artifact_values["content_type"],
                artifact_values["size_bytes"],
                artifact_values["sha256"],
                str(storage_path),
                artifact_values["required"],
                now,
                now,
            ),
        )
    except sqlite3.IntegrityError as exc:
        existing = get_artifact_for_task_path(conn, task_id, str(safe_path))
        matching_keys = (
            "worker_id",
            "lease_id",
            "relative_path",
            "display_name",
            "title",
            "description",
            "meaning",
            "content_type",
            "size_bytes",
            "sha256",
            "required",
        )
        if all(existing[key] == artifact_values[key] for key in matching_keys):
            return existing
        raise ArtifactConflict(str(exc)) from exc
    return get_artifact(conn, artifact_id)


def mark_artifact_uploaded(
    conn: sqlite3.Connection,
    artifact_id: str,
    *,
    size_bytes: Optional[int] = None,
    sha256: Optional[str] = None,
) -> Dict[str, Any]:
    artifact = get_artifact(conn, artifact_id)
    now = utc_now()
    conn.execute(
        """
        UPDATE task_artifacts
        SET status = 'uploaded',
            size_bytes = ?,
            sha256 = ?,
            updated_at = ?
        WHERE id = ?
        """,
        (
            artifact["size_bytes"] if size_bytes is None else int(size_bytes),
            artifact["sha256"] if sha256 is None else sha256,
            now,
            artifact_id,
        ),
    )
    return get_artifact(conn, artifact_id)


def mark_artifact_uploading(conn: sqlite3.Connection, artifact_id: str) -> Dict[str, Any]:
    conn.execute(
        """
        UPDATE task_artifacts
        SET status = 'uploading',
            updated_at = ?
        WHERE id = ?
        """,
        (utc_now(), artifact_id),
    )
    return get_artifact(conn, artifact_id)


def artifact_target_path(artifact: Dict[str, Any]) -> Path:
    return Path(artifact["storage_path"])


def artifact_part_path(artifact: Dict[str, Any]) -> Path:
    target = artifact_target_path(artifact)
    return target.parent / f"{target.name}.part"


def file_matches_artifact(path: Path, artifact: Dict[str, Any]) -> bool:
    if not path.exists() or path.stat().st_size != int(artifact["size_bytes"]):
        return False
    return sha256_file(path) == artifact["sha256"]


def artifact_upload_status(
    conn: sqlite3.Connection,
    artifact_id: str,
) -> Dict[str, Any]:
    artifact = get_artifact(conn, artifact_id)
    target = artifact_target_path(artifact)
    part = artifact_part_path(artifact)
    if artifact["status"] == "uploaded":
        if file_matches_artifact(target, artifact):
            artifact["uploaded_bytes"] = artifact["size_bytes"]
            return artifact
        raise ArtifactConflict("uploaded artifact content is missing or invalid")
    uploaded_bytes = 0
    if part.exists():
        uploaded_bytes = part.stat().st_size
    artifact["uploaded_bytes"] = min(uploaded_bytes, int(artifact["size_bytes"]))
    return artifact


def store_artifact_content(
    conn: sqlite3.Connection,
    artifact_id: str,
    content: bytes,
) -> Dict[str, Any]:
    artifact = get_artifact(conn, artifact_id)
    if len(content) != artifact["size_bytes"]:
        raise ArtifactConflict("artifact upload size does not match")
    digest = hashlib.sha256(content).hexdigest()
    if digest != artifact["sha256"]:
        raise ArtifactConflict("artifact upload sha256 does not match")

    target = Path(artifact["storage_path"])
    if artifact["status"] == "uploaded" and target.exists():
        if file_matches_artifact(target, artifact):
            return artifact
        raise ArtifactConflict("uploaded artifact content does not match")

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as tmp:
            tmp.write(content)
            tmp_path = Path(tmp.name)
        tmp_path.replace(target)
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink()

    return mark_artifact_uploaded(conn, artifact_id)


def store_artifact_chunk(
    conn: sqlite3.Connection,
    artifact_id: str,
    offset: int,
    content: bytes,
) -> Dict[str, Any]:
    if offset < 0:
        raise ArtifactConflict("artifact chunk offset must be non-negative")
    artifact = get_artifact(conn, artifact_id)
    target = artifact_target_path(artifact)
    if artifact["status"] == "uploaded":
        if file_matches_artifact(target, artifact):
            return artifact_upload_status(conn, artifact_id)
        raise ArtifactConflict("uploaded artifact content is missing or invalid")

    total_size = int(artifact["size_bytes"])
    if offset + len(content) > total_size:
        raise ArtifactConflict("artifact chunk exceeds declared size")

    part = artifact_part_path(artifact)
    part.parent.mkdir(parents=True, exist_ok=True)
    current_size = part.stat().st_size if part.exists() else 0
    if offset < current_size:
        existing = b""
        with part.open("rb") as file:
            file.seek(offset)
            existing = file.read(len(content))
        if len(existing) == len(content) and existing == content:
            artifact = artifact_upload_status(conn, artifact_id)
            if artifact["status"] == "created":
                artifact = mark_artifact_uploading(conn, artifact_id)
                artifact["uploaded_bytes"] = current_size
            return artifact
        raise ArtifactConflict("artifact chunk offset is behind uploaded bytes")
    if offset != current_size:
        raise ArtifactConflict("artifact chunk offset does not match uploaded bytes")

    with part.open("ab") as file:
        file.write(content)
    artifact = mark_artifact_uploading(conn, artifact_id)
    artifact["uploaded_bytes"] = part.stat().st_size
    return artifact


def complete_artifact_upload(
    conn: sqlite3.Connection,
    artifact_id: str,
) -> Dict[str, Any]:
    artifact = get_artifact(conn, artifact_id)
    target = artifact_target_path(artifact)
    if artifact["status"] == "uploaded":
        if file_matches_artifact(target, artifact):
            return artifact
        raise ArtifactConflict("uploaded artifact content is missing or invalid")

    part = artifact_part_path(artifact)
    if int(artifact["size_bytes"]) == 0:
        if artifact["sha256"] != hashlib.sha256(b"").hexdigest():
            raise ArtifactConflict("artifact upload sha256 does not match")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"")
        return mark_artifact_uploaded(conn, artifact_id)
    if not part.exists():
        raise ArtifactConflict("artifact upload has no partial content")
    if part.stat().st_size != int(artifact["size_bytes"]):
        raise ArtifactConflict("artifact upload is incomplete")
    digest = sha256_file(part)
    if digest != artifact["sha256"]:
        raise ArtifactConflict("artifact upload sha256 does not match")
    target.parent.mkdir(parents=True, exist_ok=True)
    part.replace(target)
    return mark_artifact_uploaded(conn, artifact_id)


def get_uploaded_artifact_path(conn: sqlite3.Connection, artifact_id: str) -> Path:
    artifact = get_artifact(conn, artifact_id)
    if artifact["status"] != "uploaded":
        raise ArtifactConflict("artifact is not uploaded")
    path = Path(artifact["storage_path"])
    if not path.exists():
        raise ArtifactNotFound(artifact_id)
    return path
