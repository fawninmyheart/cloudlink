import json
import os
import subprocess
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional

from packaging.requirements import InvalidRequirement, Requirement
from packaging.version import Version


class GpuRuntimeError(RuntimeError):
    error_code = "gpu_runtime_unavailable"


class GpuRuntimeValidationTimeout(GpuRuntimeError):
    error_code = "gpu_runtime_validation_timeout"


class RuntimeDependencyError(GpuRuntimeError):
    error_code = "runtime_dependency_missing"


def gpu_runtime_enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    source = os.environ if env is None else env
    return str(source.get("CLOUDLINK_GPU_ENABLED", "0")).strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def gpu_environment_path(env: Optional[Mapping[str, str]] = None) -> Path:
    source = os.environ if env is None else env
    value = str(source.get("CLOUDLINK_GPU_ENVIRONMENT_PATH", "")).strip()
    if not value:
        raise GpuRuntimeError("CLOUDLINK_GPU_ENVIRONMENT_PATH is required")
    path = Path(value).expanduser().resolve()
    if not path.is_absolute() or not path.is_dir():
        raise GpuRuntimeError("Configured GPU environment path is unavailable")
    return path


def micromamba_executable(env: Optional[Mapping[str, str]] = None) -> Path:
    source = os.environ if env is None else env
    value = str(source.get("CLOUDLINK_MICROMAMBA_EXE", "")).strip()
    if not value:
        raise GpuRuntimeError("CLOUDLINK_MICROMAMBA_EXE is required")
    path = Path(value).expanduser().resolve()
    if not path.is_file() or not os.access(path, os.X_OK):
        raise GpuRuntimeError("Configured micromamba executable is unavailable")
    return path


def micromamba_python_command(
    *args: str,
    env: Optional[Mapping[str, str]] = None,
) -> List[str]:
    return [
        str(micromamba_executable(env)),
        "run",
        "-p",
        str(gpu_environment_path(env)),
        "python",
        *args,
    ]


def validate_gpu_runtime(
    env: Optional[Mapping[str, str]] = None,
    *,
    timeout: float = 120,
) -> Dict[str, Any]:
    if not gpu_runtime_enabled(env):
        return {"enabled": False, "verified": False}
    probe = r"""
import json
import platform
import sys

result = {
    "python_version": platform.python_version(),
    "python_executable": sys.executable,
    "environment_prefix": sys.prefix,
}
try:
    import torch
    result["torch_version"] = torch.__version__
    result["torch_cuda_version"] = torch.version.cuda
    result["cuda_available"] = bool(torch.cuda.is_available())
    result["gpu_count"] = int(torch.cuda.device_count())
    if result["cuda_available"] and result["gpu_count"]:
        value = torch.ones(1, device="cuda")
        result["cuda_tensor_verified"] = bool(value.item() == 1)
except Exception as exc:
    result["torch_error"] = f"{type(exc).__name__}: {exc}"
try:
    import transformers
    result["transformers_version"] = transformers.__version__
except Exception as exc:
    result["transformers_error"] = f"{type(exc).__name__}: {exc}"
print(json.dumps(result))
"""
    try:
        completed = subprocess.run(
            micromamba_python_command("-c", probe, env=env),
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise GpuRuntimeValidationTimeout(
            f"GPU environment validation timed out after {timeout:g} seconds"
        ) from exc
    if completed.returncode != 0:
        raise GpuRuntimeError(
            f"GPU environment probe failed: {completed.stderr.strip()[-500:]}"
        )
    try:
        result = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise GpuRuntimeError("GPU environment probe returned invalid output") from exc
    expected_prefix = gpu_environment_path(env)
    actual_prefix = Path(str(result.get("environment_prefix") or "")).resolve()
    if actual_prefix != expected_prefix:
        raise GpuRuntimeError("micromamba environment prefix does not match configuration")
    if result.get("torch_error"):
        raise GpuRuntimeError(f"PyTorch validation failed: {result['torch_error']}")
    if result.get("transformers_error"):
        raise GpuRuntimeError(
            f"Transformers validation failed: {result['transformers_error']}"
        )
    if not result.get("cuda_available") or not result.get("cuda_tensor_verified"):
        raise GpuRuntimeError("PyTorch CUDA execution is unavailable")
    result.update(
        {
            "enabled": True,
            "verified": True,
            "runtime": "pytorch-cuda",
            "environment_path": str(expected_prefix),
            "micromamba_executable": str(micromamba_executable(env)),
        }
    )
    return result


def validate_gpu_requirements(
    requirements: Iterable[str],
    env: Optional[Mapping[str, str]] = None,
    *,
    timeout: int = 20,
) -> None:
    parsed: List[Requirement] = []
    for raw in requirements:
        try:
            parsed.append(Requirement(str(raw)))
        except InvalidRequirement as exc:
            raise RuntimeDependencyError(f"Invalid GPU requirement: {raw}") from exc
    if not parsed:
        return
    names = sorted({requirement.name for requirement in parsed})
    probe = (
        "import importlib.metadata,json;"
        f"names={names!r};"
        "print(json.dumps({n:(importlib.metadata.version(n) "
        "if any(d.metadata.get('Name','').lower()==n.lower() "
        "for d in importlib.metadata.distributions()) else None) for n in names}))"
    )
    completed = subprocess.run(
        micromamba_python_command("-c", probe, env=env),
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )
    if completed.returncode != 0:
        raise RuntimeDependencyError("Unable to inspect GPU environment dependencies")
    try:
        installed = json.loads(completed.stdout.strip().splitlines()[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise RuntimeDependencyError("GPU dependency probe returned invalid output") from exc
    problems = []
    for requirement in parsed:
        version_text = installed.get(requirement.name)
        if not version_text:
            problems.append(f"{requirement.name} is missing")
            continue
        if requirement.specifier and Version(version_text) not in requirement.specifier:
            problems.append(
                f"{requirement.name} {version_text} does not satisfy {requirement.specifier}"
            )
    if problems:
        raise RuntimeDependencyError(
            "GPU environment requires user maintenance: " + "; ".join(problems)
        )
