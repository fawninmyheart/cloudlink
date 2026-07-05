import hashlib
import json
import os
import re
import shutil
import tarfile
import tempfile
import urllib.parse
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

try:
    from worker.api_client import WorkerApiClient
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from api_client import WorkerApiClient


MOUNT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class UnsafeArchiveError(Exception):
    pass


@dataclass
class ResolvedDatasets:
    env: Dict[str, str]
    records: List[Dict[str, Any]]


class DatasetManager:
    def __init__(
        self,
        api_client: WorkerApiClient,
        worker_id: str,
        *,
        api_timeout_seconds: float = 20,
        download_timeout_seconds: float = 300,
        download_retries: int = 1,
    ) -> None:
        self.api_client = api_client
        self.worker_id = worker_id
        self.api_timeout_seconds = api_timeout_seconds
        self.download_timeout_seconds = download_timeout_seconds
        self.download_retries = download_retries
        self.roots = dataset_roots_from_env()
        self.root = self.active_root()

    def set_roots(self, roots: Iterable[Dict[str, Any]]) -> None:
        normalized = normalize_root_specs(roots)
        if not normalized:
            normalized = dataset_roots_from_env()
        self.roots = normalized
        self.root = self.active_root()

    def root_specs(self) -> List[Dict[str, str]]:
        return [dict(root) for root in self.roots]

    def active_root(self) -> Path:
        for root in self.roots:
            if root["mode"] == "active":
                return Path(root["path"]).expanduser()
        for root in self.roots:
            if root["mode"] != "disabled":
                return Path(root["path"]).expanduser()
        return default_dataset_root()

    def usable_roots(self) -> List[Dict[str, str]]:
        return [root for root in self.roots if root["mode"] in {"active", "readonly"}]

    def all_roots(self) -> List[Dict[str, str]]:
        return [root for root in self.roots if root["mode"] in {"active", "readonly", "disabled"}]

    def validate_roots(self) -> List[Dict[str, Any]]:
        return [self.validate_one_root(root) for root in self.root_specs()]

    def validate_one_root(self, root_spec: Dict[str, str]) -> Dict[str, Any]:
        path = Path(root_spec["path"]).expanduser()
        mode = root_spec.get("mode") or "active"
        check: Dict[str, Any] = {
            "path": str(path),
            "mode": mode,
            "label": root_spec.get("label"),
            "status": "pending",
            "readable": False,
            "writable": False,
            "total_bytes": 0,
            "free_bytes": 0,
            "cache_archive_count": 0,
            "cache_extracted_count": 0,
            "error": None,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }
        if mode == "disabled":
            check["status"] = "disabled"
            return check
        try:
            if mode == "active":
                path.mkdir(parents=True, exist_ok=True)
            if not path.exists():
                raise FileNotFoundError(f"path does not exist: {path}")
            if not path.is_dir():
                raise NotADirectoryError(f"not a directory: {path}")

            check["readable"] = os.access(path, os.R_OK)
            if not check["readable"]:
                raise PermissionError(f"path is not readable: {path}")

            check["writable"] = self.path_is_writable(path)
            if mode == "active" and not check["writable"]:
                raise PermissionError(f"active dataset root is not writable: {path}")

            usage = shutil.disk_usage(path)
            check["total_bytes"] = usage.total
            check["free_bytes"] = usage.free
            check["cache_archive_count"] = child_directory_count(path / "archives")
            check["cache_extracted_count"] = child_directory_count(path / "extracted")
            check["status"] = "ok"
        except OSError as exc:
            check["status"] = "failed"
            check["error"] = str(exc)
        return check

    def path_is_writable(self, path: Path) -> bool:
        try:
            path.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                prefix=".cloudlink-write-test-",
                dir=path,
                delete=True,
            ) as file:
                file.write(b"ok")
                file.flush()
            return True
        except OSError:
            return False

    def active_writable_root(self) -> Path:
        checks = self.validate_roots()
        for check in checks:
            if (
                check.get("mode") == "active"
                and check.get("status") == "ok"
                and check.get("writable")
            ):
                return Path(check["path"]).expanduser()
        raise ValueError("No writable active dataset root is available")

    def ensure_datasets(self, dataset_refs: Any) -> ResolvedDatasets:
        if dataset_refs in (None, ""):
            return ResolvedDatasets(env={}, records=[])
        if not isinstance(dataset_refs, list):
            raise ValueError("datasets must be a list")

        env: Dict[str, str] = {}
        records: List[Dict[str, Any]] = []
        for item in dataset_refs:
            if not isinstance(item, dict):
                raise ValueError("dataset entries must be objects")
            dataset_version_id = str(item.get("dataset_version_id", "")).strip()
            mount_name = str(item.get("mount_name", "")).strip()
            if not dataset_version_id:
                raise ValueError("dataset_version_id is required")
            if not MOUNT_NAME.match(mount_name):
                raise ValueError("mount_name must be a shell-safe identifier")

            metadata = self.fetch_metadata(dataset_version_id)
            path = self.ensure_one_dataset(metadata)
            env_key = f"CLOUDLINK_DATASET_{mount_name.upper()}"
            env[env_key] = str(path)
            records.append(
                {
                    "dataset_version_id": dataset_version_id,
                    "dataset_name": metadata.get("dataset_name"),
                    "version": metadata.get("version"),
                    "mount_name": mount_name,
                    "env": env_key,
                    "path": str(path),
                    "source_kind": metadata.get("source_kind"),
                    "manifest": metadata.get("manifest", {}),
                }
            )
        return ResolvedDatasets(env=env, records=records)

    def fetch_metadata(self, dataset_version_id: str) -> Dict[str, Any]:
        return self.api_client.get_json(
            f"/api/worker/datasets/{dataset_version_id}"
            f"?worker_id={urllib.parse.quote(self.worker_id)}",
            timeout=self.api_timeout_seconds,
        )

    def ensure_one_dataset(self, metadata: Dict[str, Any]) -> Path:
        dataset_version_id = metadata["id"]
        for root_spec in self.usable_roots():
            root = Path(root_spec["path"]).expanduser()
            archive_path = self.archive_path(root, metadata)
            if not archive_path.exists() or not self.local_file_matches(archive_path, metadata):
                continue
            if metadata.get("extract_required"):
                extracted_dir = self.extracted_dir(root, dataset_version_id)
                if self.extracted_matches(extracted_dir, archive_path, metadata):
                    self.report_cache(
                        metadata,
                        "extracted",
                        local_archive_path=str(archive_path),
                        local_extracted_path=str(extracted_dir),
                        size_bytes=archive_path.stat().st_size,
                        extracted_size_bytes=directory_size(extracted_dir),
                        checksum_sha256=file_sha256(archive_path),
                        data_root_path=str(root),
                    )
                    return extracted_dir
                if root_spec["mode"] == "active":
                    extracted_dir = self.ensure_extracted(root, archive_path, metadata)
                    self.report_cache(
                        metadata,
                        "extracted",
                        local_archive_path=str(archive_path),
                        local_extracted_path=str(extracted_dir),
                        size_bytes=archive_path.stat().st_size,
                        extracted_size_bytes=directory_size(extracted_dir),
                        checksum_sha256=file_sha256(archive_path),
                        data_root_path=str(root),
                    )
                    return extracted_dir
                continue

            self.report_cache(
                metadata,
                "cached",
                local_archive_path=str(archive_path),
                size_bytes=archive_path.stat().st_size,
                checksum_sha256=file_sha256(archive_path),
                data_root_path=str(root),
            )
            return archive_path

        root = self.active_writable_root()
        archive_dir = root / "archives" / dataset_version_id
        archive_dir.mkdir(parents=True, exist_ok=True)
        archive_path = self.archive_path(root, metadata)
        manifest_path = archive_dir / "manifest.json"

        tmp_path = archive_path.with_suffix(archive_path.suffix + ".tmp")
        self.report_cache(
            metadata,
            "downloading",
            local_archive_path=str(archive_path),
            data_root_path=str(root),
        )
        self.api_client.download_to_path(
            metadata["download_url"],
            tmp_path,
            timeout=self.download_timeout_seconds,
            retries=self.download_retries,
        )
        validate_download(tmp_path, metadata)
        tmp_path.replace(archive_path)
        manifest_path.write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        if metadata.get("extract_required"):
            extracted_dir = self.ensure_extracted(root, archive_path, metadata)
            self.report_cache(
                metadata,
                "extracted",
                local_archive_path=str(archive_path),
                local_extracted_path=str(extracted_dir),
                size_bytes=archive_path.stat().st_size,
                extracted_size_bytes=directory_size(extracted_dir),
                checksum_sha256=file_sha256(archive_path),
                data_root_path=str(root),
            )
            return extracted_dir

        self.report_cache(
            metadata,
            "cached",
            local_archive_path=str(archive_path),
            size_bytes=archive_path.stat().st_size,
            checksum_sha256=file_sha256(archive_path),
            data_root_path=str(root),
        )
        return archive_path

    def archive_path(self, root: Path, metadata: Dict[str, Any]) -> Path:
        filename = metadata.get("filename") or "source"
        return root / "archives" / metadata["id"] / safe_filename(filename)

    def extracted_dir(self, root: Path, dataset_version_id: str) -> Path:
        return root / "extracted" / dataset_version_id

    def local_file_matches(
        self,
        path: Path,
        metadata: Dict[str, Any],
        actual_checksum: Optional[str] = None,
    ) -> bool:
        expected_size = metadata.get("size_bytes")
        if expected_size is not None and path.stat().st_size != int(expected_size):
            return False
        expected_hash = metadata.get("checksum_sha256")
        if expected_hash and (actual_checksum or file_sha256(path)) != expected_hash:
            return False
        return True

    def ensure_extracted(self, root: Path, archive_path: Path, metadata: Dict[str, Any]) -> Path:
        dataset_version_id = metadata["id"]
        extracted_dir = self.extracted_dir(root, dataset_version_id)
        expected_marker = expected_extract_marker(dataset_version_id, archive_path, metadata)
        if self.extracted_matches(extracted_dir, archive_path, metadata):
            return extracted_dir

        tmp_parent = root / "tmp"
        tmp_parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=tmp_parent) as tmp_name:
            tmp_dir = Path(tmp_name)
            extract_archive_safely(archive_path, tmp_dir, metadata.get("archive_format"))
            marker_tmp = tmp_dir / ".cloudlink-extracted.json"
            marker_tmp.write_text(
                json.dumps(expected_marker, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if extracted_dir.exists():
                shutil.rmtree(extracted_dir)
            extracted_dir.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_dir), str(extracted_dir))
        return extracted_dir

    def extracted_matches(
        self,
        extracted_dir: Path,
        archive_path: Path,
        metadata: Dict[str, Any],
    ) -> bool:
        marker = extracted_dir / ".cloudlink-extracted.json"
        if not extracted_dir.exists() or not marker.exists():
            return False
        try:
            return json.loads(marker.read_text(encoding="utf-8")) == expected_extract_marker(
                metadata["id"],
                archive_path,
                metadata,
            )
        except (json.JSONDecodeError, OSError):
            return False

    def process_delete_requests(self) -> None:
        response = self.api_client.get_json(
            f"/api/worker/datasets/delete-requests"
            f"?worker_id={urllib.parse.quote(self.worker_id)}",
            timeout=self.api_timeout_seconds,
        )
        for item in response.get("requests", []):
            dataset_version_id = item["dataset_version_id"]
            for root_spec in self.all_roots():
                root = Path(root_spec["path"]).expanduser()
                archive_dir = root / "archives" / dataset_version_id
                extracted_dir = root / "extracted" / dataset_version_id
                if archive_dir.exists():
                    shutil.rmtree(archive_dir)
                if extracted_dir.exists():
                    shutil.rmtree(extracted_dir)
            self.report_cache(
                {"id": dataset_version_id},
                "deleted",
                local_archive_path=None,
                local_extracted_path=None,
                size_bytes=0,
                extracted_size_bytes=0,
            )

    def audit_known_caches(self) -> None:
        response = self.api_client.get_json(
            f"/api/worker/datasets/caches"
            f"?worker_id={urllib.parse.quote(self.worker_id)}",
            timeout=self.api_timeout_seconds,
            retries=0,
        )
        for item in response.get("caches", []):
            self.audit_one_cache(item)

    def audit_one_cache(self, item: Dict[str, Any]) -> None:
        dataset_version_id = item["dataset_version_id"]
        metadata = {
            "id": dataset_version_id,
            "size_bytes": item.get("expected_size_bytes") or item.get("size_bytes"),
            "checksum_sha256": item.get("expected_checksum_sha256")
            or item.get("checksum_sha256"),
            "extract_required": bool(item.get("extract_required")),
        }
        archive_path = Path(item["local_archive_path"]).expanduser() if item.get("local_archive_path") else None
        extracted_path = (
            Path(item["local_extracted_path"]).expanduser()
            if item.get("local_extracted_path")
            else None
        )
        data_root_path = item.get("data_root_path")
        size_bytes = 0
        extracted_size_bytes = 0
        checksum_sha256 = item.get("checksum_sha256")
        last_error = None
        status = "missing"

        if archive_path and archive_path.exists():
            archive_stat = archive_path.stat()
            size_bytes = archive_stat.st_size
            checksum_sha256 = cached_file_sha256(archive_path, archive_stat)
            if not self.local_file_matches(
                archive_path,
                metadata,
                actual_checksum=checksum_sha256,
            ):
                status = "invalid"
                last_error = "cached dataset checksum or size does not match"
            elif extracted_path and extracted_path.exists() and metadata["extract_required"]:
                if self.extracted_matches(extracted_path, archive_path, metadata):
                    status = "extracted"
                    extracted_size_bytes = directory_size(extracted_path)
                else:
                    status = "invalid"
                    last_error = "extracted dataset marker does not match archive"
            else:
                status = "cached"
        else:
            last_error = "cached dataset file is missing"

        self.report_cache(
            {"id": dataset_version_id},
            status,
            local_archive_path=str(archive_path) if archive_path else None,
            local_extracted_path=str(extracted_path) if extracted_path else None,
            size_bytes=size_bytes,
            extracted_size_bytes=extracted_size_bytes,
            checksum_sha256=checksum_sha256,
            data_root_path=data_root_path or self.root_for_path(archive_path or extracted_path),
            last_error=last_error,
        )

    def root_for_path(self, path: Optional[Path]) -> Optional[str]:
        if path is None:
            return None
        candidate = path.expanduser().resolve(strict=False)
        for root_spec in self.all_roots():
            root = Path(root_spec["path"]).expanduser().resolve(strict=False)
            if candidate == root or root in candidate.parents:
                return str(Path(root_spec["path"]).expanduser())
        return None

    def report_cache(
        self,
        metadata: Dict[str, Any],
        status: str,
        local_archive_path: Optional[str] = None,
        local_extracted_path: Optional[str] = None,
        size_bytes: int = 0,
        extracted_size_bytes: int = 0,
        checksum_sha256: Optional[str] = None,
        data_root_path: Optional[str] = None,
        last_error: Optional[str] = None,
    ) -> None:
        self.api_client.post_json(
            f"/api/worker/datasets/{metadata['id']}/cache",
            {
                "worker_id": self.worker_id,
                "status": status,
                "local_archive_path": local_archive_path,
                "local_extracted_path": local_extracted_path,
                "size_bytes": size_bytes,
                "extracted_size_bytes": extracted_size_bytes,
                "checksum_sha256": checksum_sha256,
                "data_root_path": data_root_path,
                "last_error": last_error,
            },
        )


