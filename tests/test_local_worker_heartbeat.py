import json
import threading
from pathlib import Path

import worker.local_worker as local_worker_module
from worker.config import WorkerConfig
from worker.local_worker import CloudWorker
from worker.script_runner import ScriptExecutionTimeout


def configure_worker_env(monkeypatch) -> None:
    monkeypatch.setenv("CLOUD_API_BASE_URL", "https://tasks.example.test")
    monkeypatch.setenv("WORKER_SECRET", "worker-secret")
    monkeypatch.setenv("WORKER_ID", "worker-a")
    monkeypatch.setenv("WORKER_SUPPORTED_TYPES", "script_job")
    monkeypatch.setenv("WORKER_HEARTBEAT_SECONDS", "0.01")


def test_worker_heartbeat_loop_reports_until_stopped(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setattr(
        local_worker_module,
        "collect_worker_profiles",
        lambda **_kwargs: (
            {
                "scheduler": {
                    "cpu_cores": 4,
                    "memory_bytes": 16,
                    "job_disk_bytes": 32,
                    "dataset_disk_bytes": 64,
                    "gpu_devices": [],
                }
            },
            {"python_version": "3.11"},
            {
                "cpu_cores": 4,
                "memory_bytes": 12,
                "job_disk_bytes": 30,
                "dataset_disk_bytes": 60,
                "gpu_devices": [],
            },
        ),
    )
    worker = CloudWorker()
    calls = []
    reported_twice = threading.Event()

    def fake_post_json(path, body):
        calls.append((path, body))
        if len(calls) >= 2:
            worker.stop_event.set()
            reported_twice.set()
        return {}

    monkeypatch.setattr(worker, "post_json", fake_post_json)

    thread = threading.Thread(target=worker.heartbeat_loop)
    thread.start()

    assert reported_twice.wait(timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert [path for path, _body in calls] == [
        "/api/worker/heartbeat",
        "/api/worker/heartbeat",
    ]
    assert calls[0][1]["hardware_profile"]["scheduler"]["cpu_cores"] == 4
    assert calls[0][1]["runtime_profile"] == {"python_version": "3.11"}
    assert calls[0][1]["capacity_state"]["memory_bytes"] == 12
    assert calls[0][1]["max_concurrent_tasks"] == 1
    assert calls[0][1]["active_task_count"] == 0


def test_worker_claim_sends_capacity_state(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_TASKS", "2")
    monkeypatch.setattr(
        local_worker_module,
        "collect_worker_profiles",
        lambda **_kwargs: (
            {"scheduler": {"cpu_cores": 4}},
            {"python_version": "3.11"},
            {"cpu_cores": 4, "memory_bytes": 12},
        ),
    )
    worker = CloudWorker()
    captured = {}

    def fake_post_json(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"task": None}

    monkeypatch.setattr(worker, "post_json", fake_post_json)

    assert worker.claim_task() is None
    assert captured["path"] == "/api/worker/claim"
    assert captured["body"]["capacity_state"] == {"cpu_cores": 4, "memory_bytes": 12}
    assert captured["body"]["active_task_count"] == 0


def test_worker_heartbeat_adopts_server_concurrency(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_TASKS", "1")
    monkeypatch.setattr(
        local_worker_module,
        "collect_worker_profiles",
        lambda **_kwargs: (
            {"scheduler": {"cpu_cores": 4}},
            {"python_version": "3.11"},
            {"cpu_cores": 4, "memory_bytes": 12},
        ),
    )
    worker = CloudWorker()

    def fake_post_json(_path, _body):
        return {"status": "ok", "max_concurrent_tasks": 3}

    monkeypatch.setattr(worker, "post_json", fake_post_json)

    worker.heartbeat(force=True)

    assert worker.max_concurrent_tasks == 3


def test_worker_heartbeat_applies_server_paths_and_dataset_roots(monkeypatch, tmp_path):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_MAX_CONCURRENT_TASKS", "1")
    monkeypatch.setattr(
        local_worker_module,
        "collect_worker_profiles",
        lambda **_kwargs: (
            {"scheduler": {"cpu_cores": 4}},
            {"python_version": "3.11"},
            {"cpu_cores": 4, "memory_bytes": 12},
        ),
    )
    worker = CloudWorker()

    new_job_root = tmp_path / "server-jobs"
    new_data_root = tmp_path / "server-datasets"

    def fake_post_json(_path, _body):
        return {
            "status": "ok",
            "settings": {
                "max_concurrent_tasks": 2,
                "job_root": str(new_job_root),
                "dataset_roots": [
                    {
                        "path": str(new_data_root),
                        "mode": "active",
                        "label": "FastData",
                    }
                ],
            },
        }

    monkeypatch.setattr(worker, "post_json", fake_post_json)

    worker.heartbeat(force=True)

    assert worker.max_concurrent_tasks == 2
    assert worker.job_root() == new_job_root
    assert worker.dataset_root() == new_data_root
    assert worker.dataset_manager.active_root() == new_data_root
    assert worker.dataset_manager.root_specs() == [
        {
            "path": str(new_data_root),
            "mode": "active",
            "label": "FastData",
        }
    ]


def test_worker_heartbeat_reports_dataset_root_checks(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setattr(
        local_worker_module,
        "collect_worker_profiles",
        lambda **_kwargs: (
            {"scheduler": {"cpu_cores": 4}},
            {"python_version": "3.11"},
            {"cpu_cores": 4, "memory_bytes": 12},
        ),
    )
    worker = CloudWorker()
    captured = {}
    monkeypatch.setattr(
        worker.dataset_manager,
        "validate_roots",
        lambda: [
            {
                "path": "/data/fast",
                "mode": "active",
                "status": "ok",
                "readable": True,
                "writable": True,
                "free_bytes": 100,
                "cache_archive_count": 0,
                "cache_extracted_count": 0,
                "error": None,
            }
        ],
    )

    def fake_post_json(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"status": "ok"}

    monkeypatch.setattr(worker, "post_json", fake_post_json)

    worker.heartbeat(force=True)

    assert captured["path"] == "/api/worker/heartbeat"
    assert captured["body"]["dataset_root_checks"][0]["path"] == "/data/fast"
    assert captured["body"]["dataset_root_checks"][0]["status"] == "ok"


def test_post_json_uses_configured_api_timeout(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_API_TIMEOUT_SECONDS", "7")
    worker = CloudWorker()
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)

    assert worker.post_json("/api/test", {"hello": "world"}) == {"ok": True}
    assert captured["timeout"] == 7


def test_worker_claims_tasks_when_delete_request_check_times_out(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0.01")
    worker = CloudWorker()
    claim_called = threading.Event()

    def delete_request_timeout():
        raise TimeoutError("delete request check timed out")

    def fake_claim_task():
        claim_called.set()
        worker.stop_event.set()
        return None

    monkeypatch.setattr(worker, "heartbeat_loop", lambda: None)
    monkeypatch.setattr(
        worker.dataset_manager,
        "process_delete_requests",
        delete_request_timeout,
    )
    monkeypatch.setattr(worker, "claim_task", fake_claim_task)

    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    try:
        assert claim_called.wait(timeout=1)
    finally:
        worker.stop_event.set()
        thread.join(timeout=1)
    assert not thread.is_alive()


def test_worker_runs_dataset_maintenance_on_interval(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("WORKER_MAINTENANCE_INTERVAL_SECONDS", "999")
    worker = CloudWorker()
    claim_count = 0
    maintenance_count = 0

    def fake_claim_task():
        nonlocal claim_count
        claim_count += 1
        if claim_count >= 2:
            worker.stop_event.set()
        return None

    def fake_process_delete_requests():
        nonlocal maintenance_count
        maintenance_count += 1

    monkeypatch.setattr(worker, "heartbeat_loop", lambda: None)
    monkeypatch.setattr(worker, "claim_task", fake_claim_task)
    monkeypatch.setattr(
        worker.dataset_manager,
        "process_delete_requests",
        fake_process_delete_requests,
    )

    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    try:
        thread.join(timeout=1)
    finally:
        worker.stop_event.set()
        thread.join(timeout=1)

    assert not thread.is_alive()
    assert claim_count >= 2
    assert maintenance_count == 1


def test_worker_claim_loop_is_not_blocked_by_dataset_maintenance(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("WORKER_POLL_INTERVAL_SECONDS", "0.01")
    monkeypatch.setenv("WORKER_MAINTENANCE_INTERVAL_SECONDS", "0.01")
    worker = CloudWorker()
    maintenance_started = threading.Event()
    maintenance_release = threading.Event()
    claim_called = threading.Event()

    def blocking_audit():
        maintenance_started.set()
        maintenance_release.wait(timeout=1)

    def fake_claim_task():
        claim_called.set()
        worker.stop_event.set()
        return None

    monkeypatch.setattr(worker, "heartbeat_loop", lambda: None)
    monkeypatch.setattr(worker.dataset_manager, "audit_known_caches", blocking_audit)
    monkeypatch.setattr(worker.dataset_manager, "process_delete_requests", lambda: None)
    monkeypatch.setattr(worker, "claim_task", fake_claim_task)

    with worker.active_tasks_lock:
        worker.active_tasks["busy"] = threading.Thread(target=lambda: None)

    thread = threading.Thread(target=worker.run_forever)
    thread.start()
    try:
        assert maintenance_started.wait(timeout=1)
        with worker.active_tasks_lock:
            worker.active_tasks.clear()
        assert claim_called.wait(timeout=0.2)
    finally:
        maintenance_release.set()
        worker.stop_event.set()
        thread.join(timeout=1)

    assert not thread.is_alive()


def test_worker_maintenance_audits_known_dataset_caches(monkeypatch):
    configure_worker_env(monkeypatch)
    worker = CloudWorker()
    calls = []

    monkeypatch.setattr(worker.dataset_manager, "process_delete_requests", lambda: calls.append("delete"))
    monkeypatch.setattr(worker.dataset_manager, "audit_known_caches", lambda: calls.append("audit"))

    worker.process_maintenance_if_due(force=True)

    assert calls == ["audit", "delete"]


def test_worker_passes_artifact_uploader_to_script_jobs(monkeypatch):
    configure_worker_env(monkeypatch)
    worker = CloudWorker()
    captured = {}

    class FakeDatasets:
        env = {}
        records = []

    class FakeDatasetManager:
        def ensure_datasets(self, _datasets):
            return FakeDatasets()

    class FakeUploader:
        def __init__(self, api_client, **kwargs):
            captured["api_client"] = api_client
            captured["uploader_kwargs"] = kwargs

    def fake_run_script_job(payload, worker_id, task_id, **kwargs):
        captured["payload"] = payload
        captured["worker_id"] = worker_id
        captured["task_id"] = task_id
        captured["artifact_uploader"] = kwargs.get("artifact_uploader")
        return {"ok": True}, "logs"

    worker.dataset_manager = FakeDatasetManager()
    monkeypatch.setattr(local_worker_module, "ResultArtifactUploader", FakeUploader, raising=False)
    monkeypatch.setattr(local_worker_module, "run_script_job", fake_run_script_job)

    result, logs = worker.run_task(
        {
            "id": "task-a",
            "type": "script_job",
            "lease_id": "lease-a",
            "payload": {
                "script": "print('ok')",
                "expected_artifacts": [{"path": "big.csv", "title": "Big CSV"}],
            },
        }
    )

    assert result == {"ok": True}
    assert logs == "logs"
    assert isinstance(captured["artifact_uploader"], FakeUploader)
    assert captured["uploader_kwargs"] == {
        "worker_id": "worker-a",
        "task_id": "task-a",
        "lease_id": "lease-a",
        "expected_artifacts": [{"path": "big.csv", "title": "Big CSV"}],
        "manifest": None,
        "upload_retries": 6,
        "retry_base_seconds": 2,
        "retry_max_seconds": 60,
    }


def test_worker_reports_execution_timeout_with_error_code(monkeypatch):
    configure_worker_env(monkeypatch)
    worker = CloudWorker()
    reported = {}

    def fake_run_task(_task):
        raise ScriptExecutionTimeout("script exceeded timeout", timeout_seconds=1)

    def fake_report_failed(task_id, lease_id, error, logs, error_code=None):
        reported.update(
            {
                "task_id": task_id,
                "lease_id": lease_id,
                "error": error,
                "logs": logs,
                "error_code": error_code,
            }
        )

    monkeypatch.setattr(worker, "run_task", fake_run_task)
    monkeypatch.setattr(worker, "report_failed", fake_report_failed)

    worker.run_and_report_task(
        {
            "id": "task-timeout",
            "type": "script_job",
            "lease_id": "lease-timeout",
            "payload": {"script": "import time; time.sleep(2)"},
        }
    )

    assert reported["task_id"] == "task-timeout"
    assert reported["lease_id"] == "lease-timeout"
    assert reported["error_code"] == "execution_timeout"


def test_doctor_uses_safe_claim_probe_and_hides_secret(monkeypatch, capsys):
    calls = []

    class FakeApiClient:
        def __init__(self, **_kwargs):
            pass

        def get_text(self, path, **_kwargs):
            calls.append(("GET_TEXT", path, None))
            return "colo=TEST\nhttp=http/1.1\n"

        def post_json(self, path, body, **_kwargs):
            calls.append(("POST", path, body))
            if path == "/api/worker/claim":
                return {"task": None}
            return {"ok": True}

        def get_json(self, path, **_kwargs):
            calls.append(("GET", path, None))
            return {"requests": []}

    config = WorkerConfig(
        base_url="https://tasks.example.test",
        worker_secret="do-not-print",
        worker_id="worker-a",
        supported_types=["script_job"],
        api_timeout_seconds=3,
        api_retries=0,
        api_retry_base_seconds=0.01,
        api_retry_max_seconds=1,
        artifact_upload_retries=0,
        artifact_retry_base_seconds=0.01,
        artifact_retry_max_seconds=1,
        poll_interval_seconds=1,
        heartbeat_seconds=1,
        dataset_api_timeout_seconds=3,
        dataset_download_timeout_seconds=30,
        maintenance_interval_seconds=60,
        max_concurrent_tasks=1,
        reserve_cpu_cores=None,
        reserve_memory_bytes=None,
        reserve_disk_bytes=None,
        reserve_job_disk_bytes=None,
        reserve_dataset_disk_bytes=None,
        reserve_gpu_memory_bytes=None,
    )
    monkeypatch.setattr(local_worker_module, "WorkerApiClient", FakeApiClient)

    assert local_worker_module.run_doctor(config) == 0

    output = capsys.readouterr().out
    assert "do-not-print" not in output
    assert (
        "POST",
        "/api/worker/claim",
        {
            "worker_id": "worker-a",
            "supported_types": ["__cloudlink_probe_no_such_type__"],
            "active_task_count": 0,
        },
    ) in calls
