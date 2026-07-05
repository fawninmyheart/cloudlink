import sqlite3

from tests.test_tasks_api import (
    TEST_WORKER_VERSION,
    internal_headers,
    make_client,
    register_worker,
    worker_headers,
)


GIB = 1024**3


def resource_worker_payload(worker_id="worker-a", *, active=0, max_concurrent=1):
    return {
        "worker_id": worker_id,
        "display_name": worker_id,
        "supported_types": ["script_job"],
        "enabled": True,
        "hardware_profile": {
            "raw": {
                "cpu_logical_cores": 10,
                "memory_total_bytes": 64 * GIB,
                "job_disk_total_bytes": 500 * GIB,
                "dataset_disk_total_bytes": 500 * GIB,
            },
            "reserve": {
                "cpu_cores": 2,
                "memory_bytes": 8 * GIB,
                "job_disk_bytes": 50 * GIB,
                "dataset_disk_bytes": 50 * GIB,
                "gpu_memory_bytes": 0,
            },
            "scheduler": {
                "cpu_cores": 8,
                "memory_bytes": 56 * GIB,
                "job_disk_bytes": 450 * GIB,
                "dataset_disk_bytes": 450 * GIB,
                "gpu_devices": [],
            },
        },
        "runtime_profile": {
            "cloudlink_version": TEST_WORKER_VERSION,
            "python_version": "3.11",
        },
        "capacity_state": {
            "cpu_cores": 8,
            "memory_bytes": 56 * GIB,
            "job_disk_bytes": 450 * GIB,
            "dataset_disk_bytes": 450 * GIB,
            "gpu_devices": [],
        },
        "max_concurrent_tasks": max_concurrent,
        "active_task_count": active,
    }


def create_script_job(client, resource_request):
    response = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('ok')",
                "resource_request": resource_request,
            },
        },
    )
    return response


def test_register_worker_stores_resource_profiles(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(),
    )

    assert response.status_code == 200
    body = response.json()
    assert body["hardware_profile"]["scheduler"]["memory_bytes"] == 56 * GIB
    assert body["runtime_profile"] == {
        "cloudlink_version": TEST_WORKER_VERSION,
        "python_version": "3.11",
    }
    assert body["capacity_state"]["cpu_cores"] == 8
    assert body["max_concurrent_tasks"] == 1
    assert body["active_task_count"] == 0


def test_create_script_job_rejects_impossible_resource_request(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(),
    )

    response = create_script_job(
        client,
        {"cpu_cores": 100, "memory_bytes": 8 * GIB},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "resource_request_unsatisfiable"
    assert detail["resource_request"]["cpu_cores"] == 100
    assert detail["shortages"][0]["worker_id"] == "worker-a"


def test_create_script_job_accepts_feasible_resource_request(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(),
    )

    response = create_script_job(
        client,
        {"cpu_cores": 4, "memory_bytes": 8 * GIB, "job_disk_bytes": 10 * GIB},
    )

    assert response.status_code == 200
    task = client.get(
        f"/api/internal/tasks/{response.json()['id']}",
        headers=internal_headers(),
    ).json()
    assert task["resource_request"]["cpu_cores"] == 4
    assert task["resource_request"]["memory_bytes"] == 8 * GIB


def test_fractional_worker_cpu_capacity_is_floored_for_scheduling(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    payload = resource_worker_payload()
    payload["hardware_profile"]["scheduler"]["cpu_cores"] = 4.8
    payload["capacity_state"]["cpu_cores"] = 4.8
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=payload,
    )

    response = create_script_job(client, {"cpu_cores": 4.5})

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "resource_request_unsatisfiable"


def test_claim_skips_task_when_current_capacity_is_too_low(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(),
    )
    create_script_job(client, {"cpu_cores": 4, "memory_bytes": 8 * GIB})

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "capacity_state": {
                "cpu_cores": 2,
                "memory_bytes": 4 * GIB,
                "job_disk_bytes": 450 * GIB,
                "dataset_disk_bytes": 450 * GIB,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["task"] is None


def test_claim_respects_worker_concurrency_limit(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(active=1, max_concurrent=1),
    )
    create_script_job(client, {"cpu_cores": 1, "memory_bytes": GIB})

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "active_task_count": 1,
        },
    )

    assert response.status_code == 200
    assert response.json()["task"] is None


