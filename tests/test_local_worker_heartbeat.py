import json
import threading
from pathlib import Path

import worker.local_worker as local_worker_module
from worker.api_client import ApiRequestError
from worker.artifact_manager import ArtifactUploadFailed
from worker.config import WorkerConfig
from worker.gpu_runtime import GpuRuntimeValidationTimeout
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


def test_worker_startup_reconciliation_preserves_outbox_executions(
    monkeypatch,
    tmp_path,
):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_HOME", str(tmp_path / "cloudlink-home"))
    worker = CloudWorker()
    completion = worker.completion_outbox_dir()
    delivery = worker.delivery_outbox_dir()
    completion.mkdir(parents=True)
    delivery.mkdir(parents=True)
    (completion / "completed.json").write_text(
        json.dumps({"task_id": "task-complete", "lease_id": "lease-complete"}),
        encoding="utf-8",
    )
    (delivery / "delivery.json").write_text(
        json.dumps({"task_id": "task-delivery", "lease_id": "lease-delivery"}),
        encoding="utf-8",
    )
    captured = {}

    def fake_post_json(path, body):
        captured["path"] = path
        captured["body"] = body
        return {"status": "ok", "reconciled": []}

    monkeypatch.setattr(worker, "post_json", fake_post_json)

    worker.reconcile_startup_executions()

    assert captured == {
        "path": "/api/worker/executions/reconcile",
        "body": {
            "worker_id": "worker-a",
            "active_executions": {
                "task-complete": "lease-complete",
                "task-delivery": "lease-delivery",
            },
        },
    }


def test_periodic_gpu_validation_timeout_retains_verified_profile(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_GPU_ENABLED", "1")
    monkeypatch.setenv("CLOUDLINK_GPU_ENVIRONMENT_PATH", "/opt/gpu-env")
    monkeypatch.setenv("CLOUDLINK_MICROMAMBA_EXE", "/opt/micromamba")
    verified = {
        "enabled": True,
        "verified": True,
        "runtime": "pytorch-cuda",
        "torch_version": "2.12.1",
    }
    calls = iter(
        [
            verified,
            GpuRuntimeValidationTimeout(
                "GPU environment validation timed out after 120 seconds"
            ),
        ]
    )

    def validate(*_args, **_kwargs):
        result = next(calls)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(local_worker_module, "validate_gpu_runtime", validate)
    monkeypatch.setattr(
        local_worker_module,
        "collect_worker_profiles",
        lambda **kwargs: (
            {"scheduler": {"gpu_devices": []}},
            {"gpu_runtime": kwargs["gpu_runtime_profile"]},
            {"gpu_devices": []},
        ),
    )

    worker = CloudWorker()
    worker.last_gpu_validation_at = 0
    worker.refresh_worker_profiles()

    assert worker.gpu_runtime_profile["verified"] is True
    assert worker.gpu_runtime_profile["validation_stale"] is True
    assert "timed out after 120 seconds" in (
        worker.gpu_runtime_profile["validation_warning"]
    )
    assert worker.runtime_profile["gpu_runtime"]["torch_version"] == "2.12.1"


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
    monkeypatch.setattr(worker, "reconcile_startup_executions", lambda: {})
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
    monkeypatch.setattr(worker, "reconcile_startup_executions", lambda: {})
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
    monkeypatch.setattr(worker, "reconcile_startup_executions", lambda: {})
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
        "upload_timeout_seconds": 300,
        "retry_base_seconds": 2,
        "retry_max_seconds": 60,
    }


def test_worker_reports_execution_timeout_with_error_code(monkeypatch):
    configure_worker_env(monkeypatch)
    worker = CloudWorker()
    reported = {}

    def fake_run_task(_task, _cancel_event):
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


