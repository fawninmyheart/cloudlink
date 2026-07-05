import hashlib
import mimetypes
import os
from pathlib import Path
from typing import Any, Dict, List, Optional


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_chunk_bytes() -> int:
    try:
        configured = int(os.getenv("CLOUDLINK_ARTIFACT_CHUNK_BYTES", "4194304"))
    except ValueError:
        configured = 4194304
    return max(1, configured)


class ResultArtifactUploader:
    def __init__(
        self,
        api_client: Any,
        *,
        worker_id: str,
        task_id: str,
        lease_id: str,
        expected_artifacts: Optional[List[Dict[str, Any]]] = None,
        manifest: Optional[Dict[str, Any]] = None,
        upload_retries: int = 6,
        retry_base_seconds: float = 2,
        retry_max_seconds: float = 60,
    ) -> None:
        self.api_client = api_client
        self.worker_id = worker_id
        self.task_id = task_id
        self.lease_id = lease_id
        self.upload_retries = upload_retries
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.expected_artifacts = expected_artifacts or []
        self.expected = {
            str(item["path"]): item
            for item in self.expected_artifacts
            if item.get("path")
        }
        self.raw_manifest = manifest or {}
        self.manifest = {
            str(item["path"]): item
            for item in self.raw_manifest.get("artifacts", [])
            if item.get("path")
        }

    def with_manifest(self, manifest: Dict[str, Any]) -> "ResultArtifactUploader":
        return ResultArtifactUploader(
            self.api_client,
            worker_id=self.worker_id,
            task_id=self.task_id,
            lease_id=self.lease_id,
            expected_artifacts=self.expected_artifacts,
            manifest=manifest,
            upload_retries=self.upload_retries,
            retry_base_seconds=self.retry_base_seconds,
            retry_max_seconds=self.retry_max_seconds,
        )

    def is_expected(self, relative_path: str) -> bool:
        return relative_path in self.expected

    def metadata_for(
        self,
        relative_path: str,
        size_bytes: int,
        sha256: str,
    ) -> Dict[str, Any]:
        merged: Dict[str, Any] = {}
        merged.update(self.expected.get(relative_path, {}))
        merged.update(self.manifest.get(relative_path, {}))
        title = merged.get("title") or Path(relative_path).name
        return {
            "relative_path": relative_path,
            "title": title,
            "description": merged.get("description", ""),
            "meaning": merged.get("meaning", ""),
            "content_type": (
                merged.get("content_type")
                or mimetypes.guess_type(relative_path)[0]
                or "application/octet-stream"
            ),
            "size_bytes": size_bytes,
            "sha256": sha256,
            "required": bool(merged.get("required", True)),
        }

    def upload(self, path: Path, output_dir: Path) -> Dict[str, Any]:
        relative_path = str(path.relative_to(output_dir))
        size_bytes = path.stat().st_size
        sha256 = file_sha256(path)
        metadata = self.metadata_for(relative_path, size_bytes, sha256)
        created = self.api_client.post_json(
            f"/api/worker/tasks/{self.task_id}/artifacts",
            {"worker_id": self.worker_id, "lease_id": self.lease_id, **metadata},
        )
        self.upload_file_chunks(created["id"], path, size_bytes)
        return {
            "path": relative_path,
            "title": metadata["title"],
            "description": metadata["description"],
            "meaning": metadata["meaning"],
            "size_bytes": size_bytes,
            "sha256": sha256,
            "content_omitted": True,
            "stored_on_server": True,
            "artifact_id": created["id"],
            "download_url": (
                f"/api/internal/tasks/{self.task_id}/artifacts/"
                f"{created['id']}/download"
            ),
        }

    def upload_file_chunks(self, artifact_id: str, path: Path, size_bytes: int) -> None:
        chunk_size = artifact_chunk_bytes()
        status = self.upload_status(artifact_id)
        if status.get("status") == "uploaded":
            return
        offset = int(status.get("uploaded_bytes") or 0)
        with path.open("rb") as file:
            while offset < size_bytes:
                file.seek(offset)
                chunk = file.read(min(chunk_size, size_bytes - offset))
                if not chunk:
                    break
                try:
                    response = self.api_client.put_bytes(
                        self.chunk_path(artifact_id, offset),
                        chunk,
                        retries=self.upload_retries,
                        retry_base_seconds=self.retry_base_seconds,
                        retry_max_seconds=self.retry_max_seconds,
                    )
                except Exception:
                    status = self.upload_status(artifact_id)
                    if status.get("status") == "uploaded":
                        return
                    uploaded_bytes = int(status.get("uploaded_bytes") or 0)
                    if uploaded_bytes > offset:
                        offset = uploaded_bytes
                        continue
                    raise
                if response.get("status") == "uploaded":
                    return
                uploaded_bytes = response.get("uploaded_bytes")
                offset = int(uploaded_bytes) if uploaded_bytes is not None else offset + len(chunk)
        self.complete_upload(artifact_id)

    def upload_status(self, artifact_id: str) -> Dict[str, Any]:
        return self.api_client.get_json(
            f"/api/worker/tasks/{self.task_id}/artifacts/{artifact_id}/upload-status"
        )

    def chunk_path(self, artifact_id: str, offset: int) -> str:
        return f"/api/worker/tasks/{self.task_id}/artifacts/{artifact_id}/chunks/{offset}"

    def complete_upload(self, artifact_id: str) -> None:
        try:
            response = self.api_client.post_json(
                f"/api/worker/tasks/{self.task_id}/artifacts/{artifact_id}/complete",
                {"worker_id": self.worker_id, "lease_id": self.lease_id},
                retries=self.upload_retries,
                retry_base_seconds=self.retry_base_seconds,
                retry_max_seconds=self.retry_max_seconds,
            )
        except Exception:
            status = self.upload_status(artifact_id)
            if status.get("status") == "uploaded":
                return
            raise
        if response.get("status") != "uploaded":
            status = self.upload_status(artifact_id)
            if status.get("status") != "uploaded":
                raise RuntimeError(f"artifact {artifact_id} did not finish uploading")
