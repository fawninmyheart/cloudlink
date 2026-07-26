import argparse
import json

import pytest

from scripts.submit_local_script_job import (
    build_resource_request,
    resolve_task_timeout,
    resolve_wait_timeout,
)


def namespace(**overrides):
    values = {
        "resource_request_file": None,
        "cpu_cores": None,
        "memory_gb": None,
        "job_disk_gb": None,
        "dataset_disk_gb": None,
        "expected_runtime_seconds": None,
        "concurrency_slots": None,
        "gpu_required": False,
        "gpu_count": None,
        "gpu_memory_gb": None,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def test_build_resource_request_from_direct_flags():
    request = build_resource_request(
        namespace(
            cpu_cores=4,
            memory_gb=8,
            job_disk_gb=12.5,
            dataset_disk_gb=2,
            expected_runtime_seconds=600,
            concurrency_slots=1,
            gpu_required=True,
            gpu_count=1,
            gpu_memory_gb=4,
        )
    )

    assert request == {
        "cpu_cores": 4,
        "memory_bytes": 8 * 1024**3,
        "job_disk_bytes": int(12.5 * 1024**3),
        "dataset_disk_bytes": 2 * 1024**3,
        "expected_runtime_seconds": 600,
        "concurrency_slots": 1,
        "gpu": {"required": True, "count": 1, "memory_bytes": 4 * 1024**3},
    }


def test_build_resource_request_merges_file_and_flags(tmp_path):
    file_path = tmp_path / "request.json"
    file_path.write_text(
        json.dumps(
            {
                "cpu_cores": 2,
                "memory_bytes": 1024,
                "job_disk_bytes": 2048,
                "gpu": {"required": False, "count": 0, "memory_bytes": 0},
            }
        ),
        encoding="utf-8",
    )

    request = build_resource_request(
        namespace(resource_request_file=str(file_path), memory_gb=3)
    )

    assert request["cpu_cores"] == 2
    assert request["memory_bytes"] == 3 * 1024**3
    assert request["job_disk_bytes"] == 2048


def test_build_resource_request_empty_when_no_resource_options():
    assert build_resource_request(namespace()) == {}


def test_resolve_task_timeout_prefers_new_timeout_flag():
    args = argparse.Namespace(timeout=3600, timeout_seconds=1200)

    assert resolve_task_timeout(args) == 3600


def test_resolve_task_timeout_keeps_legacy_timeout_seconds_alias():
    args = argparse.Namespace(timeout=None, timeout_seconds=900)

    assert resolve_task_timeout(args) == 900


def test_resolve_task_timeout_defaults_to_30_minutes():
    args = argparse.Namespace(timeout=None, timeout_seconds=None)

    assert resolve_task_timeout(args) == 1800


def test_wait_timeout_defaults_to_task_timeout_plus_shutdown_margin():
    args = argparse.Namespace(wait_timeout_seconds=None)

    assert resolve_wait_timeout(args, 14 * 24 * 60 * 60) == 14 * 24 * 60 * 60 + 300


def test_wait_timeout_accepts_explicit_override():
    args = argparse.Namespace(wait_timeout_seconds=60)

    assert resolve_wait_timeout(args, 1800) == 60


def test_wait_timeout_rejects_non_positive_value():
    args = argparse.Namespace(wait_timeout_seconds=0)

    with pytest.raises(SystemExit, match="must be positive"):
        resolve_wait_timeout(args, 1800)
