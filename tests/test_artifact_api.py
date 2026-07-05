import hashlib
import importlib

TEST_CLOUDLINK_VERSION = "2026.07.05.9"
TEST_MINIMUM_WORKER_VERSION = "2026.07.05.2"
TEST_WORKER_VERSION = TEST_MINIMUM_WORKER_VERSION


def make_client(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("CLOUDLINK_DATA_ROOT", str(tmp_path / "data"))
    monkeypatch.setenv("CLOUDLINK_VERSION", TEST_CLOUDLINK_VERSION)
    monkeypatch.setenv("CLOUDLINK_MINIMUM_WORKER_VERSION", TEST_MINIMUM_WORKER_VERSION)
    monkeypatch.setenv("WORKER_SECRET", "test-secret")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN", "codex-secret")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    monkeypatch.setenv("TASK_ALLOWED_TYPES", "echo_test,generate_daily_report,script_job")
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_MIN_FREE_DISK_BYTES", "0")

    import app.config
    import app.database
    import app.task_store
    import app.artifact_store
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.task_store)
    importlib.reload(app.artifact_store)
    importlib.reload(app.main)

    from fastapi.testclient import TestClient

    client = TestClient(app.main.app)
    return client


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
            "worker_id": "worker-a",
            "display_name": "Worker A",
            "supported_types": ["script_job"],
            "enabled": True,
            "runtime_profile": {"cloudlink_version": TEST_WORKER_VERSION},
        },
    )
    assert response.status_code == 200


def claim_script_task(client):
    create = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "script_job", "payload": {"script": "print('ok')"}},
    )
    task_id = create.json()["id"]
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "worker-a", "supported_types": ["script_job"]},
    )
    task = claim.json()["task"]
    assert task["id"] == task_id
    return task


def claim_codex_script_task(client):
    create = client.post(
        "/api/internal/tasks",
        headers=codex_headers(),
        json={"type": "script_job", "payload": {"script": "print('ok')"}},
    )
    task_id = create.json()["id"]
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "worker-a", "supported_types": ["script_job"]},
    )
    task = claim.json()["task"]
    assert task["id"] == task_id
    return task


def test_worker_uploads_artifact_for_owned_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task = claim_script_task(client)
    content = b"alpha,beta\n1,2\n"
    digest = hashlib.sha256(content).hexdigest()

    create = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "relative_path": "result.csv",
            "title": "Result CSV",
            "description": "Detailed rows.",
            "meaning": "Use this for follow-up analysis.",
            "content_type": "text/csv",
            "size_bytes": len(content),
            "sha256": digest,
            "required": True,
        },
    )
    assert create.status_code == 200
    artifact = create.json()

    upload = client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/content",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert upload.status_code == 200
    assert upload.json()["status"] == "uploaded"

    retry_upload = client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/content",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert retry_upload.status_code == 200
    assert retry_upload.json()["status"] == "uploaded"


def test_worker_uploads_artifact_in_resumable_chunks(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task = claim_script_task(client)
    content = b"abcdefghij"
    digest = hashlib.sha256(content).hexdigest()

    create = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "relative_path": "large.bin",
            "title": "Large binary",
            "description": "Chunked upload fixture.",
            "meaning": "Verifies resumable uploads.",
            "content_type": "application/octet-stream",
            "size_bytes": len(content),
            "sha256": digest,
            "required": True,
        },
    )
    assert create.status_code == 200
    artifact = create.json()

    status = client.get(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/upload-status",
        headers=worker_headers(),
    )
    assert status.status_code == 200
    assert status.json()["uploaded_bytes"] == 0

    first = client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/chunks/0",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content[:4],
    )
    assert first.status_code == 200
    assert first.json()["uploaded_bytes"] == 4
    assert first.json()["status"] == "uploading"

    duplicate = client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/chunks/0",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content[:4],
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["uploaded_bytes"] == 4

    wrong_offset = client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/chunks/0",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=b"zzzz",
    )
    assert wrong_offset.status_code == 409

    second = client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/chunks/4",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content[4:],
    )
    assert second.status_code == 200
    assert second.json()["uploaded_bytes"] == len(content)

    complete = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/complete",
        headers=worker_headers(),
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "uploaded"

    retry_complete = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/complete",
        headers=worker_headers(),
    )
    assert retry_complete.status_code == 200
    assert retry_complete.json()["status"] == "uploaded"

    download = client.get(
        f"/api/internal/tasks/{task['id']}/artifacts/{artifact['id']}/download",
        headers=internal_headers(),
    )
    assert download.status_code == 200
    assert download.content == content


def test_worker_completes_empty_artifact_upload(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task = claim_script_task(client)
    digest = hashlib.sha256(b"").hexdigest()

    create = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "relative_path": "empty.txt",
            "size_bytes": 0,
            "sha256": digest,
        },
    )
    assert create.status_code == 200
    artifact = create.json()

    complete = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/complete",
        headers=worker_headers(),
    )
    assert complete.status_code == 200
    assert complete.json()["status"] == "uploaded"

    download = client.get(
        f"/api/internal/tasks/{task['id']}/artifacts/{artifact['id']}/download",
        headers=internal_headers(),
    )
    assert download.status_code == 200
    assert download.content == b""


def test_worker_cannot_upload_artifact_for_wrong_lease(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task = claim_script_task(client)

    response = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": "wrong-lease",
            "relative_path": "result.csv",
            "size_bytes": 1,
            "sha256": hashlib.sha256(b"x").hexdigest(),
        },
    )

    assert response.status_code == 409


def upload_sample_artifact(client, *, codex_owned=False):
    register_worker(client)
    task = claim_codex_script_task(client) if codex_owned else claim_script_task(client)
    content = b"alpha,beta\n1,2\n"
    digest = hashlib.sha256(content).hexdigest()
    artifact = client.post(
        f"/api/worker/tasks/{task['id']}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "relative_path": "result.csv",
            "title": "Result CSV",
            "description": "Detailed rows.",
            "meaning": "Use this for follow-up analysis.",
            "content_type": "text/csv",
            "size_bytes": len(content),
            "sha256": digest,
            "required": True,
        },
    ).json()
    client.put(
        f"/api/worker/tasks/{task['id']}/artifacts/{artifact['id']}/content",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content,
    )
    return task, artifact, content


def test_codex_token_lists_and_downloads_uploaded_artifact(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    task, artifact, content = upload_sample_artifact(client, codex_owned=True)

    listed = client.get(
        f"/api/internal/tasks/{task['id']}/artifacts",
        headers=codex_headers(),
    )
    assert listed.status_code == 200
    assert listed.json()["artifacts"][0]["id"] == artifact["id"]

    download = client.get(
        f"/api/internal/tasks/{task['id']}/artifacts/{artifact['id']}/download",
        headers=codex_headers(),
    )
    assert download.status_code == 200
    assert download.content == content


def test_admin_downloads_uploaded_artifact(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    task, artifact, content = upload_sample_artifact(client)

    download = client.get(
        f"/api/admin/tasks/{task['id']}/artifacts/{artifact['id']}/download",
        auth=admin_auth(),
    )
    assert download.status_code == 200
    assert download.content == content