def test_success_report_timeout_preserves_and_retries_result(monkeypatch, tmp_path):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_HOME", str(tmp_path / "cloudlink-home"))
    monkeypatch.setenv("WORKER_API_RETRY_BASE_SECONDS", "0.001")
    worker = CloudWorker()
    calls = []
    reported_failed = []

    monkeypatch.setattr(
        worker,
        "run_task",
        lambda _task, _cancel_event: ({"prediction": [1, 2, 3]}, "done"),
    )

    def flaky_report(task_id, lease_id, result, logs):
        calls.append((task_id, lease_id, result, logs))
        if len(calls) == 1:
            raise ApiRequestError(
                "response lost",
                method="POST",
                path=f"/api/worker/tasks/{task_id}/success",
                attempt=4,
                elapsed_seconds=1,
                original=TimeoutError("response lost"),
            )

    monkeypatch.setattr(worker, "report_success", flaky_report)
    monkeypatch.setattr(
        worker,
        "report_failed",
        lambda *args, **kwargs: reported_failed.append((args, kwargs)),
    )

    worker.run_and_report_task(
        {
            "id": "task-result",
            "type": "script_job",
            "lease_id": "lease-result",
            "payload": {"script": "print('ok')"},
        }
    )

    assert len(calls) == 2
    assert reported_failed == []
    assert not (worker.completion_outbox_dir() / "task-result.json").exists()


def test_replayed_success_resumes_disconnected_execution(monkeypatch, tmp_path):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_HOME", str(tmp_path / "cloudlink-home"))
    worker = CloudWorker()
    outbox_path = worker.persist_completion(
        "task-result",
        "lease-result",
        {"prediction": [1, 2, 3]},
        "done",
    )
    reports = []
    resumes = []

    def report(task_id, lease_id, result, logs):
        reports.append((task_id, lease_id, result, logs))
        if len(reports) == 1:
            raise ApiRequestError(
                "execution disconnected",
                method="POST",
                path=f"/api/worker/tasks/{task_id}/success",
                attempt=1,
                elapsed_seconds=0.1,
                status_code=409,
                response_body=json.dumps(
                    {
                        "detail": {
                            "code": "execution_disconnected",
                            "message": "execution is waiting for reconnect",
                        }
                    }
                ),
            )

    monkeypatch.setattr(worker, "report_success", report)
    monkeypatch.setattr(
        worker,
        "resume_execution_lease",
        lambda task_id, lease_id: resumes.append((task_id, lease_id)) or {},
    )

    worker.replay_completion_outbox()

    assert len(reports) == 2
    assert resumes == [("task-result", "lease-result")]
    assert not outbox_path.exists()


def test_failure_report_resumes_disconnected_execution(monkeypatch):
    configure_worker_env(monkeypatch)
    worker = CloudWorker()
    reports = []
    resumes = []

    def report(task_id, lease_id, error, logs, error_code=None):
        reports.append((task_id, lease_id, error, logs, error_code))
        if len(reports) == 1:
            raise ApiRequestError(
                "execution disconnected",
                method="POST",
                path=f"/api/worker/tasks/{task_id}/failed",
                attempt=1,
                elapsed_seconds=0.1,
                status_code=409,
                response_body=json.dumps(
                    {
                        "detail": {
                            "code": "execution_disconnected",
                            "message": "execution is waiting for reconnect",
                        }
                    }
                ),
            )

    monkeypatch.setattr(worker, "report_failed", report)
    monkeypatch.setattr(
        worker,
        "resume_execution_lease",
        lambda task_id, lease_id: resumes.append((task_id, lease_id)) or {},
    )

    worker.report_failed_after_reconnect(
        "task-result",
        "lease-result",
        "process failed",
        "traceback",
        error_code="execution_failed",
    )

    assert len(reports) == 2
    assert resumes == [("task-result", "lease-result")]


