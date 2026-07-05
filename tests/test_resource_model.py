import pytest

from app.resource_model import (
    ResourceValidationError,
    fits_capacity,
    normalize_resource_request,
    subtract_reserved_profile,
)


def test_subtract_reserved_profile_exposes_scheduler_capacity():
    profile = subtract_reserved_profile(
        {
            "cpu_logical_cores": 10,
            "memory_total_bytes": 64 * 1024**3,
            "job_disk_total_bytes": 500 * 1024**3,
            "dataset_disk_total_bytes": 800 * 1024**3,
            "gpu_devices": [{"name": "GPU", "memory_total_bytes": 16 * 1024**3}],
        },
        {
            "cpu_cores": 2,
            "memory_bytes": 8 * 1024**3,
            "disk_bytes": 50 * 1024**3,
            "gpu_memory_bytes": 2 * 1024**3,
        },
    )

    assert profile["scheduler"]["cpu_cores"] == 8
    assert profile["scheduler"]["memory_bytes"] == 56 * 1024**3
    assert profile["scheduler"]["job_disk_bytes"] == 450 * 1024**3
    assert profile["scheduler"]["dataset_disk_bytes"] == 750 * 1024**3
    assert profile["scheduler"]["gpu_devices"][0]["memory_bytes"] == 14 * 1024**3


def test_subtract_reserved_profile_uses_default_reserves():
    profile = subtract_reserved_profile(
        {
            "cpu_logical_cores": 4,
            "memory_total_bytes": 16 * 1024**3,
            "job_disk_total_bytes": 100 * 1024**3,
            "dataset_disk_total_bytes": 200 * 1024**3,
        }
    )

    assert profile["reserve"]["cpu_cores"] == 1
    assert profile["reserve"]["memory_bytes"] == 4 * 1024**3
    assert profile["reserve"]["job_disk_bytes"] == 20 * 1024**3
    assert profile["reserve"]["dataset_disk_bytes"] == 20 * 1024**3
    assert profile["scheduler"]["cpu_cores"] == 3
    assert profile["scheduler"]["memory_bytes"] == 12 * 1024**3


def test_default_cpu_reserve_rounds_up_to_whole_cores():
    profile = subtract_reserved_profile(
        {
            "cpu_logical_cores": 6,
            "memory_total_bytes": 32 * 1024**3,
            "job_disk_total_bytes": 300 * 1024**3,
            "dataset_disk_total_bytes": 300 * 1024**3,
        }
    )

    assert profile["reserve"]["cpu_cores"] == 2
    assert isinstance(profile["reserve"]["cpu_cores"], int)
    assert profile["scheduler"]["cpu_cores"] == 4
    assert isinstance(profile["scheduler"]["cpu_cores"], int)


def test_default_byte_reserves_round_up_to_whole_gib():
    profile = subtract_reserved_profile(
        {
            "cpu_logical_cores": 16,
            "memory_total_bytes": 24 * 1024**3,
            "job_disk_total_bytes": 225 * 1024**3,
            "dataset_disk_total_bytes": 235 * 1024**3,
            "gpu_devices": [{"name": "GPU", "memory_total_bytes": 18 * 1024**3}],
        }
    )

    assert profile["reserve"]["memory_bytes"] == 5 * 1024**3
    assert profile["reserve"]["job_disk_bytes"] == 23 * 1024**3
    assert profile["reserve"]["dataset_disk_bytes"] == 24 * 1024**3
    assert profile["reserve"]["gpu_memory_bytes"] == 2 * 1024**3


def test_normalize_resource_request_rejects_negative_values():
    with pytest.raises(ResourceValidationError, match="cpu_cores"):
        normalize_resource_request({"cpu_cores": -1})


def test_normalize_resource_request_defaults_missing_values_to_zero():
    request = normalize_resource_request({})

    assert request == {
        "cpu_cores": 0,
        "memory_bytes": 0,
        "job_disk_bytes": 0,
        "dataset_disk_bytes": 0,
        "expected_runtime_seconds": 0,
        "concurrency_slots": 1,
        "gpu": {"required": False, "count": 0, "memory_bytes": 0},
    }


def test_normalize_resource_request_rounds_cpu_up_to_whole_cores():
    request = normalize_resource_request({"cpu_cores": 4.2})

    assert request["cpu_cores"] == 5
    assert isinstance(request["cpu_cores"], int)


def test_fits_capacity_reports_shortages():
    request = normalize_resource_request(
        {"cpu_cores": 4, "memory_bytes": 8 * 1024**3, "job_disk_bytes": 20 * 1024**3}
    )

    ok, shortages = fits_capacity(
        request,
        {"cpu_cores": 2, "memory_bytes": 16 * 1024**3, "job_disk_bytes": 10 * 1024**3},
    )

    assert not ok
    assert {item["resource"] for item in shortages} == {"cpu_cores", "job_disk_bytes"}
    assert all("requested" in item and "available" in item for item in shortages)


def test_gpu_required_must_fit_gpu_count_and_memory():
    request = normalize_resource_request(
        {"gpu": {"required": True, "count": 1, "memory_bytes": 12 * 1024**3}}
    )

    ok, shortages = fits_capacity(
        request,
        {"gpu_devices": [{"name": "small", "memory_bytes": 8 * 1024**3}]},
    )

    assert not ok
    assert shortages == [
        {
            "resource": "gpu",
            "requested": {"count": 1, "memory_bytes": 12 * 1024**3},
            "available": {"count": 0, "memory_bytes": 8 * 1024**3},
        }
    ]
