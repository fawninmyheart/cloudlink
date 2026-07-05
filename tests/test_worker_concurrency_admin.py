from uuid import uuid4

from tests.test_tasks_api import (
    admin_auth,
    internal_headers,
    make_client,
    register_worker,
    worker_headers,
)


def test_admin_can_update_worker_concurrency(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])

    response = client.patch(
        "/api/admin/workers/worker-a/concurrency",
        auth=admin_auth(),
        json={"max_concurrent_tasks": 3},
    )

    assert response.status_code == 200
    assert response.json()["max_concurrent_tasks"] == 3
    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert overview["workers"][0]["max_concurrent_tasks"] == 3


def test_admin_concurrency_rejects_non_positive_values(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])

    response = client.patch(
        "/api/admin/workers/worker-a/concurrency",
        auth=admin_auth(),
        json={"max_concurrent_tasks": 0},
    )

    assert response.status_code == 422


def test_codex_token_cannot_update_worker_concurrency(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])

    response = client.patch(
        "/api/admin/workers/worker-a/concurrency",
        headers={"X-Cloudlink-Codex-Token": "codex-secret"},
        json={"max_concurrent_tasks": 3},
    )

    assert response.status_code == 401


def test_heartbeat_returns_server_concurrency_without_overwriting(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])
    client.patch(
        "/api/admin/workers/worker-a/concurrency",
        auth=admin_auth(),
        json={"max_concurrent_tasks": 4},
    )

    response = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "max_concurrent_tasks": 1,
            "active_task_count": 0,
        },
    )

    assert response.status_code == 200
    assert response.json()["max_concurrent_tasks"] == 4
    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert overview["workers"][0]["max_concurrent_tasks"] == 4


