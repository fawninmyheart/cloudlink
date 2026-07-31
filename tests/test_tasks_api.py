import importlib
import hashlib
import json
import sqlite3

TEST_CLOUDLINK_VERSION = "2026.07.05.9"
TEST_MINIMUM_WORKER_VERSION = "2026.07.05.2"
TEST_WORKER_VERSION = TEST_MINIMUM_WORKER_VERSION


def make_client(monkeypatch, tmp_path, *, minimum_gpu_worker_version=None):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("CLOUDLINK_VERSION", TEST_CLOUDLINK_VERSION)
    monkeypatch.setenv("CLOUDLINK_MINIMUM_WORKER_VERSION", TEST_MINIMUM_WORKER_VERSION)
    monkeypatch.setenv(
        "CLOUDLINK_MINIMUM_GPU_WORKER_VERSION",
        minimum_gpu_worker_version or TEST_MINIMUM_WORKER_VERSION,
    )
    monkeypatch.setenv("WORKER_SECRET", "test-secret")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN", "codex-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_SUBMITTER_ID", "codex-a")
    monkeypatch.setenv("ADMIN_USERNAME", "admin")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    monkeypatch.setenv("TASK_LOCK_SECONDS", "1800")
    monkeypatch.setenv(
        "TASK_ALLOWED_TYPES",
        "echo_test,generate_daily_report,script_job",
    )
    monkeypatch.setenv("TASK_MAX_RETRIES", "1")
    import app.config
    import app.database
    import app.task_store
    import app.main

    importlib.reload(app.config)
    importlib.reload(app.database)
    importlib.reload(app.task_store)
    importlib.reload(app.main)

    from fastapi.testclient import TestClient

    client = TestClient(app.main.app)
    client.db_path = str(tmp_path / "tasks.db")
    return client


def worker_headers():
    return {"Authorization": "Bearer test-secret"}


def internal_headers():
    return {"X-Internal-API-Secret": "internal-secret"}


def codex_headers():
    return {"X-Cloudlink-Codex-Token": "codex-secret"}


def codex_b_headers():
    return {"X-Cloudlink-Codex-Token": "codex-b-secret"}


def admin_auth():
    return ("admin", "admin-pass")


def register_worker(
    client,
    worker_id="local-worker-1",
    supported_types=None,
    runtime_profile=None,
):
    response = client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json={
            "worker_id": worker_id,
            "display_name": worker_id,
            "supported_types": supported_types or ["echo_test", "generate_daily_report"],
            "enabled": True,
            "runtime_profile": (
                {"cloudlink_version": TEST_WORKER_VERSION}
                if runtime_profile is None
                else runtime_profile
            ),
        },
    )
    assert response.status_code == 200
    return response.json()


def expire_task_lock(client, task_id, retry_count=0):
    with sqlite3.connect(client.db_path) as conn:
        conn.execute(
            """
            UPDATE tasks
            SET locked_until = '2000-01-01T00:00:00+00:00',
                retry_count = ?
            WHERE id = ?
            """,
            (retry_count, task_id),
        )


def create_echo_task(client, message="hello"):
    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "echo_test", "payload": {"message": message}},
    )
    assert response.status_code == 200
    return response.json()["id"]


def create_script_task(client, script="print('ok')"):
    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "script_job", "payload": {"script": script}},
    )
    assert response.status_code == 200
    return response.json()["id"]


def test_create_and_query_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    task_id = create_echo_task(client)

    response = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers())
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task_id
    assert body["type"] == "echo_test"
    assert body["status"] == "pending"
    assert body["payload"] == {"message": "hello"}
    assert body["result"] is None
    assert body["created_at"]


def test_create_task_preserves_title_and_description(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "title": "Example event efficiency",
            "description": "Measure event efficiency and produce downloadable CSV artifacts.",
            "payload": {"script": "print('ok')"},
        },
    )

    assert response.status_code == 200
    task_id = response.json()["id"]
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["title"] == "Example event efficiency"
    assert task["description"] == "Measure event efficiency and produce downloadable CSV artifacts."


