import hashlib
import sqlite3
from pathlib import Path

from tests.test_artifact_api import (
    admin_auth,
    internal_headers,
    make_client,
    register_worker,
    worker_headers,
)


def test_artifact_purge_is_previewed_then_returns_gone(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_RETENTION_SECONDS", "1")
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    heartbeat = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "runtime_profile": {"cloudlink_version": "2026.07.05.2"},
        },
    )
    assert heartbeat.status_code == 200
    create_task = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={"type": "script_job", "payload": {"script": "print('ok')"}},
    ).json()
    claim = client.post(
        "/api/worker/claim",
        headers=worker_headers(),
        json={"worker_id": "worker-a", "supported_types": ["script_job"]},
    ).json()["task"]
    content = b"temporary result"
    artifact = client.post(
        f"/api/worker/tasks/{claim['id']}/artifacts",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": claim["lease_id"],
            "relative_path": "result.bin",
            "title": "Result",
            "description": "",
            "meaning": "Temporary test result",
            "size_bytes": len(content),
            "sha256": hashlib.sha256(content).hexdigest(),
        },
    ).json()
    upload = client.put(
        f"/api/worker/tasks/{claim['id']}/artifacts/{artifact['id']}/content",
        headers={**worker_headers(), "Content-Type": "application/octet-stream"},
        content=content,
    )
    assert upload.status_code == 200
    success = client.post(
        f"/api/worker/tasks/{claim['id']}/success",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "lease_id": claim["lease_id"],
            "result": {"summary": "done"},
            "logs": "",
        },
    )
    assert success.status_code == 200
    with sqlite3.connect(str(tmp_path / "tasks.db")) as conn:
        conn.execute(
            "UPDATE task_artifacts SET expires_at = '2000-01-01T00:00:00+00:00'"
        )

    preview = client.post(
        "/api/admin/storage/artifacts/purge",
        auth=admin_auth(),
        json={"dry_run": True},
    )
    assert preview.status_code == 200
    assert preview.json()["candidate_count"] == 1
    assert Path(artifact["storage_path"]).exists()

    execute = client.post(
        "/api/admin/storage/artifacts/purge",
        auth=admin_auth(),
        json={"dry_run": False},
    )
    assert execute.status_code == 200
    assert execute.json()["processed_count"] == 1
    download = client.get(
        f"/api/admin/tasks/{create_task['id']}/artifacts/{artifact['id']}/download",
        auth=admin_auth(),
    )
    assert download.status_code == 410


def test_released_dataset_requires_online_verified_cache_holder(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)
    register_worker(client)
    heartbeat = client.post(
        "/api/worker/heartbeat",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "supported_types": ["script_job"],
            "runtime_profile": {"cloudlink_version": "2026.07.05.2"},
        },
    )
    assert heartbeat.status_code == 200
    source = tmp_path / "source.csv"
    source.write_text("ts,close\n1,100\n", encoding="utf-8")
    dataset = client.post(
        "/api/internal/datasets",
        headers=internal_headers(),
        json={
            "name": "released-data",
            "version": "v1",
            "source_kind": "owned_file",
            "source_path": str(source),
            "compute_sha256": True,
        },
    ).json()
    cache = client.post(
        f"/api/worker/datasets/{dataset['id']}/cache",
        headers=worker_headers(),
        json={
            "worker_id": "worker-a",
            "status": "cached",
            "local_archive_path": "/data/released-data.csv",
            "size_bytes": dataset["size_bytes"],
            "checksum_sha256": dataset["checksum_sha256"],
        },
    )
    assert cache.status_code == 200
    release = client.post(
        f"/api/admin/datasets/{dataset['id']}/release-server-copy",
        auth=admin_auth(),
        json={"dry_run": False, "reason": "cache-only test"},
    )
    assert release.status_code == 200
    assert not Path(dataset["server_path"]).exists()

    task = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('ok')",
                "datasets": [
                    {
                        "dataset_version_id": dataset["id"],
                        "mount_name": "data",
                        "required": True,
                    }
                ],
            },
        },
    )
    assert task.status_code == 200, task.text

    with sqlite3.connect(str(tmp_path / "tasks.db")) as conn:
        conn.execute(
            "UPDATE worker_nodes SET last_seen_at = '2000-01-01T00:00:00+00:00'"
        )
    client.get("/api/internal/queue/status", headers=internal_headers())
    result = client.get(
        f"/api/internal/tasks/{task.json()['id']}",
        headers=internal_headers(),
    ).json()
    assert result["status"] == "failed"
    assert result["error_code"] == "dataset_became_unavailable"

    rejected = client.post(
        "/api/internal/tasks",
        headers=internal_headers(),
        json={
            "type": "script_job",
            "payload": {
                "script": "print('ok')",
                "datasets": [{"dataset_version_id": dataset["id"], "mount_name": "data"}],
            },
        },
    )
    assert rejected.status_code == 422
    assert rejected.json()["detail"]["code"] == "dataset_unavailable"