def test_artifact_upload_failure_pauses_delivery_without_reporting_task_failed(
    monkeypatch,
    tmp_path,
):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_HOME", str(tmp_path / "cloudlink-home"))
    worker = CloudWorker()
    task = {
        "id": "task-delivery",
        "type": "script_job",
        "lease_id": "lease-delivery",
        "payload": {"script": "print('ok')"},
    }
    output_dir = tmp_path / "job" / "outputs"
    output_dir.mkdir(parents=True)
    worker.persist_delivery(
        task,
        {
            "worker_id": "worker-a",
            "runtime": "python-auto",
            "exit_code": 0,
            "job_dir": str(output_dir.parent),
            "stdout": "ok\n",
            "stderr": "",
            "datasets": [],
        },
        "done",
        output_dir,
        {},
    )
    reported_failed = []
    resumed = []

    monkeypatch.setattr(
        worker,
        "run_task",
        lambda _task, _cancel_event: (_ for _ in ()).throw(
            ArtifactUploadFailed("offline")
        ),
    )
    monkeypatch.setattr(
        worker,
        "deliver_preserved_result",
        lambda item: resumed.append(item),
    )
    monkeypatch.setattr(
        worker,
        "report_failed",
        lambda *args, **kwargs: reported_failed.append((args, kwargs)),
    )

    worker.run_and_report_task(task)

    assert len(resumed) == 1
    assert resumed[0]["task_id"] == "task-delivery"
    assert reported_failed == []


def test_delivery_replay_does_not_consume_execution_slot(monkeypatch, tmp_path):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_HOME", str(tmp_path / "cloudlink-home"))
    worker = CloudWorker()
    outbox = worker.delivery_outbox_dir()
    outbox.mkdir(parents=True)
    path = outbox / "task-delivery.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "task-delivery",
                "lease_id": "lease-old",
                "base_result": {},
                "logs": "",
                "output_dir": str(tmp_path / "outputs"),
            }
        ),
        encoding="utf-8",
    )
    delivery_started = threading.Event()
    release_delivery = threading.Event()

    monkeypatch.setattr(worker, "resume_delivery_lease", lambda item: item)
    monkeypatch.setattr(
        worker,
        "task_lease_loop",
        lambda _task_id, _lease_id, _cancel_event, stop_event: stop_event.wait(),
    )

    def wait_for_release(_item):
        delivery_started.set()
        assert release_delivery.wait(timeout=1)

    monkeypatch.setattr(worker, "deliver_preserved_result", wait_for_release)

    worker.replay_delivery_outbox()

    assert delivery_started.wait(timeout=1)
    assert worker.active_task_count() == 0
    assert list(worker.delivery_threads) == ["task-delivery"]

    release_delivery.set()
    worker.join_delivery_threads(timeout=1)
    assert worker.delivery_threads == {}


def test_delivery_replay_reacquires_lease_after_artifact_lease_conflict(
    monkeypatch,
    tmp_path,
):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_HOME", str(tmp_path / "cloudlink-home"))
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS", "0.001")
    worker = CloudWorker()
    outbox = worker.delivery_outbox_dir()
    outbox.mkdir(parents=True)
    path = outbox / "task-delivery.json"
    path.write_text(
        json.dumps(
            {
                "task_id": "task-delivery",
                "lease_id": "lease-old",
                "base_result": {},
                "logs": "",
                "output_dir": str(tmp_path / "outputs"),
            }
        ),
        encoding="utf-8",
    )
    resumed_leases = []
    delivery_leases = []

    def resume(item):
        item["lease_id"] = f"lease-{len(resumed_leases) + 1}"
        resumed_leases.append(item["lease_id"])
        return item

    def deliver(item):
        delivery_leases.append(item["lease_id"])
        if len(delivery_leases) == 1:
            raise local_worker_module.DeliveryLeaseLost("lease expired")

    monkeypatch.setattr(worker, "resume_delivery_lease", resume)
    monkeypatch.setattr(worker, "deliver_preserved_result", deliver)
    monkeypatch.setattr(
        worker,
        "task_lease_loop",
        lambda _task_id, _lease_id, _cancel_event, stop_event: stop_event.wait(),
    )

    worker.replay_delivery_item(path)

    assert resumed_leases == ["lease-1", "lease-2"]
    assert delivery_leases == ["lease-1", "lease-2"]


def test_artifact_task_lease_conflict_is_detected_through_wrapped_error():
    api_error = ApiRequestError(
        "artifact rejected",
        method="POST",
        path="/api/worker/tasks/task-delivery/artifacts",
        attempt=1,
        elapsed_seconds=0.1,
        status_code=409,
        response_body='{"detail":"Task lease does not match"}',
    )
    try:
        raise api_error
    except ApiRequestError as exc:
        wrapped = ArtifactUploadFailed("artifact upload failed")
        wrapped.__cause__ = exc

    assert local_worker_module.delivery_lease_was_lost(wrapped)