def test_starvation_protection_blocks_small_task_behind_aged_large_task(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_STARVATION_PROTECTION_SECONDS", "60")
    monkeypatch.setenv("CLOUDLINK_QUEUE_TIMEOUT_SECONDS", "999999999")
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(max_concurrent=2),
    )
    large = create_script_job(
        client,
        {"cpu_cores": 8, "memory_bytes": 40 * GIB},
    ).json()["id"]
    small = create_script_job(
        client,
        {"cpu_cores": 1, "memory_bytes": GIB},
    ).json()["id"]
    with sqlite3.connect(client.db_path) as conn:
        conn.execute(
            """
            UPDATE tasks
            SET created_at = '2000-01-01T00:00:00+00:00',
                updated_at = '2000-01-01T00:00:00+00:00'
            WHERE id = ?
            """,
            (large,),
        )

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "capacity_state": {
                "cpu_cores": 4,
                "memory_bytes": 16 * GIB,
                "job_disk_bytes": 450 * GIB,
                "dataset_disk_bytes": 450 * GIB,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["task"] is None
    large_task = client.get(f"/api/internal/tasks/{large}", headers=internal_headers()).json()
    small_task = client.get(f"/api/internal/tasks/{small}", headers=internal_headers()).json()
    assert large_task["status"] == "pending"
    assert small_task["status"] == "pending"


def test_young_large_task_does_not_block_small_task(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_STARVATION_PROTECTION_SECONDS", "3600")
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(max_concurrent=2),
    )
    create_script_job(client, {"cpu_cores": 8, "memory_bytes": 40 * GIB})
    small = create_script_job(client, {"cpu_cores": 1, "memory_bytes": GIB}).json()["id"]

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "capacity_state": {
                "cpu_cores": 4,
                "memory_bytes": 16 * GIB,
                "job_disk_bytes": 450 * GIB,
                "dataset_disk_bytes": 450 * GIB,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["task"]["id"] == small


def test_overview_worker_capacity_subtracts_running_task_reservations(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(max_concurrent=2),
    )
    create_script_job(client, {"cpu_cores": 3, "memory_bytes": 12 * GIB})

    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "worker-a", "supported_types": ["script_job"]},
    )

    assert claim.status_code == 200
    overview = client.get("/api/admin/overview", auth=("admin", "admin-pass")).json()
    worker = overview["workers"][0]
    assert worker["capacity_state"]["cpu_cores"] == 5
    assert worker["capacity_state"]["memory_bytes"] == 44 * GIB
    assert worker["reserved_resources"]["cpu_cores"] == 3
    assert worker["reserved_resources"]["memory_bytes"] == 12 * GIB


def test_overview_uses_configured_reserve_for_display(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(),
    )

    response = client.patch(
        "/api/admin/workers/worker-a/settings",
        auth=("admin", "admin-pass"),
        json={
            "max_concurrent_tasks": 1,
            "reserve_overrides": {
                "cpu_cores": 4,
                "memory_bytes": 16 * GIB,
                "job_disk_bytes": 100 * GIB,
                "dataset_disk_bytes": 120 * GIB,
            },
        },
    )

    assert response.status_code == 200
    overview = client.get("/api/admin/overview", auth=("admin", "admin-pass")).json()
    worker = overview["workers"][0]
    assert worker["hardware_profile"]["reserve"]["cpu_cores"] == 4
    assert worker["hardware_profile"]["scheduler"]["cpu_cores"] == 6
    assert worker["hardware_profile"]["scheduler"]["memory_bytes"] == 48 * GIB
    assert worker["capacity_state"]["cpu_cores"] == 6
    assert worker["capacity_state"]["memory_bytes"] == 48 * GIB
    assert worker["reported_hardware_profile"]["reserve"]["cpu_cores"] == 2


def test_overview_recomputes_cpu_capacity_when_configured_reserve_decreases(
    monkeypatch,
    tmp_path,
):
    client = make_client(monkeypatch, tmp_path)
    client.post(
        "/api/internal/workers",
        headers=internal_headers(),
        json=resource_worker_payload(),
    )

    response = client.patch(
        "/api/admin/workers/worker-a/settings",
        auth=("admin", "admin-pass"),
        json={
            "max_concurrent_tasks": 1,
            "reserve_overrides": {
                "cpu_cores": 1,
                "memory_bytes": 8 * GIB,
                "job_disk_bytes": 50 * GIB,
                "dataset_disk_bytes": 50 * GIB,
            },
        },
    )

    assert response.status_code == 200
    overview = client.get("/api/admin/overview", auth=("admin", "admin-pass")).json()
    worker = overview["workers"][0]
    assert worker["hardware_profile"]["scheduler"]["cpu_cores"] == 9
    assert worker["capacity_state"]["cpu_cores"] == 9
    assert worker["reserved_resources"]["cpu_cores"] == 0
    assert worker["reported_capacity_state"]["cpu_cores"] == 8


def test_old_workers_cannot_claim_zero_resource_tasks(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        worker_id="old-worker",
        supported_types=["script_job"],
        runtime_profile={"python_version": "3.11"},
    )
    create_script_job(client, {})

    response = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "old-worker", "supported_types": ["script_job"]},
    )

    assert response.status_code == 200
    assert response.json() == {"task": None}
    overview = client.get("/api/admin/overview", auth=("admin", "admin-pass")).json()
    assert overview["workers"][0]["needs_update"] is True