def safe_filename(value: str) -> str:
    name = Path(value).name
    if not name or name in {".", ".."}:
        raise ValueError("filename is invalid")
    return name


def validate_download(path: Path, metadata: Dict[str, Any]) -> None:
    expected_size = metadata.get("size_bytes")
    if expected_size is not None and path.stat().st_size != int(expected_size):
        raise ValueError("downloaded dataset size does not match manifest")
    expected_hash = metadata.get("checksum_sha256")
    if expected_hash and file_sha256(path) != expected_hash:
        raise ValueError("downloaded dataset checksum does not match manifest")


def default_dataset_root() -> Path:
    return Path(
        os.getenv(
            "CLOUDLINK_DATASET_ROOT",
            str(Path(os.getenv("CLOUDLINK_HOME", "~/.cloudlink")).expanduser() / "datasets"),
        )
    ).expanduser()


def dataset_roots_from_env() -> List[Dict[str, str]]:
    raw = os.getenv("CLOUDLINK_DATASET_ROOTS", "").strip()
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return normalize_root_specs(parsed)
        except json.JSONDecodeError:
            if raw.startswith("[") or raw.startswith("{"):
                return [{"path": str(default_dataset_root()), "mode": "active"}]
        return normalize_root_specs(
            {"path": item, "mode": "active" if index == 0 else "readonly"}
            for index, item in enumerate(raw.split(os.pathsep))
            if item.strip()
        )
    return [{"path": str(default_dataset_root()), "mode": "active"}]


