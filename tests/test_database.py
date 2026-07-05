import importlib
from concurrent.futures import ThreadPoolExecutor


def test_sqlite_connection_can_be_closed_from_dependency_cleanup_thread(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("WORKER_SECRET", "test-secret")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN", "codex-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")

    import app.config
    import app.database

    importlib.reload(app.config)
    importlib.reload(app.database)

    conn = app.database.connect()

    with ThreadPoolExecutor(max_workers=1) as executor:
        executor.submit(conn.close).result()
