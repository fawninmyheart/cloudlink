#!/usr/bin/env python3
import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from cloudlink_client import CloudlinkAuthError, request_json  # noqa: E402


TERMINAL_STATUSES = {"success", "failed", "timeout", "cancelled"}


def read_script(args: argparse.Namespace) -> str:
    if args.script_file:
        return open(args.script_file, "r", encoding="utf-8").read()
    if args.script:
        return args.script
    if not sys.stdin.isatty():
        return sys.stdin.read()
    raise SystemExit("Provide --script, --script-file, or stdin")


def load_input_files(items: List[str]) -> List[Dict[str, str]]:
    files = []
    for item in items:
        if "=" not in item:
            raise SystemExit("--input-file must be in job/path=local/path form")
        job_path, local_path = item.split("=", 1)
        with open(local_path, "r", encoding="utf-8") as file:
            files.append({"path": job_path, "content": file.read()})
    return files


def load_json_file(path: Optional[str]) -> Dict[str, Any]:
    if not path:
        return {}
    with open(path, "r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, dict):
        raise SystemExit("--task-context-file must contain a JSON object")
    return data


def gb_to_bytes(value: Optional[float]) -> Optional[int]:
    if value is None:
        return None
    if value < 0:
        raise SystemExit("resource GB values must be non-negative")
    return int(value * 1024**3)


def build_resource_request(args: argparse.Namespace) -> Dict[str, Any]:
    request = load_json_file(args.resource_request_file)
    if args.cpu_cores is not None:
        if args.cpu_cores < 0:
            raise SystemExit("--cpu-cores must be non-negative")
        request["cpu_cores"] = args.cpu_cores
    memory_bytes = gb_to_bytes(args.memory_gb)
    if memory_bytes is not None:
        request["memory_bytes"] = memory_bytes
    job_disk_bytes = gb_to_bytes(args.job_disk_gb)
    if job_disk_bytes is not None:
        request["job_disk_bytes"] = job_disk_bytes
    dataset_disk_bytes = gb_to_bytes(args.dataset_disk_gb)
    if dataset_disk_bytes is not None:
        request["dataset_disk_bytes"] = dataset_disk_bytes
    if args.expected_runtime_seconds is not None:
        if args.expected_runtime_seconds < 0:
            raise SystemExit("--expected-runtime-seconds must be non-negative")
        request["expected_runtime_seconds"] = args.expected_runtime_seconds
    if args.concurrency_slots is not None:
        if args.concurrency_slots < 1:
            raise SystemExit("--concurrency-slots must be at least 1")
        request["concurrency_slots"] = args.concurrency_slots

    gpu = dict(request.get("gpu") or {})
    if args.gpu_required:
        gpu["required"] = True
    if args.gpu_count is not None:
        if args.gpu_count < 0:
            raise SystemExit("--gpu-count must be non-negative")
        gpu["count"] = args.gpu_count
    gpu_memory_bytes = gb_to_bytes(args.gpu_memory_gb)
    if gpu_memory_bytes is not None:
        gpu["memory_bytes"] = gpu_memory_bytes
    if gpu:
        gpu.setdefault("required", False)
        gpu.setdefault("count", 0)
        gpu.setdefault("memory_bytes", 0)
        request["gpu"] = gpu
    return request


def parse_expected_artifact(item: str) -> Dict[str, Any]:
    parts = item.split("|")
    if not parts or not parts[0].strip():
        raise SystemExit("--expected-artifact requires at least a path")
    while len(parts) < 6:
        parts.append("")
    required_value = parts[5].strip().lower()
    required = required_value not in {"0", "false", "no", "optional"}
    return {
        "path": parts[0].strip(),
        "title": parts[1].strip(),
        "description": parts[2].strip(),
        "meaning": parts[3].strip(),
        "content_type": parts[4].strip() or None,
        "required": required,
    }


def load_dataset_refs(items: List[str]) -> List[Dict[str, Any]]:
    refs: List[Dict[str, Any]] = []
    for item in items:
        if ":" not in item:
            raise SystemExit("--dataset must be in dataset_version_id:mount_name form")
        dataset_version_id, mount_name = item.split(":", 1)
        dataset_version_id = dataset_version_id.strip()
        mount_name = mount_name.strip()
        if not dataset_version_id or not mount_name:
            raise SystemExit("--dataset requires both dataset_version_id and mount_name")
        refs.append(
            {
                "dataset_version_id": dataset_version_id,
                "mount_name": mount_name,
                "required": True,
            }
        )
    return refs


def resolve_task_timeout(args: argparse.Namespace) -> int:
    value = args.timeout if args.timeout is not None else args.timeout_seconds
    if value is None:
        return 1800
    if value <= 0:
        raise SystemExit("--timeout must be positive")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Submit a Cloudlink script job from trusted server-side code."
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("CLOUDLINK_INTERNAL_BASE_URL", "http://127.0.0.1:8010"),
    )
    parser.add_argument("--script")
    parser.add_argument("--script-file")
    parser.add_argument("--title", default="")
    parser.add_argument("--description", default="")
    parser.add_argument("--task-context-file")
    parser.add_argument(
        "--resource-request-file",
        help="JSON object declaring cpu/memory/disk/gpu resource_request.",
    )
    parser.add_argument("--cpu-cores", type=float)
    parser.add_argument("--memory-gb", type=float)
    parser.add_argument("--job-disk-gb", type=float)
    parser.add_argument("--dataset-disk-gb", type=float)
    parser.add_argument("--expected-runtime-seconds", type=int)
    parser.add_argument("--concurrency-slots", type=int)
    parser.add_argument("--gpu-required", action="store_true")
    parser.add_argument("--gpu-count", type=int)
    parser.add_argument("--gpu-memory-gb", type=float)
    parser.add_argument("--entrypoint", default="main.py")
    parser.add_argument("--runtime", default="python-auto")
    parser.add_argument("--requirement", action="append", default=[])
    parser.add_argument("--arg", action="append", default=[])
    parser.add_argument("--stdin")
    parser.add_argument("--stdin-file")
    parser.add_argument(
        "--input-file",
        action="append",
        default=[],
        help="Add a text input file as job/path=local/path.",
    )
    parser.add_argument(
        "--dataset",
        action="append",
        default=[],
        help="Reference a managed dataset as dataset_version_id:mount_name.",
    )
    parser.add_argument(
        "--expected-artifact",
        action="append",
        default=[],
        help="Format: path|title|description|meaning|content_type|required",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=None,
        help="Maximum real script runtime in seconds. Default: 1800.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=None,
        help="Deprecated alias for --timeout.",
    )
    parser.add_argument("--wait", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=5)
    parser.add_argument("--wait-timeout-seconds", type=int, default=7200)
    args = parser.parse_args()

    stdin_text = args.stdin or ""
    if args.stdin_file:
        stdin_text = open(args.stdin_file, "r", encoding="utf-8").read()

    payload: Dict[str, Any] = {
        "runtime": args.runtime,
        "script": read_script(args),
        "entrypoint": args.entrypoint,
        "requirements": args.requirement,
        "args": args.arg,
        "stdin": stdin_text,
        "input_files": load_input_files(args.input_file),
        "datasets": load_dataset_refs(args.dataset),
        "task_context": load_json_file(args.task_context_file),
        "expected_artifacts": [
            parse_expected_artifact(item) for item in args.expected_artifact
        ],
        "timeout_seconds": resolve_task_timeout(args),
    }
    resource_request = build_resource_request(args)
    if resource_request:
        payload["resource_request"] = resource_request

    base_url = args.base_url.rstrip("/")
    try:
        created = request_json(
            "POST",
            f"{base_url}/api/internal/tasks",
            {
                "type": "script_job",
                "title": args.title,
                "description": args.description,
                "payload": payload,
            },
        )
    except CloudlinkAuthError as exc:
        raise SystemExit(str(exc)) from exc
    task_id = created["id"]
    print(json.dumps(created, ensure_ascii=False))

    if not args.wait:
        return 0

    deadline = time.time() + args.wait_timeout_seconds
    while time.time() < deadline:
        task = request_json(
            "GET",
            f"{base_url}/api/internal/tasks/{task_id}",
        )
        if task["status"] in TERMINAL_STATUSES:
            print(json.dumps(task, ensure_ascii=False, indent=2))
            return 0 if task["status"] == "success" else 1
        time.sleep(args.poll_seconds)

    raise SystemExit(f"Timed out waiting for task {task_id}")


if __name__ == "__main__":
    raise SystemExit(main())
