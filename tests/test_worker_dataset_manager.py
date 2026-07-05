import json
import tarfile
import zipfile
from pathlib import Path

import pytest

from worker.dataset_manager import (
    DatasetManager,
    UnsafeArchiveError,
    dataset_roots_from_env,
    file_sha256,
)


class FakeApiClient:
    def __init__(self, metadata, payload):
        self.metadata = metadata
        self.payload = payload
        self.reports = []
        self.downloads = []
        self.cache_rows = []

    def get_json(self, path, **_kwargs):
        if path == f"/api/worker/datasets/{self.metadata['id']}?worker_id=local-worker-a":
            return self.metadata
        if path == "/api/worker/datasets/caches?worker_id=local-worker-a":
            return {"caches": self.cache_rows}
        raise AssertionError(path)

    def download_to_path(self, path, target, **_kwargs):
        assert path == self.metadata["download_url"]
        self.downloads.append(path)
        target.write_bytes(self.payload)

    def post_json(self, path, body, **_kwargs):
        self.reports.append((path, body))
        return {"status": "ok"}


def make_manager(tmp_path, metadata, payload):
    api_client = FakeApiClient(metadata, payload)
    manager = DatasetManager(api_client, "local-worker-a")
    manager.root = tmp_path / "datasets"
    manager.set_roots([{"path": str(manager.root), "mode": "active"}])
    return manager, api_client


def test_dataset_manager_downloads_extracts_and_reuses_archive(tmp_path, monkeypatch):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("data.csv", "ts,close\n1,100\n")
    payload = archive.read_bytes()
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "example-prices",
        "version": "2024-v1",
        "source_kind": "owned_archive",
        "filename": "source.zip",
        "size_bytes": len(payload),
        "checksum_sha256": None,
        "archive_format": "zip",
        "extract_required": True,
        "manifest": {"schema": ["ts", "close"]},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, api_client = make_manager(tmp_path, metadata, payload)

    resolved = manager.ensure_datasets(
        [{"dataset_version_id": "dataset-version-1", "mount_name": "klines"}]
    )

    assert resolved.env == {
        "CLOUDLINK_DATASET_KLINES": str(
            tmp_path / "datasets" / "extracted" / "dataset-version-1"
        )
    }
    assert (Path(resolved.env["CLOUDLINK_DATASET_KLINES"]) / "data.csv").read_text(
        encoding="utf-8"
    ) == "ts,close\n1,100\n"
    assert resolved.records[0]["mount_name"] == "klines"
    assert resolved.records[0]["path"] == resolved.env["CLOUDLINK_DATASET_KLINES"]
    assert api_client.reports[-1][1]["status"] == "extracted"

    report_count = len(api_client.reports)
    manager.ensure_datasets(
        [{"dataset_version_id": "dataset-version-1", "mount_name": "klines"}]
    )
    assert len(api_client.reports) == report_count + 1
    assert api_client.reports[-1][1]["status"] == "extracted"


