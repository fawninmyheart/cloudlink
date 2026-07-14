import importlib
import sqlite3
import zipfile
from pathlib import Path

import pytest


def make_conn(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("CLOUDLINK_DATA_ROOT", str(tmp_path / "cloudlink-data"))
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")

    import app.config
    import app.database
    import app.dataset_store

    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.dataset_store)

    app.database.init_db()
    conn = app.database.connect()
    return conn, app.dataset_store


def test_register_symlink_file_deletion_keeps_original(monkeypatch, tmp_path):
    conn, dataset_store = make_conn(monkeypatch, tmp_path)
    original = tmp_path / "source.csv"
    original.write_text("ts,close\n1,100\n", encoding="utf-8")

    version = dataset_store.register_dataset_version(
        conn,
        name="example-prices",
        version="2024-v1",
        title="Example Prices",
        description="test csv",
        source_kind="symlink_file",
        source_path=str(original),
        content_type="text/csv",
        manifest_extra={"schema": ["ts", "close"]},
        created_by="test",
    )

    managed = Path(version["server_path"])
    assert managed.is_symlink()
    assert managed.resolve() == original.resolve()
    assert version["original_path"] == str(original.resolve())
    assert version["manifest"]["schema"] == ["ts", "close"]

    dataset_store.delete_dataset_version(conn, version["id"])

    assert original.exists()
    assert not managed.exists()
    assert dataset_store.list_dataset_versions(conn) == []
    conn.close()


def test_register_owned_archive_moves_and_deletes_real_file(monkeypatch, tmp_path):
    conn, dataset_store = make_conn(monkeypatch, tmp_path)
    archive = tmp_path / "klines.zip"
    with zipfile.ZipFile(archive, "w") as zip_file:
        zip_file.writestr("data.csv", "ts,close\n1,100\n")

    version = dataset_store.register_dataset_version(
        conn,
        name="example-archive",
        version="2024-v1",
        title="Example archive",
        description="test archive",
        source_kind="owned_archive",
        source_path=str(archive),
        content_type="application/zip",
        archive_format="zip",
        extract_required=True,
        created_by="test",
    )

    managed = Path(version["server_path"])
    assert not archive.exists()
    assert managed.exists()
    assert managed.name == "klines.zip"
    assert version["checksum_sha256"]
    assert version["manifest"]["extract_required"] is True

    dataset_store.delete_dataset_version(conn, version["id"])

    assert not managed.exists()
    assert dataset_store.list_dataset_versions(conn) == []
    conn.close()


def test_register_owned_file_copies_and_deletes_managed_copy(monkeypatch, tmp_path):
    conn, dataset_store = make_conn(monkeypatch, tmp_path)
    original = tmp_path / "source.csv"
    original.write_text("ts,close\n1,100\n", encoding="utf-8")

    version = dataset_store.register_dataset_version(
        conn,
        name="example-owned-file",
        version="2024-v1",
        title="Example Owned File",
        description="test csv",
        source_kind="owned_file",
        source_path=str(original),
        content_type="text/csv",
        created_by="test",
    )

    managed = Path(version["server_path"])
    assert original.exists()
    assert managed.exists()
    assert not managed.is_symlink()
    assert managed.read_text(encoding="utf-8") == original.read_text(encoding="utf-8")
    assert version["checksum_sha256"]
    assert version["manifest"]["copied_from_source_path"] == str(original.resolve())

    dataset_store.delete_dataset_version(conn, version["id"])

    assert original.exists()
    assert not managed.exists()
    assert dataset_store.list_dataset_versions(conn) == []
    conn.close()


def test_delete_dataset_version_blocks_active_worker_cache(monkeypatch, tmp_path):
    conn, dataset_store = make_conn(monkeypatch, tmp_path)
    original = tmp_path / "source.csv"
    original.write_text("ts,close\n1,100\n", encoding="utf-8")
    version = dataset_store.register_dataset_version(
        conn,
        name="example-prices",
        version="2024-v1",
        title="Example Prices",
        description="test csv",
        source_kind="symlink_file",
        source_path=str(original),
        content_type="text/csv",
        created_by="test",
    )
    dataset_store.upsert_worker_cache(
        conn,
        worker_id="local-worker-a",
        dataset_version_id=version["id"],
        status="cached",
        local_archive_path="/home/cloudlink-test/.cloudlink/datasets/archives/source.csv",
    )

    with pytest.raises(dataset_store.DatasetConflict, match="worker cache"):
        dataset_store.delete_dataset_version(conn, version["id"])

    assert Path(version["server_path"]).exists()
    conn.close()


def test_deleted_worker_cache_clears_local_paths(monkeypatch, tmp_path):
    conn, dataset_store = make_conn(monkeypatch, tmp_path)
    original = tmp_path / "source.csv"
    original.write_text("ts,close\n1,100\n", encoding="utf-8")
    version = dataset_store.register_dataset_version(
        conn,
        name="example-prices",
        version="2024-v1",
        title="Example Prices",
        description="test csv",
        source_kind="symlink_file",
        source_path=str(original),
        content_type="text/csv",
        created_by="test",
    )
    dataset_store.upsert_worker_cache(
        conn,
        worker_id="local-worker-a",
        dataset_version_id=version["id"],
        status="cached",
        local_archive_path="/home/cloudlink-test/.cloudlink/datasets/archives/source.csv",
        size_bytes=100,
    )

    cache = dataset_store.upsert_worker_cache(
        conn,
        worker_id="local-worker-a",
        dataset_version_id=version["id"],
        status="deleted",
        size_bytes=0,
        extracted_size_bytes=0,
    )

    assert cache["status"] == "deleted"
    assert cache["local_archive_path"] is None
    assert cache["local_extracted_path"] is None
    assert cache["size_bytes"] == 0
    conn.close()
