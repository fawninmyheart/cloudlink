import pytest

from worker.config import WorkerConfigError, load_worker_config


def base_env():
    return {
        "CLOUD_API_BASE_URL": "https://tasks.example.test/",
        "WORKER_SECRET": "secret",
        "WORKER_ID": "local-worker-a",
        "WORKER_SUPPORTED_TYPES": "echo_test, script_job",
    }


def test_load_worker_config_applies_defaults_and_normalizes_values():
    config = load_worker_config(base_env())

    assert config.base_url == "https://tasks.example.test"
    assert config.worker_secret == "secret"
    assert config.worker_id == "local-worker-a"
    assert config.supported_types == ["echo_test", "script_job"]
    assert config.api_timeout_seconds == 20
    assert config.api_retries == 3
    assert config.api_retry_base_seconds == 1
    assert config.api_retry_max_seconds == 15
    assert config.artifact_upload_retries == 6
    assert config.artifact_retry_base_seconds == 2
    assert config.artifact_retry_max_seconds == 60
    assert config.poll_interval_seconds == 5
    assert config.heartbeat_seconds == 30
    assert config.dataset_api_timeout_seconds == 20
    assert config.dataset_download_timeout_seconds == 300
    assert config.maintenance_interval_seconds == 60
    assert config.max_concurrent_tasks == 1
    assert config.reserve_cpu_cores is None
    assert config.reserve_memory_bytes is None
    assert config.reserve_disk_bytes is None
    assert config.reserve_job_disk_bytes is None
    assert config.reserve_dataset_disk_bytes is None
    assert config.reserve_gpu_memory_bytes is None


def test_load_worker_config_parses_concurrency_and_reserve_overrides():
    env = base_env()
    env.update(
        {
            "WORKER_MAX_CONCURRENT_TASKS": "3",
            "WORKER_RESERVE_CPU_CORES": "2",
            "WORKER_RESERVE_MEMORY_GB": "8",
            "WORKER_RESERVE_DISK_GB": "50",
            "WORKER_RESERVE_JOB_DISK_GB": "30",
            "WORKER_RESERVE_DATASET_DISK_GB": "80",
            "WORKER_RESERVE_GPU_MEMORY_GB": "2",
        }
    )

    config = load_worker_config(env)

    assert config.max_concurrent_tasks == 3
    assert config.reserve_cpu_cores == 2
    assert config.reserve_memory_bytes == 8 * 1024**3
    assert config.reserve_disk_bytes == 50 * 1024**3
    assert config.reserve_job_disk_bytes == 30 * 1024**3
    assert config.reserve_dataset_disk_bytes == 80 * 1024**3
    assert config.reserve_gpu_memory_bytes == 2 * 1024**3


def test_load_worker_config_parses_retry_overrides():
    env = base_env()
    env.update(
        {
            "WORKER_API_RETRY_MAX_SECONDS": "12",
            "CLOUDLINK_ARTIFACT_UPLOAD_RETRIES": "8",
            "CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS": "3",
            "CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS": "90",
        }
    )

    config = load_worker_config(env)

    assert config.api_retry_max_seconds == 12
    assert config.artifact_upload_retries == 8
    assert config.artifact_retry_base_seconds == 3
    assert config.artifact_retry_max_seconds == 90


def test_load_worker_config_rejects_missing_required_values():
    env = base_env()
    env["WORKER_SECRET"] = ""

    with pytest.raises(WorkerConfigError, match="WORKER_SECRET is required"):
        load_worker_config(env)


def test_load_worker_config_rejects_empty_supported_types():
    env = base_env()
    env["WORKER_SUPPORTED_TYPES"] = " , "

    with pytest.raises(WorkerConfigError, match="WORKER_SUPPORTED_TYPES cannot be empty"):
        load_worker_config(env)


def test_load_worker_config_rejects_invalid_numeric_values():
    env = base_env()
    env["WORKER_API_RETRIES"] = "-1"

    with pytest.raises(WorkerConfigError, match="WORKER_API_RETRIES"):
        load_worker_config(env)


def test_load_worker_config_rejects_zero_concurrency():
    env = base_env()
    env["WORKER_MAX_CONCURRENT_TASKS"] = "0"

    with pytest.raises(WorkerConfigError, match="WORKER_MAX_CONCURRENT_TASKS"):
        load_worker_config(env)
