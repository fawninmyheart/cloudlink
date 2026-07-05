import importlib


def reload_modules(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("CLOUDLINK_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_MAX_FILE_BYTES", "1024")
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_MAX_TASK_BYTES", "2048")
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_MIN_FREE_DISK_BYTES", "0")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")

    import app.config
    import app.database
    import app.task_store
    import app.artifact_store

    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.task_store)
    importlib.reload(app.artifact_store)
    app.database.init_db()
    return app.database, app.task_store, app.artifact_store


def test_create_artifact_record_uses_safe_storage_path(monkeypatch, tmp_path):
    database, task_store, artifact_store = reload_modules(monkeypatch, tmp_path)
    with database.connect() as conn:
        task = task_store.create_task(conn, "script_job", {"script": "print('ok')"})
        artifact = artifact_store.create_artifact_record(
            conn,
            task_id=task["id"],
            worker_id="worker-a",
            lease_id="lease-a",
            relative_path="reports/result.csv",
            title="Result CSV",
            description="Detailed result rows.",
            meaning="Use for downstream filtering.",
            content_type="text/csv",
            size_bytes=12,
            sha256="abc",
            required=True,
        )

    assert artifact["relative_path"] == "reports/result.csv"
    assert artifact["status"] == "created"
    assert artifact["storage_path"].startswith(str(tmp_path / "data" / "artifacts"))
    assert ".." not in artifact["storage_path"]


def test_rejects_unsafe_artifact_relative_path(monkeypatch, tmp_path):
    database, task_store, artifact_store = reload_modules(monkeypatch, tmp_path)
    with database.connect() as conn:
        task = task_store.create_task(conn, "script_job", {"script": "print('ok')"})
        try:
            artifact_store.create_artifact_record(
                conn,
                task_id=task["id"],
                worker_id="worker-a",
                lease_id="lease-a",
                relative_path="../secret.txt",
                title="Bad",
                description="Bad",
                meaning="Bad",
                content_type="text/plain",
                size_bytes=1,
                sha256="abc",
                required=True,
            )
        except artifact_store.ArtifactConflict as exc:
            assert "relative_path" in str(exc)
        else:
            raise AssertionError("unsafe relative path accepted")