def normalize_root_specs(roots: Iterable[Dict[str, Any]]) -> List[Dict[str, str]]:
    normalized: List[Dict[str, str]] = []
    seen = set()
    active_seen = False
    for item in roots:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").strip()
        if not path or path in seen:
            continue
        mode = str(item.get("mode") or "active").strip().lower()
        if mode not in {"active", "readonly", "disabled"}:
            mode = "readonly"
        if mode == "active":
            if active_seen:
                mode = "readonly"
            else:
                active_seen = True
        root = {"path": str(Path(path).expanduser()), "mode": mode}
        label = str(item.get("label") or "").strip()
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


def expected_extract_marker(
    dataset_version_id: str,
    archive_path: Path,
    metadata: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "dataset_version_id": dataset_version_id,
        "checksum_sha256": metadata.get("checksum_sha256") or file_sha256(archive_path),
        "size_bytes": archive_path.stat().st_size,
    }


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cache_audit_marker_path(path: Path) -> Path:
    return path.with_name(f".{path.name}.cloudlink-audit.json")


def cached_file_sha256(path: Path, stat_result: Optional[os.stat_result] = None) -> str:
    stat = stat_result or path.stat()
    marker_path = cache_audit_marker_path(path)
    marker = read_cache_audit_marker(marker_path)
    if (
        marker.get("size_bytes") == stat.st_size
        and marker.get("mtime_ns") == stat.st_mtime_ns
        and marker.get("sha256")
    ):
        return str(marker["sha256"])

    checksum = file_sha256(path)
    write_cache_audit_marker(
        marker_path,
        {
            "size_bytes": stat.st_size,
            "mtime_ns": stat.st_mtime_ns,
            "sha256": checksum,
        },
    )
    return checksum