def test_codex_token_can_create_and_query_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    create = client.post(
        "/api/internal/tasks",
        headers=codex_headers(),
        json={"type": "echo_test", "payload": {"message": "from codex"}},
    )
    assert create.status_code == 200

    task_id = create.json()["id"]
    query = client.get(f"/api/internal/tasks/{task_id}", headers=codex_headers())

    assert query.status_code == 200
    assert query.json()["payload"] == {"message": "from codex"}
    assert query.json()["submitter_id"] == "codex-a"


def test_codex_tokens_only_see_their_own_tasks(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CLOUDLINK_CODEX_TOKENS",
        json.dumps({"codex-a": "codex-secret", "codex-b": "codex-b-secret"}),
    )
    client = make_client(monkeypatch, tmp_path)

    task_a = client.post(
        "/api/internal/tasks",
        headers=codex_headers(),
        json={
            "type": "echo_test",
            "payload": {"message": "from a", "task_context": {"group_id": "ga"}},
        },
    ).json()["id"]
    task_b = client.post(
        "/api/internal/tasks",
        headers=codex_b_headers(),
        json={
            "type": "echo_test",
            "payload": {"message": "from b", "task_context": {"group_id": "gb"}},
        },
    ).json()["id"]

    own_list = client.get("/api/internal/tasks", headers=codex_headers())

    assert own_list.status_code == 200
    assert [task["id"] for task in own_list.json()["tasks"]] == [task_a]
    assert own_list.json()["resource_status"]["pending_count"] == 2

    other_detail = client.get(
        f"/api/internal/tasks/{task_b}",
        headers=codex_headers(),
    )
    assert other_detail.status_code == 404

    internal_list = client.get("/api/internal/tasks", headers=internal_headers())
    assert {task["id"] for task in internal_list.json()["tasks"]} == {task_a, task_b}


def test_codex_token_can_cancel_only_own_pending_task(monkeypatch, tmp_path):
    monkeypatch.setenv(
        "CLOUDLINK_CODEX_TOKENS",
        json.dumps({"codex-a": "codex-secret", "codex-b": "codex-b-secret"}),
    )
    client = make_client(monkeypatch, tmp_path)

    task_a = client.post(
        "/api/internal/tasks",
        headers=codex_headers(),
        json={"type": "echo_test", "payload": {"message": "from a"}},
    ).json()["id"]
    task_b = client.post(
        "/api/internal/tasks",
        headers=codex_b_headers(),
        json={"type": "echo_test", "payload": {"message": "from b"}},
    ).json()["id"]

    other_cancel = client.post(
        f"/api/internal/tasks/{task_b}/cancel",
        headers=codex_headers(),
        json={"reason": "not mine"},
    )
    assert other_cancel.status_code == 404

    own_cancel = client.post(
        f"/api/internal/tasks/{task_a}/cancel",
        headers=codex_headers(),
        json={"reason": "superseded by smaller batch"},
    )
    assert own_cancel.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_a}", headers=codex_headers()).json()
    assert task["status"] == "cancelled"
    assert task["error_code"] == "cancelled"


def test_create_task_rejects_when_max_pending_is_reached(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_MAX_PENDING_TASKS", "2")
    client = make_client(monkeypatch, tmp_path)
    create_echo_task(client, "one")
    create_echo_task(client, "two")

    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "echo_test", "payload": {"message": "three"}},
    )

    assert response.status_code == 429
    detail = response.json()["detail"]
    assert detail["code"] == "max_pending_exceeded"
    assert detail["pending_count"] == 2
    assert detail["max_pending"] == 2


