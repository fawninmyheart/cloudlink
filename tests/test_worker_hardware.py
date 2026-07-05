from pathlib import Path

from app.version import CLOUDLINK_VERSION
from worker.hardware import (
    build_capacity_state,
    build_runtime_profile,
    collect_worker_profiles,
    detect_available_memory_bytes,
    detect_total_memory_bytes,
)


def test_collect_worker_profiles_subtracts_configured_reserves(tmp_path, monkeypatch):
    job_root = tmp_path / "jobs"
    dataset_root = tmp_path / "datasets"
    job_root.mkdir()
    dataset_root.mkdir()

    monkeypatch.setattr("worker.hardware.cpu_count", lambda: 10)
    monkeypatch.setattr(
        "worker.hardware.detect_total_memory_bytes",
        lambda: 64 * 1024**3,
    )
    monkeypatch.setattr(
        "worker.hardware.detect_available_memory_bytes",
        lambda: 32 * 1024**3,
    )
    monkeypatch.setattr(
        "worker.hardware.disk_usage",
        lambda _path: (500 * 1024**3, 100 * 1024**3),
    )
    monkeypatch.setattr("worker.hardware.detect_gpu_devices", lambda: [])

    hardware_profile, _runtime_profile, capacity_state = collect_worker_profiles(
        job_root=job_root,
        dataset_root=dataset_root,
        reserve_overrides={
            "cpu_cores": 2,
            "memory_bytes": 8 * 1024**3,
            "disk_bytes": 50 * 1024**3,
            "gpu_memory_bytes": 0,
        },
    )

    assert hardware_profile["scheduler"]["cpu_cores"] == 8
    assert hardware_profile["scheduler"]["memory_bytes"] == 56 * 1024**3
    assert hardware_profile["scheduler"]["job_disk_bytes"] == 450 * 1024**3
    assert capacity_state["cpu_cores"] == 8
    assert capacity_state["memory_bytes"] == 24 * 1024**3
    assert capacity_state["job_disk_bytes"] == 50 * 1024**3


def test_collect_worker_profiles_records_raw_disk_free_bytes(tmp_path, monkeypatch):
    job_root = tmp_path / "jobs"
    dataset_root = tmp_path / "datasets"
    job_root.mkdir()
    dataset_root.mkdir()

    def fake_disk_usage(path):
        if path == job_root:
            return 300 * 1024**3, 220 * 1024**3
        return 500 * 1024**3, 410 * 1024**3

    monkeypatch.setattr("worker.hardware.cpu_count", lambda: 8)
    monkeypatch.setattr(
        "worker.hardware.detect_total_memory_bytes",
        lambda: 32 * 1024**3,
    )
    monkeypatch.setattr("worker.hardware.detect_available_memory_bytes", lambda: None)
    monkeypatch.setattr("worker.hardware.disk_usage", fake_disk_usage)
    monkeypatch.setattr("worker.hardware.detect_gpu_devices", lambda: [])

    hardware_profile, _runtime_profile, capacity_state = collect_worker_profiles(
        job_root=job_root,
        dataset_root=dataset_root,
    )

    assert hardware_profile["raw"]["job_disk_total_bytes"] == 300 * 1024**3
    assert hardware_profile["raw"]["job_disk_free_bytes"] == 220 * 1024**3
    assert hardware_profile["raw"]["dataset_disk_total_bytes"] == 500 * 1024**3
    assert hardware_profile["raw"]["dataset_disk_free_bytes"] == 410 * 1024**3
    assert capacity_state["job_disk_bytes"] == 190 * 1024**3
    assert capacity_state["dataset_disk_bytes"] == 360 * 1024**3


def test_build_capacity_state_uses_scheduler_when_free_values_unknown():
    hardware_profile = {
        "scheduler": {
            "cpu_cores": 4,
            "memory_bytes": 16,
            "job_disk_bytes": 32,
            "dataset_disk_bytes": 64,
            "gpu_devices": [],
        },
        "reserve": {
            "memory_bytes": 4,
            "job_disk_bytes": 8,
            "dataset_disk_bytes": 8,
        },
    }

    capacity = build_capacity_state(
        hardware_profile,
        memory_available_bytes=None,
        job_disk_free_bytes=None,
        dataset_disk_free_bytes=None,
    )

    assert capacity["memory_bytes"] == 16
    assert capacity["job_disk_bytes"] == 32
    assert capacity["dataset_disk_bytes"] == 64