def read_cache_audit_marker(path: Path) -> Dict[str, Any]:
    try:
        marker = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return marker if isinstance(marker, dict) else {}


def write_cache_audit_marker(path: Path, value: Dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def directory_size(path: Path) -> int:
    return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())


def child_directory_count(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.iterdir() if item.is_dir())


def extract_archive_safely(
    archive_path: Path,
    target_dir: Path,
    archive_format: Optional[str],
) -> None:
    fmt = (archive_format or "").lower()
    if fmt == "zip" or archive_path.suffix.lower() == ".zip":
        with zipfile.ZipFile(archive_path) as zip_file:
            for member in zip_file.infolist():
                assert_safe_extract_path(target_dir, member.filename)
            zip_file.extractall(target_dir)
        return

    suffixes = "".join(archive_path.suffixes).lower()
    if fmt in {"tar", "tar.gz", "tgz"} or suffixes in {".tar", ".tar.gz", ".tgz"}:
        with tarfile.open(archive_path) as tar_file:
            for member in tar_file.getmembers():
                assert_safe_extract_path(target_dir, member.name)
                if not (member.isfile() or member.isdir()):
                    raise UnsafeArchiveError(f"unsafe archive member type: {member.name}")
            tar_file.extractall(target_dir)
        return

    raise ValueError("unsupported archive format")


def assert_safe_extract_path(target_dir: Path, member_name: str) -> None:
    target_root = target_dir.resolve()
    destination = (target_dir / member_name).resolve()
    if destination != target_root and target_root not in destination.parents:
        raise UnsafeArchiveError(f"unsafe archive path: {member_name}")