def test_queue_status_reports_facts_without_task_details(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_MAX_PENDING_TASKS", "3")
    client = make_client(monkeypatch, tmp_path)
    create_echo_task(client, "one")

    response = client.get("/api/internal/queue/status", headers=codex_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["pending_count"] == 1
    assert body["max_pending"] == 3
    assert body["queue_timeout_seconds"] == 21600
    assert "tasks" not in body


def test_pending_task_expires_after_queue_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_QUEUE_TIMEOUT_SECONDS", "21600")
    client = make_client(monkeypatch, tmp_path)
    task_id = create_echo_task(client, "old")
    with sqlite3.connect(client.db_path) as conn:
        conn.execute(
            """
            UPDATE tasks
            SET created_at = '2000-01-01T00:00:00+00:00',
                updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (task_id,),
        )

    response = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "timeout"
    assert body["error_code"] == "queue_timeout"
    assert "Queue timeout" in body["error"]


def test_internal_api_rejects_public_forwarded_requests(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/tasks",
        headers={
            **internal_headers(),
            "Host": "tasks.example.test",
            "X-Forwarded-For": "203.0.113.10",
        },
        json={"type": "echo_test", "payload": {"message": "public"}},
    )

    assert response.status_code == 403
    assert "local" in response.json()["detail"].lower()


def test_codex_token_rejects_public_host_even_without_forward_header(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/tasks",
        headers={**codex_headers(), "Host": "tasks.example.test"},
        json={"type": "echo_test", "payload": {"message": "public"}},
    )

    assert response.status_code == 403
    assert "local" in response.json()["detail"].lower()


def test_codex_token_cannot_register_workers(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/workers",
        headers=codex_headers(),
        json={
            "worker_id": "unknown-worker",
            "display_name": "unknown-worker",
            "supported_types": ["script_job"],
            "enabled": True,
        },
    )

    assert response.status_code == 401


def test_codex_token_can_query_internal_status(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["script_job"])

    response = client.get("/api/internal/status", headers=codex_headers())

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["total"] == 0
    assert body["workers"][0]["worker_id"] == "local-worker-1"
    assert body["workers"][0]["supported_types"] == ["script_job"]


def test_rejects_unknown_task_type(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "run_shell", "payload": {"command": "whoami"}},
    )

    assert response.status_code == 400
    assert "Unsupported task type" in response.json()["detail"]


def test_rejects_non_object_payload(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "echo_test", "payload": "hello"},
    )

    assert response.status_code == 422


def test_create_script_job(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('cloudlink')",
                "runtime": "python-auto",
                "requirements": ["requests==2.32.3"],
                "timeout_seconds": 600,
            },
        },
    )

    assert response.status_code == 200
    task_id = response.json()["id"]
    task = client.get(
        f"/api/internal/tasks/{task_id}",
        headers=internal_headers(),
    ).json()
    assert task["type"] == "script_job"
    assert task["status"] == "pending"
    assert task["payload"]["script"].startswith("print")


def test_worker_claim_requires_auth(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/worker/claim",
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 401


def test_task_create_and_query_require_internal_auth(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    create_response = client.post(
        "/api/internal/tasks",
        json={"type": "echo_test", "payload": {"message": "hello"}},
    )
    assert create_response.status_code == 401

    task_id = create_echo_task(client)
    query_response = client.get(f"/api/internal/tasks/{task_id}")
    assert query_response.status_code == 401


def test_unknown_worker_cannot_claim(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "unknown-worker", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 403


def test_worker_claims_supported_pending_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["task"]["id"] == task_id
    assert body["task"]["type"] == "echo_test"
    assert body["task"]["payload"] == {"message": "hello"}
    assert body["task"]["lease_id"]

    claimed = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert claimed["status"] == "running"
    assert claimed["locked_by"] == "local-worker-1"
    assert claimed["locked_until"]
    assert claimed["started_at"]
    assert claimed["lease_id"] == body["task"]["lease_id"]


def test_worker_below_minimum_version_needs_update_and_cannot_claim(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        runtime_profile={
            "cloudlink_version": "2026.07.05.1",
            "python_version": "3.11",
        },
    )
    task_id = create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 200
    assert response.json() == {"task": None}
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "pending"

    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    worker = overview["workers"][0]
    assert worker["online"] is True
    assert worker["needs_update"] is True
    assert worker["version_status"] == "needs_update"
    assert worker["worker_version"] == "2026.07.05.1"
    assert worker["required_version"] == TEST_MINIMUM_WORKER_VERSION
    assert worker["minimum_worker_version"] == TEST_MINIMUM_WORKER_VERSION
    assert worker["server_version"] == TEST_CLOUDLINK_VERSION


def test_worker_heartbeat_at_minimum_version_clears_update_required(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        runtime_profile={
            "cloudlink_version": "2026.07.05.1",
            "python_version": "3.11",
        },
    )

    heartbeat = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "supported_types": ["echo_test"],
            "runtime_profile": {
                "cloudlink_version": TEST_MINIMUM_WORKER_VERSION,
                "python_version": "3.11",
            },
        },
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["version_status"] == "ok"
    assert heartbeat.json()["minimum_worker_version"] == TEST_MINIMUM_WORKER_VERSION
    assert heartbeat.json()["server_version"] == TEST_CLOUDLINK_VERSION

    task_id = create_echo_task(client)
    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 200
    assert response.json()["task"]["id"] == task_id


def test_gpu_worker_uses_gpu_specific_minimum_version(monkeypatch, tmp_path):
    gpu_minimum = "2026.07.05.8"
    client = make_client(
        monkeypatch,
        tmp_path,
        minimum_gpu_worker_version=gpu_minimum,
    )
    response = client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json={
            "worker_id": "gpu-worker",
            "display_name": "GPU Worker",
            "supported_types": ["script_job"],
            "enabled": True,
            "gpu_requested": True,
            "runtime_profile": {
                "cloudlink_version": TEST_MINIMUM_WORKER_VERSION,
                "gpu_runtime": {"enabled": True, "verified": True},
            },
        },
    )

    assert response.status_code == 200
    worker = response.json()
    assert worker["needs_update"] is True
    assert worker["version_status"] == "needs_update"
    assert worker["base_minimum_worker_version"] == TEST_MINIMUM_WORKER_VERSION
    assert worker["gpu_minimum_worker_version"] == gpu_minimum
    assert worker["minimum_worker_version"] == gpu_minimum


def test_cpu_worker_keeps_base_minimum_when_gpu_minimum_is_higher(
    monkeypatch,
    tmp_path,
):
    client = make_client(
        monkeypatch,
        tmp_path,
        minimum_gpu_worker_version="2026.07.05.8",
    )
    worker = register_worker(client)

    assert worker["needs_update"] is False
    assert worker["minimum_worker_version"] == TEST_MINIMUM_WORKER_VERSION


def test_worker_newer_than_minimum_version_can_claim(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        runtime_profile={
            "cloudlink_version": "2026.07.05.10",
            "python_version": "3.11",
        },
    )
    task_id = create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 200
    assert response.json()["task"]["id"] == task_id


def test_native_windows_worker_is_unsupported_and_cannot_claim(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        runtime_profile={
            "cloudlink_version": TEST_WORKER_VERSION,
            "python_version": "3.11",
            "system": "Windows",
        },
    )
    task_id = create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 200
    assert response.json() == {"task": None}
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "pending"

    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    worker = overview["workers"][0]
    assert worker["needs_update"] is True
    assert worker["version_status"] == "unsupported_platform"


def test_wsl_linux_worker_can_claim_with_microsoft_kernel_string(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        runtime_profile={
            "cloudlink_version": TEST_WORKER_VERSION,
            "python_version": "3.11",
            "system": "Linux",
            "platform": "Linux-5.15.0-microsoft-standard-WSL2-x86_64-with-glibc2.35",
        },
    )
    task_id = create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert response.status_code == 200
    assert response.json()["task"]["id"] == task_id


def test_worker_claim_returns_null_when_no_supported_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["generate_daily_report"])
    create_echo_task(client)

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "supported_types": ["generate_daily_report"],
        },
    )

    assert response.status_code == 200
    assert response.json() == {"task": None}


def test_worker_reports_success_for_owned_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]

    response = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
            "result": {"echo": "hello", "worker_id": "local-worker-1"},
            "logs": "done",
        },
    )

    assert response.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "success"
    assert task["result"] == {"echo": "hello", "worker_id": "local-worker-1"}
    assert task["logs"] == "done"
    assert task["finished_at"]


def test_worker_success_report_is_idempotent_after_response_loss(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]
    result = {"echo": "hello", "worker_id": "local-worker-1"}
    result_sha256 = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    body = {
        "worker_id": "local-worker-1",
        "lease_id": claim["lease_id"],
        "result_sha256": result_sha256,
        "result": result,
        "logs": "done",
    }

    first = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json=body,
    )
    repeated = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json=body,
    )

    assert first.status_code == 200
    assert repeated.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["result_sha256"] == result_sha256
    assert task["completion_lease_id"] == claim["lease_id"]


def test_worker_reports_large_success_payload(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]
    result = {"encoded_evidence": "x" * (900 * 1024)}
    result_sha256 = hashlib.sha256(
        json.dumps(
            result,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()

    response = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
            "result_sha256": result_sha256,
            "result": result,
            "logs": "large result",
        },
    )

    assert response.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "success"
    assert task["result_sha256"] == result_sha256
    assert len(task["result"]["encoded_evidence"]) == 900 * 1024


def test_worker_cannot_report_for_another_worker(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]

    response = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json={
            "worker_id": "other-worker",
            "lease_id": claim["lease_id"],
            "result": {"ok": True},
            "logs": "",
        },
    )

    assert response.status_code == 403


def test_worker_cannot_report_with_stale_lease(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]

    response = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": "old-lease",
            "result": {"ok": True},
            "logs": "",
        },
    )

    assert response.status_code == 409
    assert claim["lease_id"] != "old-lease"


def test_expired_running_task_is_not_automatically_reclaimed(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["script_job"])
    register_worker(
        client,
        worker_id="local-worker-2",
        supported_types=["script_job"],
    )
    task_id = create_script_task(client)
    client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["script_job"]},
    ).json()["task"]
    expire_task_lock(client, task_id, retry_count=0)

    second_claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-2", "supported_types": ["script_job"]},
    )

    assert second_claim.status_code == 200
    assert second_claim.json() == {"task": None}

    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "disconnected"
    assert task["error_code"] == "worker_disconnected"
    assert task["retry_count"] == 0

    create_script_task(client, script="print('second')")
    original_worker_claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["script_job"]},
    )
    assert original_worker_claim.status_code == 200
    assert original_worker_claim.json() == {"task": None}


def test_expired_running_task_waits_for_original_worker_regardless_of_retry_count(
    monkeypatch,
    tmp_path,
):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["script_job"])
    task_id = create_script_task(client)
    client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["script_job"]},
    )
    expire_task_lock(client, task_id, retry_count=1)

    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )

    assert claim.status_code == 200
    assert claim.json() == {"task": None}
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "disconnected"
    assert "waiting for the original execution" in task["error"]
    assert task["finished_at"] is None


def test_expired_non_script_task_remains_terminal_worker_lost(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    )
    expire_task_lock(client, task_id)

    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()

    assert task["status"] == "timeout"
    assert task["error_code"] == "worker_lost"
    assert task["finished_at"] is not None


def test_expired_script_execution_can_resume_with_original_worker_and_lease(
    monkeypatch,
    tmp_path,
):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["script_job"])
    task_id = create_script_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["script_job"]},
    ).json()["task"]
    expire_task_lock(client, task_id)

    renewal = client.post(
        f"/api/worker/tasks/{task_id}/lease",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
        },
    )

    assert renewal.status_code == 409
    assert renewal.json()["detail"]["code"] == "execution_disconnected"
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "disconnected"
    assert task["resource_reservation"] is not None

    resume = client.post(
        f"/api/worker/tasks/{task_id}/execution/resume",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
        },
    )

    assert resume.status_code == 200
    assert resume.json()["lease_id"] == claim["lease_id"]
    assert resume.json()["cancel_requested"] is False
    resumed = client.get(
        f"/api/internal/tasks/{task_id}",
        headers=internal_headers(),
    ).json()
    assert resumed["status"] == "running"
    assert resumed["error_code"] is None
    assert resumed["resource_reservation"] is not None

    renewed = client.post(
        f"/api/worker/tasks/{task_id}/lease",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
        },
    )
    assert renewed.status_code == 200


def test_disconnected_execution_rejects_wrong_worker_or_lease(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["script_job"])
    register_worker(
        client,
        worker_id="local-worker-2",
        supported_types=["script_job"],
    )
    task_id = create_script_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["script_job"]},
    ).json()["task"]
    expire_task_lock(client, task_id)
    client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers())

    wrong_worker = client.post(
        f"/api/worker/tasks/{task_id}/execution/resume",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-2",
            "lease_id": claim["lease_id"],
        },
    )
    wrong_lease = client.post(
        f"/api/worker/tasks/{task_id}/execution/resume",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": "wrong-lease",
        },
    )

    assert wrong_worker.status_code == 409
    assert wrong_worker.json()["detail"]["code"] == "task_worker_mismatch"
    assert wrong_lease.status_code == 409
    assert wrong_lease.json()["detail"]["code"] == "task_lease_mismatch"


def test_cancelled_disconnected_execution_notifies_original_worker(
    monkeypatch,
    tmp_path,
):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, supported_types=["script_job"])
    task_id = create_script_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["script_job"]},
    ).json()["task"]
    expire_task_lock(client, task_id)
    client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers())

    cancel = client.post(
        f"/api/internal/tasks/{task_id}/cancel",
        headers=internal_headers(),
        json={"reason": "operator cancelled while worker was offline"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "disconnected"

    resume = client.post(
        f"/api/worker/tasks/{task_id}/execution/resume",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
        },
    )
    assert resume.status_code == 200
    assert resume.json()["cancel_requested"] is True
    assert resume.json()["cancel_reason"] == "operator cancelled while worker was offline"

    reported = client.post(
        f"/api/worker/tasks/{task_id}/failed",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
            "error": "process tree terminated",
            "error_code": "cancelled",
            "logs": "",
        },
    )
    assert reported.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "cancelled"


def test_worker_can_renew_lease_and_receive_running_cancel(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]

    cancel = client.post(
        f"/api/internal/tasks/{task_id}/cancel",
        headers=internal_headers(),
        json={"reason": "stop long model run"},
    )
    assert cancel.status_code == 200
    assert cancel.json()["status"] == "running"
    assert cancel.json()["cancel_requested_at"]

    renewal = client.post(
        f"/api/worker/tasks/{task_id}/lease",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
        },
    )
    assert renewal.status_code == 200
    assert renewal.json()["cancel_requested"] is True
    assert renewal.json()["cancel_reason"] == "stop long model run"

    cancelled = client.post(
        f"/api/worker/tasks/{task_id}/failed",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
            "error": "process tree terminated",
            "error_code": "cancelled",
            "logs": "",
        },
    )
    assert cancelled.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "cancelled"


def test_running_cancel_wins_over_late_success_report(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]
    client.post(
        f"/api/internal/tasks/{task_id}/cancel",
        headers=internal_headers(),
        json={"reason": "stop before accepting result"},
    )

    response = client.post(
        f"/api/worker/tasks/{task_id}/success",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
            "result": {"ok": True},
            "logs": "late success",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "cancelled"
    assert task["error_code"] == "cancelled"


def test_worker_reports_failed_for_owned_task(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    task_id = create_echo_task(client)
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "local-worker-1", "supported_types": ["echo_test"]},
    ).json()["task"]

    response = client.post(
        f"/api/worker/tasks/{task_id}/failed",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "lease_id": claim["lease_id"],
            "error": "boom",
            "logs": "traceback text",
        },
    )

    assert response.status_code == 200
    task = client.get(f"/api/internal/tasks/{task_id}", headers=internal_headers()).json()
    assert task["status"] == "failed"
    assert task["error"] == "boom"
    assert task["logs"] == "traceback text"
    assert task["finished_at"]


def test_worker_heartbeat_updates_dashboard_nodes(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)

    response = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "supported_types": ["echo_test"],
        },
    )
    assert response.status_code == 200

    overview = client.get("/api/admin/overview", auth=admin_auth())
    assert overview.status_code == 200
    nodes = overview.json()["workers"]
    assert nodes[0]["worker_id"] == "local-worker-1"
    assert nodes[0]["online"] is True


def test_worker_heartbeat_does_not_shrink_registered_types(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        supported_types=["echo_test", "generate_daily_report", "script_job"],
    )

    response = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "local-worker-1",
            "supported_types": ["echo_test"],
        },
    )
    assert response.status_code == 200

    overview = client.get("/api/admin/overview", auth=admin_auth())
    node = overview.json()["workers"][0]
    assert node["supported_types"] == [
        "echo_test",
        "generate_daily_report",
        "script_job",
    ]


def test_dashboard_requires_admin_auth(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    assert client.get("/").status_code == 401
    assert client.get("/api/admin/overview").status_code == 401
    response = client.get("/", auth=admin_auth())
    assert response.status_code == 200
    assert "Cloudlink 调度控制台" in response.text
    assert "任务队列" in response.text
    assert "本地算力节点" in response.text
    assert "服务器数据维护" in response.text
    assert "运行 / 总耗时" in response.text
    assert "脚本运行耗时" in response.text
    assert "完整生命周期耗时" in response.text
    assert "taskDurations" in response.text
    assert "X-Cloudlink-Section-Etags" in response.text
    assert "response.status === 304" in response.text
    assert "/api/admin/tasks/${encodeURIComponent(taskId)}" in response.text
    assert "document.hidden ? 30000 : 5000" in response.text
    assert "visibilitychange" in response.text
    assert 'id="tasks-panel"' in response.text
    assert "task-scroll" in response.text
    assert "任务说明" in response.text
    assert "结果文件" in response.text
    assert "文件意义" in response.text
    assert "下载" in response.text
    assert "stored_on_server" in response.text
    assert "height: clamp(560px, calc(100vh - 230px), 760px)" in response.text
    assert "worker-card-list" in response.text
    assert 'id="task-modal"' in response.text
    assert 'id="worker-settings-modal"' in response.text
    assert "保存设置" in response.text
    assert 'id="details-panel"' not in response.text
    assert "Cloudlink Task Console" not in response.text


def test_admin_overview_is_lightweight_and_conditional(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    task_id = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "title": "Large task",
            "description": "Large result should only load in detail.",
            "payload": {"script": "print('ok')", "blob": "p" * 50000},
        },
    ).json()["id"]
    with sqlite3.connect(client.db_path) as conn:
        conn.execute(
            """
            UPDATE tasks
            SET status = 'success',
                result = ?,
                logs = ?,
                updated_at = '2026-01-01T00:00:00+00:00',
                started_at = '2026-01-01T00:00:00+00:00',
                finished_at = '2026-01-01T00:00:01+00:00'
            WHERE id = ?
            """,
            (
                json.dumps(
                    {
                        "summary": "finished",
                        "stdout": "s" * 50000,
                        "output_files": [{"path": "big.txt", "content": "o" * 50000}],
                    }
                ),
                "l" * 50000,
                task_id,
            ),
        )

    overview = client.get("/api/admin/overview", auth=admin_auth())

    assert overview.status_code == 200
    assert len(overview.content) < 12000
    assert "etag" in overview.headers
    assert "x-cloudlink-section-etags" in overview.headers
    body = overview.json()
    assert "artifacts" not in body
    task = body["tasks"][0]
    assert task["id"] == task_id
    assert "payload" not in task
    assert "result" not in task
    assert "logs" not in task
    assert "title" not in task
    assert "description" not in task
    assert "error" not in task
    assert "resource_request" not in task

    repeat = client.get(
        "/api/admin/overview",
        auth=admin_auth(),
        headers={
            "If-None-Match": overview.headers["etag"],
            "X-Cloudlink-Section-Etags": overview.headers["x-cloudlink-section-etags"],
        },
    )

    assert repeat.status_code == 304
    assert repeat.content == b""

    detail = client.get(f"/api/admin/tasks/{task_id}", auth=admin_auth())
    assert detail.status_code == 200
    detail_body = detail.json()
    assert detail_body["payload"]["blob"] == "p" * 50000
    assert detail_body["result"]["stdout"] == "s" * 50000
    assert detail_body["logs"] == "l" * 50000


def test_admin_overview_returns_only_changed_sections(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    first = client.get("/api/admin/overview", auth=admin_auth())
    assert first.status_code == 200

    create_echo_task(client, "changed")
    changed = client.get(
        "/api/admin/overview",
        auth=admin_auth(),
        headers={
            "If-None-Match": first.headers["etag"],
            "X-Cloudlink-Section-Etags": first.headers["x-cloudlink-section-etags"],
        },
    )

    assert changed.status_code == 200
    body = changed.json()
    assert sorted(body["changed_sections"]) == ["summary", "tasks"]
    assert "summary" in body
    assert "tasks" in body
    assert "workers" not in body
    assert "datasets" not in body
    assert "dataset_caches" not in body


def test_admin_overview_supports_gzip(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_MAX_PENDING_TASKS", "30")
    client = make_client(monkeypatch, tmp_path)
    for index in range(20):
        create_echo_task(client, f"gzip-{index}")

    response = client.get(
        "/api/admin/overview",
        auth=admin_auth(),
        headers={"Accept-Encoding": "gzip"},
    )

    assert response.status_code == 200
    assert response.headers["content-encoding"] == "gzip"