def test_admin_can_update_worker_runtime_settings(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])

    response = client.patch(
        "/api/admin/workers/worker-a/settings",
        auth=admin_auth(),
        json={
            "max_concurrent_tasks": 3,
            "job_root": "/home/cloudlink-test/.cloudlink/jobs-fast",
            "dataset_roots": [
                {
                    "path": "/Volumes/FastData/cloudlink",
                    "mode": "active",
                    "label": "FastData",
                },
                {
                    "path": "/home/cloudlink-test/.cloudlink/datasets",
                    "mode": "readonly",
                    "label": "旧数据盘",
                },
            ],
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["max_concurrent_tasks"] == 3
    assert body["configured_job_root"] == "/home/cloudlink-test/.cloudlink/jobs-fast"
    assert body["configured_dataset_roots"] == [
        {
            "path": "/Volumes/FastData/cloudlink",
            "mode": "active",
            "label": "FastData",
        },
        {
            "path": "/home/cloudlink-test/.cloudlink/datasets",
            "mode": "readonly",
            "label": "旧数据盘",
        },
    ]

    heartbeat = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "runtime_profile": {
                "job_root": "/home/cloudlink-test/.cloudlink/jobs",
                "dataset_root": "/home/cloudlink-test/.cloudlink/datasets",
            },
            "active_task_count": 0,
        },
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["settings"] == {
        "max_concurrent_tasks": 3,
        "job_root": "/home/cloudlink-test/.cloudlink/jobs-fast",
        "dataset_roots": [
            {
                "path": "/Volumes/FastData/cloudlink",
                "mode": "active",
                "label": "FastData",
            },
            {
                "path": "/home/cloudlink-test/.cloudlink/datasets",
                "mode": "readonly",
                "label": "旧数据盘",
            },
        ],
        "reserve_overrides": {},
    }


def test_admin_can_update_worker_reserve_settings(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])

    response = client.patch(
        "/api/admin/workers/worker-a/settings",
        auth=admin_auth(),
        json={
            "max_concurrent_tasks": 2,
            "reserve_overrides": {
                "cpu_cores": 4,
                "memory_bytes": 10 * 1024**3,
                "job_disk_bytes": 80 * 1024**3,
                "dataset_disk_bytes": 120 * 1024**3,
                "gpu_memory_bytes": 2 * 1024**3,
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["configured_reserve_overrides"] == {
        "cpu_cores": 4,
        "memory_bytes": 10 * 1024**3,
        "job_disk_bytes": 80 * 1024**3,
        "dataset_disk_bytes": 120 * 1024**3,
        "gpu_memory_bytes": 2 * 1024**3,
    }

    heartbeat = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "active_task_count": 0,
        },
    )

    assert heartbeat.status_code == 200
    assert heartbeat.json()["settings"]["reserve_overrides"] == {
        "cpu_cores": 4,
        "memory_bytes": 10 * 1024**3,
        "job_disk_bytes": 80 * 1024**3,
        "dataset_disk_bytes": 120 * 1024**3,
        "gpu_memory_bytes": 2 * 1024**3,
    }


def test_admin_worker_settings_drop_removed_dataset_root_without_cache(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(
        client,
        worker_id="worker-a",
        supported_types=["script_job"],
    )
    client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "runtime_profile": {
                "dataset_root": "/home/cloudlink-test/.cloudlink/datasets-old",
            },
            "active_task_count": 0,
        },
    )

    response = client.patch(
        "/api/admin/workers/worker-a/settings",
        auth=admin_auth(),
        json={
            "max_concurrent_tasks": 2,
            "dataset_roots": [
                {
                    "path": "/Volumes/FastData/cloudlink",
                    "mode": "active",
                    "label": "FastData",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["configured_dataset_roots"] == [
        {
            "path": "/Volumes/FastData/cloudlink",
            "mode": "active",
            "label": "FastData",
        }
    ]


def test_admin_worker_settings_preserve_existing_dataset_root_with_cache(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    source = tmp_path / "cached-source.txt"
    source.write_text("cached", encoding="utf-8")
    register_worker(
        client,
        worker_id="worker-a",
        supported_types=["script_job"],
    )
    client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "runtime_profile": {
                "dataset_root": "/home/cloudlink-test/.cloudlink/datasets-old",
            },
            "active_task_count": 0,
        },
    )
    dataset_name = f"cached-ds-root-preserve-{uuid4().hex}"
    dataset_response = client.post(
        "/api/internal/datasets",
        headers=internal_headers(),
        json={
            "name": dataset_name,
            "version": "v1",
            "title": "Cached DS",
            "description": "cached",
            "source_kind": "symlink_file",
            "source_path": str(source),
            "content_type": "text/plain",
        },
    )
    dataset = dataset_response.json()
    client.post(
        f"/api/worker/datasets/{dataset['id']}/cache",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "status": "cached",
            "local_archive_path": "/home/cloudlink-test/.cloudlink/datasets-old/archives/cached-ds/source",
            "data_root_path": "/home/cloudlink-test/.cloudlink/datasets-old",
        },
    )

    response = client.patch(
        "/api/admin/workers/worker-a/settings",
        auth=admin_auth(),
        json={
            "max_concurrent_tasks": 2,
            "dataset_roots": [
                {
                    "path": "/Volumes/FastData/cloudlink",
                    "mode": "active",
                    "label": "FastData",
                }
            ],
        },
    )

    assert response.status_code == 200
    assert response.json()["configured_dataset_roots"] == [
        {
            "path": "/Volumes/FastData/cloudlink",
            "mode": "active",
            "label": "FastData",
        },
        {
            "path": "/home/cloudlink-test/.cloudlink/datasets-old",
            "mode": "readonly",
            "label": "历史数据盘",
        },
    ]


def test_worker_heartbeat_persists_dataset_root_checks(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])

    response = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "runtime_profile": {
                "dataset_roots": [
                    {"path": "/data/fast", "mode": "active", "label": "Fast"},
                    {"path": "/data/old", "mode": "readonly", "label": "Old"},
                ],
            },
            "dataset_root_checks": [
                {
                    "path": "/data/fast",
                    "mode": "active",
                    "status": "ok",
                    "readable": True,
                    "writable": True,
                    "free_bytes": 12345,
                    "cache_archive_count": 2,
                    "cache_extracted_count": 1,
                    "error": None,
                },
                {
                    "path": "/data/old",
                    "mode": "readonly",
                    "status": "failed",
                    "readable": False,
                    "writable": False,
                    "free_bytes": 0,
                    "cache_archive_count": 0,
                    "cache_extracted_count": 0,
                    "error": "path does not exist",
                },
            ],
            "active_task_count": 0,
        },
    )

    assert response.status_code == 200
    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    worker = overview["workers"][0]
    assert worker["dataset_root_checks"][0]["path"] == "/data/fast"
    assert worker["dataset_root_checks"][0]["status"] == "ok"
    assert worker["dataset_root_checks"][1]["status"] == "failed"


def test_admin_worker_cards_keep_stable_order_after_heartbeats(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client, worker_id="worker-a", supported_types=["script_job"])
    register_worker(client, worker_id="worker-b", supported_types=["script_job"])

    response = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-b",
            "supported_types": ["script_job"],
            "active_task_count": 0,
        },
    )

    assert response.status_code == 200
    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert [worker["worker_id"] for worker in overview["workers"]] == [
        "worker-a",
        "worker-b",
    ]
