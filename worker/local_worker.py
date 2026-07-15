import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time
import traceback
from datetime import date
from typing import Any, Dict, List, Optional, Tuple
import urllib.parse

try:
    from worker.api_client import ApiRequestError, WorkerApiClient
    from worker.artifact_manager import ResultArtifactUploader
    from worker.config import WorkerConfig, WorkerConfigError, load_worker_config
    from worker.dataset_manager import DatasetManager
    from worker.hardware import collect_worker_profiles
    from worker.script_runner import ScriptExecutionTimeout, run_script_job
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from api_client import ApiRequestError, WorkerApiClient
    from artifact_manager import ResultArtifactUploader
    from config import WorkerConfig, WorkerConfigError, load_worker_config
    from dataset_manager import DatasetManager
    from hardware import collect_worker_profiles
    from script_runner import ScriptExecutionTimeout, run_script_job


class CloudWorker:
    def __init__(
        self,
        config: Optional[WorkerConfig] = None,
        *,
        api_client: Optional[WorkerApiClient] = None,
        dataset_manager: Optional[DatasetManager] = None,
    ) -> None:
        self.config = config or load_worker_config()
        self.base_url = self.config.base_url
        self.worker_secret = self.config.worker_secret
        self.worker_id = self.config.worker_id
        self.api_timeout = self.config.api_timeout_seconds
        self.poll_interval = self.config.poll_interval_seconds
        self.heartbeat_interval = self.config.heartbeat_seconds
        self.maintenance_interval = self.config.maintenance_interval_seconds
        self.max_concurrent_tasks = self.config.max_concurrent_tasks
        self.supported_types = self.config.supported_types
        self.last_heartbeat_at = 0.0
        self.last_maintenance_at = 0.0
        self.last_error: Optional[str] = None
        self.stop_event = threading.Event()
        self.active_tasks: Dict[str, threading.Thread] = {}
        self.active_tasks_lock = threading.Lock()
        self.maintenance_thread: Optional[threading.Thread] = None
        self.maintenance_lock = threading.Lock()
        self.hardware_profile: Dict[str, Any] = {}
        self.runtime_profile: Dict[str, Any] = {}
        self.capacity_state: Dict[str, Any] = {}
        self.server_reserve_overrides: Dict[str, Any] = {}
        self.current_job_root = self.default_job_root()
        self.api_client = api_client or WorkerApiClient(
            base_url=self.config.base_url,
            worker_secret=self.config.worker_secret,
            default_timeout=self.config.api_timeout_seconds,
            default_retries=self.config.api_retries,
            retry_base_seconds=self.config.api_retry_base_seconds,
            retry_max_seconds=self.config.api_retry_max_seconds,
            log=self.log,
        )
        self.dataset_manager = dataset_manager or DatasetManager(
            self.api_client,
            self.config.worker_id,
            api_timeout_seconds=self.config.dataset_api_timeout_seconds,
            download_timeout_seconds=self.config.dataset_download_timeout_seconds,
            download_retries=self.config.api_retries,
        )
        self.refresh_worker_profiles()

    def log(self, message: str) -> None:
        print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

    def post_json(self, path: str, body: Dict[str, Any]) -> Dict[str, Any]:
        return self.api_client.post_json(path, body)

    def reserve_overrides(self) -> Dict[str, Any]:
        overrides: Dict[str, Any] = {}
        if self.config.reserve_cpu_cores is not None:
            overrides["cpu_cores"] = self.config.reserve_cpu_cores
        if self.config.reserve_memory_bytes is not None:
            overrides["memory_bytes"] = self.config.reserve_memory_bytes
        if self.config.reserve_disk_bytes is not None:
            overrides["disk_bytes"] = self.config.reserve_disk_bytes
        if self.config.reserve_job_disk_bytes is not None:
            overrides["job_disk_bytes"] = self.config.reserve_job_disk_bytes
        if self.config.reserve_dataset_disk_bytes is not None:
            overrides["dataset_disk_bytes"] = self.config.reserve_dataset_disk_bytes
        if self.config.reserve_gpu_memory_bytes is not None:
            overrides["gpu_memory_bytes"] = self.config.reserve_gpu_memory_bytes
        overrides.update(self.server_reserve_overrides)
        return overrides

    def default_job_root(self) -> Path:
        return Path(
            os.getenv(
                "CLOUDLINK_JOB_ROOT",
                str(Path(os.getenv("CLOUDLINK_HOME", "~/.cloudlink")).expanduser() / "jobs"),
            )
        ).expanduser()

    def job_root(self) -> Path:
        return self.current_job_root

    def dataset_root(self) -> Path:
        if hasattr(self, "dataset_manager"):
            return self.dataset_manager.active_root()
        return Path(
            os.getenv(
                "CLOUDLINK_DATASET_ROOT",
                str(Path(os.getenv("CLOUDLINK_HOME", "~/.cloudlink")).expanduser() / "datasets"),
            )
        ).expanduser()

    def python_runtime(self) -> Path:
        return Path(
            os.getenv(
                "CLOUDLINK_PYTHON_AUTO_VENV",
                str(Path(os.getenv("CLOUDLINK_HOME", "~/.cloudlink")).expanduser() / "venvs/python-auto"),
            )
        ).expanduser()

    def refresh_worker_profiles(self) -> None:
        (
            self.hardware_profile,
            self.runtime_profile,
            self.capacity_state,
        ) = collect_worker_profiles(
            job_root=self.job_root(),
            dataset_root=self.dataset_root(),
            dataset_roots=self.dataset_manager.root_specs(),
            worker_id=self.worker_id,
            python_runtime=self.python_runtime(),
            reserve_overrides=self.reserve_overrides(),
        )

    def active_task_count(self) -> int:
        with self.active_tasks_lock:
            return len(self.active_tasks)

    def active_task_ids(self) -> List[str]:
        with self.active_tasks_lock:
            return sorted(self.active_tasks)

    def claim_task(self) -> Optional[Dict[str, Any]]:
        self.refresh_worker_profiles()
        response = self.post_json(
            "/api/worker/claim",
            {
                "worker_id": self.worker_id,
                "supported_types": self.supported_types,
                "capacity_state": self.capacity_state,
                "active_task_count": self.active_task_count(),
            },
        )
        return response.get("task")

    def heartbeat(self, last_error: Optional[str] = None, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_heartbeat_at < self.heartbeat_interval:
            return
        self.refresh_worker_profiles()
        dataset_root_checks = self.dataset_root_checks_best_effort()
        response = self.post_json(
            "/api/worker/heartbeat",
            {
                "worker_id": self.worker_id,
                "supported_types": self.supported_types,
                "last_error": last_error,
                "hardware_profile": self.hardware_profile,
                "runtime_profile": self.runtime_profile,
                "capacity_state": self.capacity_state,
                "dataset_root_checks": dataset_root_checks,
                "max_concurrent_tasks": self.max_concurrent_tasks,
                "active_task_count": self.active_task_count(),
            },
        )
        self.apply_server_settings(response)
        self.last_heartbeat_at = now

    def apply_server_settings(self, response: Dict[str, Any]) -> None:
        settings = response.get("settings") or {}
        server_concurrency = settings.get("max_concurrent_tasks", response.get("max_concurrent_tasks"))
        if server_concurrency is not None:
            try:
                parsed = int(server_concurrency)
            except (TypeError, ValueError):
                parsed = self.max_concurrent_tasks
            if parsed >= 1:
                self.max_concurrent_tasks = parsed
        job_root = str(settings.get("job_root") or "").strip()
        if job_root:
            self.current_job_root = Path(job_root).expanduser()
            os.environ["CLOUDLINK_JOB_ROOT"] = str(self.current_job_root)
        dataset_roots = settings.get("dataset_roots")
        if isinstance(dataset_roots, list):
            self.dataset_manager.set_roots(dataset_roots)
            os.environ["CLOUDLINK_DATASET_ROOT"] = str(self.dataset_manager.active_root())
        reserve_overrides = settings.get("reserve_overrides")
        if isinstance(reserve_overrides, dict):
            self.server_reserve_overrides = reserve_overrides

    def dataset_root_checks_best_effort(self) -> List[Dict[str, Any]]:
        try:
            return self.dataset_manager.validate_roots()
        except Exception as exc:
            self.last_error = str(exc)
            self.log(f"dataset root validation error: {exc}; continuing")
            return []

    def heartbeat_loop(self) -> None:
        while not self.stop_event.is_set():
            try:
                self.heartbeat(last_error=self.last_error, force=True)
                self.last_error = None
            except (ApiRequestError, TimeoutError, json.JSONDecodeError) as exc:
                self.last_error = str(exc)
                self.log(f"heartbeat error: {exc}; will retry")
            except Exception as exc:
                self.last_error = str(exc)
                self.log(f"heartbeat loop error: {exc}; will retry")
            self.stop_event.wait(self.heartbeat_interval)

    def report_success(
        self,
        task_id: str,
        lease_id: str,
        result: Dict[str, Any],
        logs: str,
    ) -> None:
        self.post_json(
            f"/api/worker/tasks/{task_id}/success",
            {
                "worker_id": self.worker_id,
                "lease_id": lease_id,
                "result": result,
                "logs": logs,
            },
        )

    def report_failed(
        self,
        task_id: str,
        lease_id: str,
        error: str,
        logs: str,
        error_code: Optional[str] = None,
    ) -> None:
        body = {
            "worker_id": self.worker_id,
            "lease_id": lease_id,
            "error": error,
            "logs": logs,
        }
        if error_code:
            body["error_code"] = error_code
        self.post_json(f"/api/worker/tasks/{task_id}/failed", body)

    def run_task(self, task: Dict[str, Any]) -> Tuple[Dict[str, Any], str]:
        task_type = task["type"]
        payload = task["payload"]
        if task_type == "echo_test":
            return run_echo_test(payload, self.worker_id)
        if task_type == "generate_daily_report":
            return run_generate_daily_report(payload, self.worker_id)
        if task_type == "script_job":
            datasets = self.dataset_manager.ensure_datasets(payload.get("datasets", []))
            artifact_uploader = ResultArtifactUploader(
                self.api_client,
                worker_id=self.worker_id,
                task_id=task["id"],
                lease_id=task["lease_id"],
                expected_artifacts=payload.get("expected_artifacts", []),
                manifest=None,
                upload_retries=self.config.artifact_upload_retries,
                retry_base_seconds=self.config.artifact_retry_base_seconds,
                retry_max_seconds=self.config.artifact_retry_max_seconds,
            )
            return run_script_job(
                payload,
                self.worker_id,
                task["id"],
                dataset_env=datasets.env,
                dataset_records=datasets.records,
                artifact_uploader=artifact_uploader,
            )
        raise ValueError(f"Unsupported task type: {task_type}")

    def process_delete_requests_best_effort(self) -> None:
        try:
            self.dataset_manager.process_delete_requests()
        except (ApiRequestError, TimeoutError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            self.log(f"dataset delete request check failed: {exc}; continuing")
        except Exception as exc:
            self.last_error = str(exc)
            self.log(f"dataset delete request check error: {exc}; continuing")

    def audit_dataset_caches_best_effort(self) -> None:
        try:
            self.dataset_manager.audit_known_caches()
        except (ApiRequestError, TimeoutError, json.JSONDecodeError) as exc:
            self.last_error = str(exc)
            self.log(f"dataset cache audit failed: {exc}; continuing")
        except Exception as exc:
            self.last_error = str(exc)
            self.log(f"dataset cache audit error: {exc}; continuing")

    def run_dataset_maintenance(self) -> None:
        self.audit_dataset_caches_best_effort()
        self.process_delete_requests_best_effort()

    def process_maintenance_if_due(self, *, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_maintenance_at < self.maintenance_interval:
            return
        if force:
            self.last_maintenance_at = now
            self.run_dataset_maintenance()
            return

        with self.maintenance_lock:
            if self.maintenance_thread and self.maintenance_thread.is_alive():
                return
            self.last_maintenance_at = now
            self.maintenance_thread = threading.Thread(
                target=self.run_dataset_maintenance,
                name="cloudlink-maintenance",
                daemon=True,
            )
            self.maintenance_thread.start()

    def run_and_report_task(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        lease_id = task["lease_id"]
        try:
            result, logs = self.run_task(task)
            self.report_success(task_id, lease_id, result, logs)
            self.log(f"reported success for task {task_id}")
        except ScriptExecutionTimeout as exc:
            logs = traceback.format_exc()
            self.log(f"task {task_id} execution timed out: {exc}")
            try:
                self.report_failed(
                    task_id,
                    lease_id,
                    str(exc),
                    logs,
                    error_code=exc.error_code,
                )
            except Exception as report_exc:
                self.log(f"failed to report task timeout for {task_id}: {report_exc}")
        except Exception as exc:
            logs = traceback.format_exc()
            self.log(f"task {task_id} failed: {exc}")
            try:
                self.report_failed(task_id, lease_id, str(exc), logs)
            except Exception as report_exc:
                self.log(f"failed to report task failure for {task_id}: {report_exc}")
        finally:
            with self.active_tasks_lock:
                self.active_tasks.pop(task_id, None)

    def start_task_thread(self, task: Dict[str, Any]) -> None:
        task_id = task["id"]
        thread = threading.Thread(
            target=self.run_and_report_task,
            args=(task,),
            name=f"cloudlink-task-{task_id[:8]}",
            daemon=True,
        )
        with self.active_tasks_lock:
            self.active_tasks[task_id] = thread
        thread.start()

    def join_active_tasks(self, timeout: float = 5) -> None:
        with self.active_tasks_lock:
            threads = list(self.active_tasks.values())
        for thread in threads:
            thread.join(timeout=timeout)

    def join_maintenance(self, timeout: float = 2) -> None:
        with self.maintenance_lock:
            thread = self.maintenance_thread
        if thread and thread.is_alive():
            thread.join(timeout=timeout)

    def run_forever(self) -> None:
        self.log(
            f"worker {self.worker_id} started; supported_types={self.supported_types}"
        )
        heartbeat_thread = threading.Thread(
            target=self.heartbeat_loop,
            name="cloudlink-heartbeat",
            daemon=True,
        )
        heartbeat_thread.start()
        try:
            while not self.stop_event.is_set():
                try:
                    claimed_any = False
                    while self.active_task_count() < self.max_concurrent_tasks:
                        task = self.claim_task()
                        if task is None:
                            break

                        task_id = task["id"]
                        self.log(f"claimed task {task_id} type={task['type']}")
                        self.start_task_thread(task)
                        claimed_any = True

                    if self.stop_event.is_set():
                        break

                    if not claimed_any:
                        self.process_maintenance_if_due()
                        self.stop_event.wait(self.poll_interval)
                        continue

                    self.stop_event.wait(self.poll_interval)
                except KeyboardInterrupt:
                    self.log("received Ctrl+C, exiting")
                    return
                except (ApiRequestError, TimeoutError, json.JSONDecodeError) as exc:
                    self.last_error = str(exc)
                    self.log(f"network/API error: {exc}; retrying after sleep")
                    self.stop_event.wait(self.poll_interval)
                except Exception as exc:
                    self.last_error = str(exc)
                    self.log(f"worker loop error: {exc}; retrying after sleep")
                    self.stop_event.wait(self.poll_interval)
        finally:
            self.stop_event.set()
            heartbeat_thread.join(timeout=2)
            self.join_maintenance(timeout=2)
            self.join_active_tasks(timeout=2)


def run_echo_test(payload: Dict[str, Any], worker_id: str) -> Tuple[Dict[str, Any], str]:
    message = payload.get("message", "")
    result = {"echo": message, "worker_id": worker_id}
    logs = f"echo_test completed; message_length={len(str(message))}"
    return result, logs


def run_generate_daily_report(
    payload: Dict[str, Any],
    worker_id: str,
) -> Tuple[Dict[str, Any], str]:
    topic = payload.get("topic", "未指定主题")
    report_date = payload.get("date", date.today().isoformat())
    purpose = payload.get("purpose", "未指定用途")
    text = (
        f"日报 Mock 报告\n"
        f"日期：{report_date}\n"
        f"主题：{topic}\n"
        f"用途：{purpose}\n"
        f"执行 worker：{worker_id}\n"
        "当前版本返回 mock 内容，后续可在此函数中接入真实报告生成逻辑。"
    )
    return {
        "summary": f"{report_date} {topic} mock report",
        "text": text,
        "output_files": [],
        "worker_id": worker_id,
    }, "generate_daily_report mock completed"


def print_safe_config(config: WorkerConfig) -> None:
    print("Cloudlink local worker config", flush=True)
    print(f"  API: {config.base_url}", flush=True)
    print(f"  Worker: {config.worker_id}", flush=True)
    print(f"  Types: {', '.join(config.supported_types)}", flush=True)
    print(f"  API timeout: {config.api_timeout_seconds:g}s", flush=True)
    print(f"  API retries: {config.api_retries}", flush=True)
    print(
        f"  API retry backoff: base={config.api_retry_base_seconds:g}s "
        f"max={config.api_retry_max_seconds:g}s",
        flush=True,
    )
    print(f"  Artifact upload retries: {config.artifact_upload_retries}", flush=True)
    print(
        f"  Artifact retry backoff: base={config.artifact_retry_base_seconds:g}s "
        f"max={config.artifact_retry_max_seconds:g}s",
        flush=True,
    )
    print(f"  Poll interval: {config.poll_interval_seconds:g}s", flush=True)
    print(f"  Heartbeat interval: {config.heartbeat_seconds:g}s", flush=True)
    print(f"  Maintenance interval: {config.maintenance_interval_seconds:g}s", flush=True)
    print(f"  Max concurrent tasks: {config.max_concurrent_tasks}", flush=True)
    print(
        "  Resource reserve: "
        f"cpu={config.reserve_cpu_cores if config.reserve_cpu_cores is not None else 'auto'}, "
        f"memory={config.reserve_memory_bytes if config.reserve_memory_bytes is not None else 'auto'} bytes, "
        f"disk={config.reserve_disk_bytes if config.reserve_disk_bytes is not None else 'auto'} bytes, "
        f"job_disk={config.reserve_job_disk_bytes if config.reserve_job_disk_bytes is not None else 'auto'} bytes, "
        f"dataset_disk={config.reserve_dataset_disk_bytes if config.reserve_dataset_disk_bytes is not None else 'auto'} bytes",
        flush=True,
    )
    print(
        f"  Dataset API timeout: {config.dataset_api_timeout_seconds:g}s",
        flush=True,
    )
    print(
        f"  Dataset download timeout: {config.dataset_download_timeout_seconds:g}s",
        flush=True,
    )
    print("  Worker secret: configured", flush=True)


def run_doctor(config: Optional[WorkerConfig] = None) -> int:
    cfg = config or load_worker_config()
    print_safe_config(cfg)
    client = WorkerApiClient(
        base_url=cfg.base_url,
        worker_secret=cfg.worker_secret,
        default_timeout=cfg.api_timeout_seconds,
        default_retries=cfg.api_retries,
        retry_base_seconds=cfg.api_retry_base_seconds,
        retry_max_seconds=cfg.api_retry_max_seconds,
        log=lambda message: print(f"  {message}", flush=True),
    )
    checks = [
        (
            "public_https",
            lambda: client.get_text(
                "/cdn-cgi/trace",
                auth=False,
                timeout=cfg.api_timeout_seconds,
                retries=cfg.api_retries,
            ),
        ),
        (
            "worker_heartbeat",
            lambda: client.post_json(
                "/api/worker/heartbeat",
                {
                    "worker_id": cfg.worker_id,
                    "supported_types": cfg.supported_types,
                    "last_error": "doctor probe",
                    "max_concurrent_tasks": cfg.max_concurrent_tasks,
                    "active_task_count": 0,
                },
                timeout=cfg.api_timeout_seconds,
                retries=cfg.api_retries,
            ),
        ),
        (
            "dataset_delete_requests",
            lambda: client.get_json(
                "/api/worker/datasets/delete-requests"
                f"?worker_id={urllib.parse.quote(cfg.worker_id)}",
                timeout=cfg.dataset_api_timeout_seconds,
                retries=cfg.api_retries,
            ),
        ),
        (
            "safe_claim_probe",
            lambda: client.post_json(
                "/api/worker/claim",
                {
                    "worker_id": cfg.worker_id,
                    "supported_types": ["__cloudlink_probe_no_such_type__"],
                    "active_task_count": 0,
                },
                timeout=cfg.api_timeout_seconds,
                retries=cfg.api_retries,
            ),
        ),
    ]

    failures = 0
    print("Cloudlink local worker doctor", flush=True)
    for name, check in checks:
        started = time.perf_counter()
        try:
            result = check()
            elapsed = time.perf_counter() - started
            detail = summarize_probe_result(result)
            suffix = f" ({detail})" if detail else ""
            print(f"  ok   {name} elapsed={elapsed:.2f}s{suffix}", flush=True)
        except Exception as exc:
            failures += 1
            elapsed = time.perf_counter() - started
            print(
                f"  fail {name} elapsed={elapsed:.2f}s error={exc}",
                flush=True,
            )
    return 1 if failures else 0


def summarize_probe_result(result: Any) -> str:
    if isinstance(result, str):
        lines = [line for line in result.splitlines() if line.strip()]
        sample = ", ".join(lines[:2])
        return sample[:120]
    if isinstance(result, dict):
        if "task" in result:
            return "no task claimed" if result.get("task") is None else "unexpected task"
        if "requests" in result:
            return f"delete_requests={len(result.get('requests') or [])}"
        if result.get("ok") is True:
            return "ok=true"
    return ""


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Cloudlink local worker")
    parser.add_argument(
        "command",
        nargs="?",
        default="start",
        choices=("start", "doctor", "print-config"),
    )
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> None:
    try:
        if sys.platform.startswith("win"):
            print(
                "Cloudlink no longer supports native Windows workers. "
                "Install WSL and register the node with the Linux installer.",
                flush=True,
            )
            raise SystemExit(2)
        args = parse_args(argv)
        if args.command == "print-config":
            print_safe_config(load_worker_config())
            return
        if args.command == "doctor":
            raise SystemExit(run_doctor())
        CloudWorker().run_forever()
    except WorkerConfigError as exc:
        print(f"Configuration error: {exc}", flush=True)
        raise SystemExit(2) from exc


if __name__ == "__main__":
    main(sys.argv[1:])