def test_build_runtime_profile_includes_python_and_roots(tmp_path):
    profile = build_runtime_profile(
        worker_id="worker-a",
        job_root=Path(tmp_path / "jobs"),
        dataset_root=Path(tmp_path / "datasets"),
        dataset_roots=[
            {
                "path": str(tmp_path / "datasets"),
                "mode": "active",
                "label": "默认数据盘",
            },
            {
                "path": str(tmp_path / "old-datasets"),
                "mode": "readonly",
                "label": "历史数据盘",
            },
        ],
        python_runtime=Path(tmp_path / "venv" / "bin" / "python"),
    )

    assert profile["worker_id"] == "worker-a"
    assert profile["job_root"].endswith("jobs")
    assert profile["dataset_root"].endswith("datasets")
    assert profile["dataset_roots"][0]["mode"] == "active"
    assert profile["dataset_roots"][1]["path"].endswith("old-datasets")
    assert profile["python_runtime"].endswith("python")
    assert profile["python_version"]
    assert profile["cloudlink_version"] == CLOUDLINK_VERSION


def test_detect_total_memory_uses_sysconf_fallback(monkeypatch):
    monkeypatch.setattr("worker.hardware._sysctl_int", lambda _name: None)
    monkeypatch.setattr("worker.hardware._linux_meminfo", lambda: {})
    monkeypatch.setattr("worker.hardware.sys.platform", "darwin")
    monkeypatch.setattr(
        "worker.hardware.os.sysconf",
        lambda key: 4096 if key == "SC_PAGE_SIZE" else 100,
    )

    assert detect_total_memory_bytes() == 409600


def test_windows_memory_detection_uses_global_memory_status(monkeypatch):
    monkeypatch.setattr("worker.hardware.sys.platform", "win32")
    monkeypatch.setattr(
        "worker.hardware._windows_memory_status",
        lambda: (32 * 1024**3, 20 * 1024**3),
        raising=False,
    )

    assert detect_total_memory_bytes() == 32 * 1024**3
    assert detect_available_memory_bytes() == 20 * 1024**3


def test_disk_usage_returns_zero_for_invalid_path(tmp_path):
    from worker.hardware import disk_usage

    file_path = tmp_path / "not-a-directory"
    file_path.write_text("blocks mkdir", encoding="utf-8")

    total, free = disk_usage(file_path)

    assert total == 0
    assert free == 0


def test_disk_usage_uses_macos_capacity_probe_when_it_reports_more_free_space(
    tmp_path,
    monkeypatch,
):
    from worker.hardware import disk_usage

    monkeypatch.setattr("worker.hardware.sys.platform", "darwin")
    monkeypatch.setattr(
        "worker.hardware.shutil.disk_usage",
        lambda _path: (460 * 1024**3, 394 * 1024**3, 66 * 1024**3),
    )
    monkeypatch.setattr(
        "worker.hardware._macos_foundation_disk_usage",
        lambda _path: (460 * 1024**3, 200 * 1024**3),
    )

    total, free = disk_usage(tmp_path)

    assert total == 460 * 1024**3
    assert free == 200 * 1024**3


def test_disk_usage_keeps_shutil_capacity_when_macos_probe_is_lower(
    tmp_path,
    monkeypatch,
):
    from worker.hardware import disk_usage

    monkeypatch.setattr("worker.hardware.sys.platform", "darwin")
    monkeypatch.setattr(
        "worker.hardware.shutil.disk_usage",
        lambda _path: (460 * 1024**3, 394 * 1024**3, 66 * 1024**3),
    )
    monkeypatch.setattr(
        "worker.hardware._macos_foundation_disk_usage",
        lambda _path: (460 * 1024**3, 0),
    )

    total, free = disk_usage(tmp_path)

    assert total == 460 * 1024**3
    assert free == 66 * 1024**3