def test_dataset_manager_reuses_valid_readonly_root(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("data.csv", "ts,close\n1,100\n")
    payload = archive.read_bytes()
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "example-prices",
        "version": "2024-v1",
        "source_kind": "owned_archive",
        "filename": "source.zip",
        "size_bytes": len(payload),
        "checksum_sha256": file_sha256(archive),
        "archive_format": "zip",
        "extract_required": True,
        "manifest": {"schema": ["ts", "close"]},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, api_client = make_manager(tmp_path, metadata, payload)
    old_root = tmp_path / "old-datasets"
    new_root = tmp_path / "new-datasets"
    old_archive = old_root / "archives" / "dataset-version-1" / "source.zip"
    old_archive.parent.mkdir(parents=True)
    old_archive.write_bytes(payload)
    old_extracted = old_root / "extracted" / "dataset-version-1"
    old_extracted.mkdir(parents=True)
    (old_extracted / "data.csv").write_text("ts,close\n1,100\n", encoding="utf-8")
    (old_extracted / ".cloudlink-extracted.json").write_text(
        json.dumps(
            {
                "dataset_version_id": "dataset-version-1",
                "checksum_sha256": metadata["checksum_sha256"],
                "size_bytes": len(payload),
            }
        ),
        encoding="utf-8",
    )
    manager.set_roots(
        [
            {"path": str(old_root), "mode": "readonly", "label": "旧数据盘"},
            {"path": str(new_root), "mode": "active", "label": "FastData"},
        ]
    )

    resolved = manager.ensure_datasets(
        [{"dataset_version_id": "dataset-version-1", "mount_name": "klines"}]
    )

    assert resolved.env["CLOUDLINK_DATASET_KLINES"] == str(old_extracted)
    assert api_client.downloads == []
    assert api_client.reports[-1][1]["data_root_path"] == str(old_root)


def test_dataset_manager_downloads_to_active_root_when_old_hash_is_invalid(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("data.csv", "ts,close\n1,100\n")
    payload = archive.read_bytes()
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "example-prices",
        "version": "2024-v1",
        "source_kind": "owned_archive",
        "filename": "source.zip",
        "size_bytes": len(payload),
        "checksum_sha256": file_sha256(archive),
        "archive_format": "zip",
        "extract_required": False,
        "manifest": {"schema": ["ts", "close"]},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, api_client = make_manager(tmp_path, metadata, payload)
    old_root = tmp_path / "old-datasets"
    new_root = tmp_path / "new-datasets"
    old_archive = old_root / "archives" / "dataset-version-1" / "source.zip"
    old_archive.parent.mkdir(parents=True)
    old_archive.write_text("wrong", encoding="utf-8")
    manager.set_roots(
        [
            {"path": str(old_root), "mode": "readonly"},
            {"path": str(new_root), "mode": "active"},
        ]
    )

    resolved = manager.ensure_datasets(
        [{"dataset_version_id": "dataset-version-1", "mount_name": "klines"}]
    )

    expected_path = new_root / "archives" / "dataset-version-1" / "source.zip"
    assert resolved.env["CLOUDLINK_DATASET_KLINES"] == str(expected_path)
    assert expected_path.read_bytes() == payload
    assert api_client.downloads == [metadata["download_url"]]
    assert api_client.reports[-1][1]["data_root_path"] == str(new_root)


def test_dataset_manager_audits_known_cache_hashes(tmp_path):
    archive = tmp_path / "source.zip"
    archive.write_text("expected", encoding="utf-8")
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "example-prices",
        "version": "2024-v1",
        "source_kind": "owned_archive",
        "filename": "source.zip",
        "size_bytes": archive.stat().st_size,
        "checksum_sha256": file_sha256(archive),
        "archive_format": None,
        "extract_required": False,
        "manifest": {},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, api_client = make_manager(tmp_path, metadata, archive.read_bytes())
    cache_path = manager.root / "archives" / "dataset-version-1" / "source.zip"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(archive.read_bytes())
    api_client.cache_rows = [
        {
            "dataset_version_id": "dataset-version-1",
            "status": "cached",
            "local_archive_path": str(cache_path),
            "local_extracted_path": None,
            "size_bytes": archive.stat().st_size,
            "extracted_size_bytes": 0,
            "checksum_sha256": metadata["checksum_sha256"],
            "expected_checksum_sha256": metadata["checksum_sha256"],
            "expected_size_bytes": archive.stat().st_size,
            "extract_required": False,
        }
    ]

    manager.audit_known_caches()

    assert api_client.reports[-1][1]["status"] == "cached"
    cache_path.write_text("changed", encoding="utf-8")

    manager.audit_known_caches()

    assert api_client.reports[-1][1]["status"] == "invalid"


def test_dataset_manager_audit_skips_hash_when_size_and_mtime_are_unchanged(
    tmp_path,
    monkeypatch,
):
    archive = tmp_path / "source.zip"
    archive.write_text("expected", encoding="utf-8")
    checksum = file_sha256(archive)
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "example-prices",
        "version": "2024-v1",
        "source_kind": "owned_archive",
        "filename": "source.zip",
        "size_bytes": archive.stat().st_size,
        "checksum_sha256": checksum,
        "archive_format": None,
        "extract_required": False,
        "manifest": {},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, api_client = make_manager(tmp_path, metadata, archive.read_bytes())
    cache_path = manager.root / "archives" / "dataset-version-1" / "source.zip"
    cache_path.parent.mkdir(parents=True)
    cache_path.write_bytes(archive.read_bytes())
    api_client.cache_rows = [
        {
            "dataset_version_id": "dataset-version-1",
            "status": "cached",
            "local_archive_path": str(cache_path),
            "local_extracted_path": None,
            "size_bytes": archive.stat().st_size,
            "extracted_size_bytes": 0,
            "checksum_sha256": checksum,
            "expected_checksum_sha256": checksum,
            "expected_size_bytes": archive.stat().st_size,
            "extract_required": False,
        }
    ]
    hash_calls = []

    def counting_sha256(path):
        hash_calls.append(Path(path))
        return file_sha256(Path(path))

    monkeypatch.setattr("worker.dataset_manager.file_sha256", counting_sha256)

    manager.audit_known_caches()
    manager.audit_known_caches()

    assert hash_calls == [cache_path]
    assert api_client.reports[-1][1]["status"] == "cached"


def test_dataset_manager_validates_dataset_roots(tmp_path):
    metadata = {
        "id": "dataset-version-1",
        "download_url": "/unused",
        "filename": "source",
    }
    manager, _api_client = make_manager(tmp_path, metadata, b"")
    valid_root = tmp_path / "valid-root"
    invalid_root = tmp_path / "not-a-directory"
    invalid_root.write_text("file blocks mkdir", encoding="utf-8")
    manager.set_roots(
        [
            {"path": str(valid_root), "mode": "active", "label": "Fast"},
            {"path": str(invalid_root), "mode": "readonly", "label": "Broken"},
        ]
    )

    checks = manager.validate_roots()

    assert checks[0]["path"] == str(valid_root)
    assert checks[0]["status"] == "ok"
    assert checks[0]["readable"] is True
    assert checks[0]["writable"] is True
    assert checks[0]["free_bytes"] > 0
    assert checks[1]["path"] == str(invalid_root)
    assert checks[1]["status"] == "failed"
    assert checks[1]["readable"] is False
    assert "not a directory" in checks[1]["error"].lower() or "file exists" in checks[1]["error"].lower()


def test_dataset_manager_refuses_download_when_active_root_is_invalid(tmp_path):
    archive = tmp_path / "source.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("data.csv", "ts,close\n1,100\n")
    payload = archive.read_bytes()
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "example-prices",
        "version": "2024-v1",
        "source_kind": "owned_archive",
        "filename": "source.zip",
        "size_bytes": len(payload),
        "checksum_sha256": file_sha256(archive),
        "archive_format": "zip",
        "extract_required": False,
        "manifest": {"schema": ["ts", "close"]},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, api_client = make_manager(tmp_path, metadata, payload)
    invalid_active = tmp_path / "active-file"
    invalid_active.write_text("file blocks mkdir", encoding="utf-8")
    manager.set_roots([{"path": str(invalid_active), "mode": "active"}])

    with pytest.raises(ValueError, match="No writable active dataset root"):
        manager.ensure_datasets(
            [{"dataset_version_id": "dataset-version-1", "mount_name": "klines"}]
        )

    assert api_client.downloads == []


def test_dataset_roots_from_malformed_json_falls_back_to_default(tmp_path, monkeypatch):
    default_root = tmp_path / "default-datasets"
    monkeypatch.setenv("CLOUDLINK_DATASET_ROOT", str(default_root))
    monkeypatch.setenv(
        "CLOUDLINK_DATASET_ROOTS",
        "[{path:/home/cloudlink-test/.cloudlink/datasets,mode:active}]",
    )

    roots = dataset_roots_from_env()

    assert roots == [{"path": str(default_root), "mode": "active"}]
    assert not (Path.cwd() / "[{path").exists()


def test_dataset_manager_rejects_zip_path_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("../evil.txt", "bad")
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "bad",
        "version": "v1",
        "source_kind": "owned_archive",
        "filename": "bad.zip",
        "size_bytes": archive.stat().st_size,
        "checksum_sha256": None,
        "archive_format": "zip",
        "extract_required": True,
        "manifest": {},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, _api_client = make_manager(tmp_path, metadata, archive.read_bytes())

    with pytest.raises(UnsafeArchiveError):
        manager.ensure_datasets(
            [{"dataset_version_id": "dataset-version-1", "mount_name": "bad"}]
        )


def test_dataset_manager_rejects_tar_links(tmp_path):
    archive = tmp_path / "bad.tar"
    with tarfile.open(archive, "w") as tar_file:
        link = tarfile.TarInfo("data/passwd-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar_file.addfile(link)
    metadata = {
        "id": "dataset-version-1",
        "dataset_name": "bad",
        "version": "v1",
        "source_kind": "owned_archive",
        "filename": "bad.tar",
        "size_bytes": archive.stat().st_size,
        "checksum_sha256": None,
        "archive_format": "tar",
        "extract_required": True,
        "manifest": {},
        "download_url": "/api/worker/datasets/dataset-version-1/download?worker_id=local-worker-a",
    }
    manager, _api_client = make_manager(tmp_path, metadata, archive.read_bytes())

    with pytest.raises(UnsafeArchiveError):
        manager.ensure_datasets(
            [{"dataset_version_id": "dataset-version-1", "mount_name": "bad"}]
        )


def test_script_job_receives_dataset_env_and_manifest(monkeypatch, tmp_path):
    from worker.script_runner import run_script_job

    dataset_path = tmp_path / "datasets" / "extracted" / "dataset-version-1"
    dataset_path.mkdir(parents=True)
    (dataset_path / "data.csv").write_text("ts,close\n1,100\n", encoding="utf-8")
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: __import__("sys").executable,
    )

    result, _logs = run_script_job(
        {
            "script": (
                "import json, os\n"
                "from pathlib import Path\n"
                "dataset = Path(os.environ['CLOUDLINK_DATASET_KLINES'])\n"
                "print(dataset.joinpath('data.csv').read_text(encoding='utf-8').strip())\n"
                "print(json.loads(Path('datasets.json').read_text())[0]['mount_name'])\n"
            )
        },
        "local-worker-a",
        task_id="task-dataset",
        dataset_env={"CLOUDLINK_DATASET_KLINES": str(dataset_path)},
        dataset_records=[{"mount_name": "klines", "path": str(dataset_path)}],
    )

    assert result["exit_code"] == 0
    assert "ts,close" in result["stdout"]
    assert "klines" in result["stdout"]
    manifest = tmp_path / "jobs" / "task-dataset" / "datasets.json"
    assert json.loads(manifest.read_text(encoding="utf-8")) == [
        {"mount_name": "klines", "path": str(dataset_path)}
    ]
