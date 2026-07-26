import subprocess

import pytest

from worker.gpu_runtime import (
    GpuRuntimeValidationTimeout,
    validate_gpu_runtime,
)


def test_gpu_runtime_timeout_has_concise_error(monkeypatch, tmp_path):
    micromamba = tmp_path / "micromamba"
    micromamba.write_text("#!/bin/sh\n", encoding="utf-8")
    micromamba.chmod(0o755)
    environment = tmp_path / "gpu-env"
    environment.mkdir()
    env = {
        "CLOUDLINK_GPU_ENABLED": "1",
        "CLOUDLINK_GPU_ENVIRONMENT_PATH": str(environment),
        "CLOUDLINK_MICROMAMBA_EXE": str(micromamba),
    }

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

    monkeypatch.setattr(subprocess, "run", timeout)

    with pytest.raises(
        GpuRuntimeValidationTimeout,
        match="timed out after 120 seconds",
    ) as exc_info:
        validate_gpu_runtime(env)

    assert "import torch" not in str(exc_info.value)
