import hashlib
import importlib
import json


def make_client(monkeypatch, tmp_path):
    source_root = tmp_path / "sources"
    result_root = tmp_path / "results"
    source_root.mkdir()
    result_root.mkdir()
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("CLOUDLINK_DATA_ROOT", str(tmp_path / "cloudlink-data"))
    monkeypatch.setenv("CLOUDLINK_TRANSFER_SOURCE_ROOTS", str(source_root))
    monkeypatch.setenv("CLOUDLINK_RESULT_DESTINATION_ROOTS", str(result_root))
    monkeypatch.setenv("CLOUDLINK_VERSION", "2026.08.04.1")
    monkeypatch.setenv("CLOUDLINK_MINIMUM_WORKER_VERSION", "2026.08.04.1")
    monkeypatch.setenv("CLOUDLINK_MINIMUM_GPU_WORKER_VERSION", "2026.08.04.1")
    monkeypatch.setenv("WORKER_SECRET", "worker-secret")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN", "codex-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-secret")
    monkeypatch.setenv("TASK_ALLOWED_TYPES", "script_job")
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_MIN_FREE_DISK_BYTES", "0")

    import app.artifact_store
    import app.config
    import app.database
    import app.task_store
    import app.transfer_store
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.transfer_store)
    importlib.reload(app.task_store)
    importlib.reload(app.artifact_store)
    importlib.reload(app.main)

    from fastapi.testclient import TestClient

    client = TestClient(app.main.app)
    client.source_root = source_root
    client.result_root = result_root
    return client


def internal_headers():
    return {"X-Internal-API-Secret": "internal-secret"}


def worker_headers():
    return {"Authorization": "Bearer worker-secret"}


def register_worker(client):
    response = client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json={
            "worker_id": "worker-a",
            "display_name": "Worker A",
            "supported_types": ["script_job"],
            "runtime_profile": {"cloudlink_version": "2026.08.04.1"},
            "enabled": True,
        },
    )
    assert response.status_code == 200


def create_transfer_task(client):
    source = client.source_root / "large-input.bin"
    source.write_bytes(b"direct input")
    destination = client.result_root / "task-output"
    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('ok')",
                "input_paths": [
                    {"path": "inputs/data.bin", "source_path": str(source)}
                ],
            },
            "result_path": str(destination),
        },
    )
    assert response.status_code == 200, response.text
    return response.json()["id"], source, destination


def claim(client):
    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "worker-a", "supported_types": ["script_job"]},
    )
    assert response.status_code == 200, response.text
    return response.json()["task"]


def test_path_input_is_streamed_and_result_is_published(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id, source, destination = create_transfer_task(client)
    task = claim(client)

    item = task["payload"]["input_paths"][0]
    assert "source_path" not in item
    downloaded = client.get(item["download_url"], headers=worker_headers())
    assert downloaded.status_code == 200
    assert downloaded.content == source.read_bytes()

    content = b"result payload"
    digest = hashlib.sha256(content).hexdigest()
    created = client.post(
        f"/api/worker/tasks/{task_id}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "relative_path": "reports/result.txt",
            "title": "Result",
            "description": "",
            "meaning": "",
            "content_type": "text/plain",
            "size_bytes": len(content),
            "sha256": digest,
            "required": True,
        },
    ).json()
    uploaded = client.put(
        f"/api/worker/tasks/{task_id}/artifacts/{created['id']}/content",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert uploaded.status_code == 200
    result = {"summary": "ok", "output_files": [{"artifact_id": created["id"]}]}
    result_hash = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    completed = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "result": result,
            "result_sha256": result_hash,
            "logs": "done",
        },
    )
    assert completed.status_code == 200, completed.text
    assert (destination / "reports" / "result.txt").read_bytes() == content
    record = json.loads((destination / "_cloudlink_task.json").read_text())
    assert record["status"] == "success"
    assert record["task_id"] == task_id

    release = client.post(
        f"/api/internal/tasks/{task_id}/release-input-cache",
        headers=internal_headers(),
        json={},
    )
    assert release.status_code == 200
    assert release.json()["requested"] == 1
    pending = client.get(
        "/api/worker/input-cache/release-requests?worker_id=worker-a",
        headers=worker_headers(),
    ).json()["requests"]
    assert pending[0]["cache_key"] == item["cache_key"]


def test_changed_input_is_rejected_at_download(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    _task_id, source, _destination = create_transfer_task(client)
    task = claim(client)
    source.write_bytes(b"changed after submission")

    response = client.get(
        task["payload"]["input_paths"][0]["download_url"],
        headers=worker_headers(),
    )

    assert response.status_code == 409
    assert "changed" in response.json()["detail"]


def test_failed_task_publishes_terminal_record(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id, _source, destination = create_transfer_task(client)
    task = claim(client)

    failed = client.post(
        f"/api/worker/tasks/{task_id}/failed",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": task["lease_id"],
            "error": "model fit failed",
            "error_code": "execution_failed",
            "logs": "traceback",
        },
    )

    assert failed.status_code == 200, failed.text
    record = json.loads((destination / "_cloudlink_task.json").read_text())
    assert record["status"] == "failed"
    assert record["error"] == "model fit failed"


def test_cache_release_waits_while_same_content_is_in_use(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    first_id, source, _destination = create_transfer_task(client)
    first = claim(client)
    failed = client.post(
        f"/api/worker/tasks/{first_id}/failed",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": first["lease_id"],
            "error": "retry with fixed code",
            "error_code": "execution_failed",
            "logs": "failed",
        },
    )
    assert failed.status_code == 200

    second_response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('fixed')",
                "input_paths": [
                    {"path": "inputs/data.bin", "source_path": str(source)}
                ],
            },
            "result_path": str(client.result_root / "task-output-fixed"),
        },
    )
    assert second_response.status_code == 200
    second_id = second_response.json()["id"]
    second = claim(client)
    assert second["id"] == second_id

    released = client.post(
        f"/api/internal/tasks/{first_id}/release-input-cache",
        headers=internal_headers(),
        json={},
    )
    assert released.status_code == 200
    pending_while_running = client.get(
        "/api/worker/input-cache/release-requests?worker_id=worker-a",
        headers=worker_headers(),
    ).json()["requests"]
    assert pending_while_running == []

    second_failed = client.post(
        f"/api/worker/tasks/{second_id}/failed",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": second["lease_id"],
            "error": "done",
            "error_code": "execution_failed",
            "logs": "done",
        },
    )
    assert second_failed.status_code == 200
    pending_after_completion = client.get(
        "/api/worker/input-cache/release-requests?worker_id=worker-a",
        headers=worker_headers(),
    ).json()["requests"]
    assert len(pending_after_completion) == 1


def test_paths_outside_allowlists_are_rejected(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    outside = tmp_path / "outside.bin"
    outside.write_bytes(b"no")

    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('no')",
                "input_paths": [{"path": "data.bin", "source_path": str(outside)}],
            },
            "result_path": str(tmp_path / "outside-results" / "task"),
        },
    )

    assert response.status_code == 409