def test_artifact_metadata_conflict_is_not_mistaken_for_lost_lease():
    error = ApiRequestError(
        "artifact rejected",
        method="POST",
        path="/api/worker/tasks/task-delivery/artifacts",
        attempt=1,
        elapsed_seconds=0.1,
        status_code=409,
        response_body='{"detail":"UNIQUE constraint failed: task_artifacts.task_id"}',
    )

    assert not local_worker_module.delivery_lease_was_lost(error)


def test_task_lease_loop_delivers_cancel_request(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_TASK_LEASE_RENEW_SECONDS", "0.01")
    worker = CloudWorker()
    cancel_event = threading.Event()
    stop_event = threading.Event()
    calls = []

    def fake_post_json(path, body):
        calls.append((path, body))
        return {"cancel_requested": True, "cancel_reason": "stop model run"}

    monkeypatch.setattr(worker, "post_json", fake_post_json)
    thread = threading.Thread(
        target=worker.task_lease_loop,
        args=("task-a", "lease-a", cancel_event, stop_event),
    )
    thread.start()

    assert cancel_event.wait(timeout=1)
    stop_event.set()
    thread.join(timeout=1)
    assert not thread.is_alive()
    assert calls[0] == (
        "/api/worker/tasks/task-a/lease",
        {"worker_id": "worker-a", "lease_id": "lease-a"},
    )


def test_task_lease_loop_resumes_after_server_marks_execution_disconnected(
    monkeypatch,
):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_TASK_LEASE_RENEW_SECONDS", "0.01")
    worker = CloudWorker()
    cancel_event = threading.Event()
    stop_event = threading.Event()
    calls = []

    def fake_post_json(path, body):
        calls.append((path, body))
        if path.endswith("/lease"):
            raise ApiRequestError(
                "execution disconnected",
                method="POST",
                path=path,
                attempt=1,
                elapsed_seconds=0.1,
                status_code=409,
                response_body=json.dumps(
                    {
                        "detail": {
                            "code": "execution_disconnected",
                            "message": "execution is waiting for reconnect",
                        }
                    }
                ),
            )
        stop_event.set()
        return {"cancel_requested": False}

    monkeypatch.setattr(worker, "post_json", fake_post_json)
    thread = threading.Thread(
        target=worker.task_lease_loop,
        args=("task-a", "lease-a", cancel_event, stop_event),
    )
    thread.start()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert not cancel_event.is_set()
    assert calls == [
        (
            "/api/worker/tasks/task-a/lease",
            {"worker_id": "worker-a", "lease_id": "lease-a"},
        ),
        (
            "/api/worker/tasks/task-a/execution/resume",
            {"worker_id": "worker-a", "lease_id": "lease-a"},
        ),
    ]


def test_task_lease_loop_stops_when_execution_resume_is_denied(monkeypatch):
    configure_worker_env(monkeypatch)
    monkeypatch.setenv("CLOUDLINK_TASK_LEASE_RENEW_SECONDS", "0.01")
    worker = CloudWorker()
    cancel_event = threading.Event()
    stop_event = threading.Event()

    def fake_post_json(path, _body):
        code = (
            "execution_disconnected"
            if path.endswith("/lease")
            else "task_lease_mismatch"
        )
        raise ApiRequestError(
            code,
            method="POST",
            path=path,
            attempt=1,
            elapsed_seconds=0.1,
            status_code=409,
            response_body=json.dumps(
                {"detail": {"code": code, "message": code}}
            ),
        )

    monkeypatch.setattr(worker, "post_json", fake_post_json)
    thread = threading.Thread(
        target=worker.task_lease_loop,
        args=("task-a", "lease-a", cancel_event, stop_event),
    )
    thread.start()

    assert cancel_event.wait(timeout=1)
    thread.join(timeout=1)
    assert not thread.is_alive()


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
        result_report_timeout_seconds=30,
        artifact_upload_retries=0,
        artifact_upload_timeout_seconds=30,
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
