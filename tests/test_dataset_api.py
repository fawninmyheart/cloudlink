import importlib
import zipfile
from pathlib import Path


def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("CLOUDLINK_DATA_ROOT", str(tmp_path / "cloudlink-data"))
    monkeypatch.setenv("WORKER_SECRET", "test-secret")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN", "codex-secret")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    monkeypatch.setenv("TASK_ALLOWED_TYPES", "echo_test,generate_daily_report,script_job")

    import app.config
    import app.database
    import app.dataset_store
    import app.task_store
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.dataset_store)
    importlib.reload(app.task_store)
    importlib.reload(app.main)

    from fastapi.testclient import TestClient

    return TestClient(app.main.app)


def worker_headers():
    return {"Authorization": "Bearer test-secret"}


def internal_headers():
    return {"X-Internal-API-Secret": "internal-secret"}


def codex_headers():
    return {"X-Cloudlink-Codex-Token": "codex-secret"}


def admin_auth():
    return ("admin", "admin-pass")


def register_worker(client):
    response = client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json={
            "worker_id": "local-worker-a",
            "display_name": "Mac mini",
            "supported_types": ["script_job"],
            "enabled": True,
        },
    )
    assert response.status_code == 200


def test_internal_registers_lists_and_deletes_symlink_dataset(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    source = tmp_path / "klines.csv"
    source.write_text("ts,close\n1,100\n", encoding="utf-8")

    create = client.post(
        "/api/internal/datasets",
        headers=internal_headers(),
        json={
            "name": "example-prices",
            "version": "2024-v1",
            "title": "Example Prices",
            "description": "test csv",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/csv",
            "manifest": {"schema": ["ts", "close"]},
        },
    )

    assert create.status_code == 200
    version = create.json()
    managed = Path(version["server_path"])
    assert managed.is_symlink()

    listed = client.get("/api/internal/datasets", headers=internal_headers())
    assert listed.status_code == 200
    assert listed.json()["datasets"][0]["id"] == version["id"]
    assert listed.json()["datasets"][0]["manifest"]["schema"] == ["ts", "close"]

    delete = client.delete(
        f"/api/internal/datasets/{version['id']}",
        headers=internal_headers(),
    )
    assert delete.status_code == 200
    assert source.exists()
    assert not managed.exists()


def test_codex_token_can_register_and_list_but_not_delete_dataset(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    source = tmp_path / "cloudlink-data" / "imports" / "klines.csv"
    source.parent.mkdir(parents=True)
    source.write_text("ts,close\n1,100\n", encoding="utf-8")

    create = client.post(
        "/api/internal/datasets",
        headers=codex_headers(),
        json={
            "name": "example-prices-1m",
            "version": "2026-v1",
            "title": "Example Prices 1m",
            "description": "codex csv",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/csv",
        },
    )
    assert create.status_code == 200
    version = create.json()

    listed = client.get("/api/internal/datasets", headers=codex_headers())
    assert listed.status_code == 200
    assert listed.json()["datasets"][0]["id"] == version["id"]

    delete = client.delete(
        f"/api/internal/datasets/{version['id']}",
        headers=codex_headers(),
    )
    assert delete.status_code == 401


def test_codex_token_cannot_register_dataset_outside_allowed_source_roots(
    monkeypatch,
    tmp_path,
):
    allowed_root = tmp_path / "allowed-imports"
    allowed_root.mkdir()
    monkeypatch.setenv("CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS", str(allowed_root))
    client = make_client(monkeypatch, tmp_path)
    source = tmp_path / "outside" / "secret.txt"
    source.parent.mkdir()
    source.write_text("do-not-expose\n", encoding="utf-8")

    create = client.post(
        "/api/internal/datasets",
        headers=codex_headers(),
        json={
            "name": "outside-secret",
            "version": "v1",
            "title": "Outside Secret",
            "description": "must be rejected for codex token",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/plain",
        },
    )

    assert create.status_code == 403
    assert "allowed dataset source roots" in create.json()["detail"]


def test_codex_token_can_register_dataset_inside_allowed_source_roots(
    monkeypatch,
    tmp_path,
):
    allowed_root = tmp_path / "allowed-imports"
    allowed_root.mkdir()
    monkeypatch.setenv("CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS", str(allowed_root))
    client = make_client(monkeypatch, tmp_path)
    source = allowed_root / "klines.csv"
    source.write_text("ts,close\n1,100\n", encoding="utf-8")

    create = client.post(
        "/api/internal/datasets",
        headers=codex_headers(),
        json={
            "name": "allowed-klines",
            "version": "v1",
            "title": "Allowed Klines",
            "description": "allowed codex import",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/csv",
        },
    )

    assert create.status_code == 200
    assert Path(create.json()["server_path"]).is_symlink()


def test_internal_secret_can_register_dataset_outside_codex_source_roots(
    monkeypatch,
    tmp_path,
):
    allowed_root = tmp_path / "allowed-imports"
    allowed_root.mkdir()
    monkeypatch.setenv("CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS", str(allowed_root))
    client = make_client(monkeypatch, tmp_path)
    source = tmp_path / "outside" / "admin.csv"
    source.parent.mkdir()
    source.write_text("ts,close\n1,100\n", encoding="utf-8")

    create = client.post(
        "/api/internal/datasets",
        headers=internal_headers(),
        json={
            "name": "admin-outside",
            "version": "v1",
            "title": "Admin Outside",
            "description": "internal admin import",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/csv",
        },
    )

    assert create.status_code == 200


def test_worker_can_fetch_metadata_download_and_report_cache(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    source = tmp_path / "klines.csv"
    source.write_text("ts,close\n1,100\n", encoding="utf-8")
    version = client.post(
        "/api/internal/datasets",
        headers=internal_headers(),
        json={
            "name": "example-prices",
            "version": "2024-v1",
            "title": "Example Prices",
            "description": "test csv",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/csv",
        },
    ).json()

    metadata = client.get(
        f"/api/worker/datasets/{version['id']}?worker_id=local-worker-a",
        headers=worker_headers(),
    )
    assert metadata.status_code == 200
    assert metadata.json()["id"] == version["id"]
    assert metadata.json()["download_url"].endswith(
        f"/api/worker/datasets/{version['id']}/download?worker_id=local-worker-a"
    )

    download = client.get(
        f"/api/worker/datasets/{version['id']}/download?worker_id=local-worker-a",
        headers=worker_headers(),
    )
    assert download.status_code == 200
    assert download.text == "ts,close\n1,100\n"

    report = client.post(
        f"/api/worker/datasets/{version['id']}/cache",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-a",
            "status": "cached",
            "local_archive_path": "/home/cloudlink-test/.cloudlink/datasets/archives/source.csv",
            "size_bytes": len(download.content),
        },
    )
    assert report.status_code == 200

    overview = client.get("/api/admin/overview", auth=admin_auth())
    assert overview.status_code == 200
    body = overview.json()
    assert body["datasets"][0]["id"] == version["id"]
    assert body["dataset_caches"][0]["worker_id"] == "local-worker-a"
    assert body["dataset_caches"][0]["status"] == "cached"


def test_admin_can_request_worker_cache_delete(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    archive = tmp_path / "klines.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("data.csv", "ts,close\n1,100\n")
    version = client.post(
        "/api/internal/datasets",
        headers=internal_headers(),
        json={
            "name": "example-archive",
            "version": "2024-v1",
            "title": "Example archive",
            "description": "test archive",
            "source_kind": "owned_archive",
            "source_path": str(archive),
            "content_type": "application/zip",
            "archive_format": "zip",
            "extract_required": True,
        },
    ).json()
    client.post(
        f"/api/worker/datasets/{version['id']}/cache",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-a",
            "status": "extracted",
            "local_archive_path": "/tmp/archive.zip",
            "local_extracted_path": "/tmp/extracted",
        },
    )

    requested = client.post(
        f"/api/admin/datasets/{version['id']}/worker-delete",
        auth=admin_auth(),
        json={"worker_id": "local-worker-a"},
    )
    assert requested.status_code == 200
    assert requested.json()["updated"] == 1

    delete_requests = client.get(
        "/api/worker/datasets/delete-requests?worker_id=local-worker-a",
        headers=worker_headers(),
    )
    assert delete_requests.status_code == 200
    assert delete_requests.json()["requests"][0]["dataset_version_id"] == version["id"]
