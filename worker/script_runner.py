import base64
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from uuid import uuid4

try:
    from worker.runtime_manager import ensure_python_auto_runtime
except ModuleNotFoundError:  # pragma: no cover - supports direct script execution.
    from runtime_manager import ensure_python_auto_runtime


PYTHON_AUTO_RUNTIMES = {"python-auto", "python3-auto", "python3.12-auto"}
SAFE_ENV_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class ScriptExecutionTimeout(RuntimeError):
    error_code = "execution_timeout"

    def __init__(self, message: str, timeout_seconds: int) -> None:
        self.timeout_seconds = timeout_seconds
        super().__init__(message)


def run_script_job(
    payload: Dict[str, Any],
    worker_id: str,
    task_id: Optional[str] = None,
    dataset_env: Optional[Dict[str, str]] = None,
    dataset_records: Optional[List[Dict[str, Any]]] = None,
    artifact_uploader: Any = None,
) -> Tuple[Dict[str, Any], str]:
    runtime = normalize_runtime(payload.get("runtime", "python-auto"))
    script = payload.get("script")
    if not isinstance(script, str) or not script.strip():
        raise ValueError("script_job payload.script is required")

    job_dir = build_job_dir(task_id)
    output_dir = job_dir / "outputs"
    output_dir.mkdir(parents=True, exist_ok=True)

    entrypoint = safe_relative_path(payload.get("entrypoint", "main.py"), "entrypoint")
    entrypoint_path = job_dir / entrypoint
    entrypoint_path.parent.mkdir(parents=True, exist_ok=True)
    entrypoint_path.write_text(script, encoding="utf-8")

    write_input_files(job_dir, payload.get("input_files", []))
    write_dataset_manifest(job_dir, dataset_records or [])
    requirements = normalize_requirements_payload(payload.get("requirements", []))
    python_path = ensure_python_auto_runtime(requirements)

    args = normalize_string_list(payload.get("args", []), "args")
    timeout_seconds = bounded_timeout(payload.get("timeout_seconds"))
    command = [str(python_path), str(entrypoint_path)] + args
    try:
        completed = subprocess.run(
            command,
            input=normalize_stdin(payload.get("stdin", "")),
            text=True,
            capture_output=True,
            timeout=timeout_seconds,
            cwd=str(job_dir),
            env=build_job_env(job_dir, output_dir, payload.get("env", {}), dataset_env or {}),
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        stdout = timeout_output_text(exc.stdout)
        stderr = timeout_output_text(exc.stderr)
        logs = build_logs(command, -1, stdout, stderr)
        raise ScriptExecutionTimeout(
            f"script_job execution exceeded timeout_seconds={timeout_seconds}",
            timeout_seconds=timeout_seconds,
        ) from exc

    stdout = trim_text(completed.stdout)
    stderr = trim_text(completed.stderr)
    logs = build_logs(command, completed.returncode, stdout, stderr)
    artifact_manifest = read_artifact_manifest(output_dir)
    if artifact_manifest and artifact_uploader and hasattr(artifact_uploader, "with_manifest"):
        artifact_uploader = artifact_uploader.with_manifest(artifact_manifest)
    result = {
        "summary": first_nonempty_line(stdout, stderr),
        "worker_id": worker_id,
        "runtime": runtime,
        "exit_code": completed.returncode,
        "job_dir": str(job_dir),
        "stdout": stdout,
        "stderr": stderr,
        "output_files": list_output_files(output_dir, artifact_uploader),
        "datasets": dataset_records or [],
    }

    if completed.returncode != 0:
        raise RuntimeError(
            f"script_job failed with exit code {completed.returncode}: {stderr[-1000:]}"
        )
    return result, logs


def normalize_runtime(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("runtime must be a string")
    runtime = value.strip()
    if runtime not in PYTHON_AUTO_RUNTIMES:
        raise ValueError("runtime must be one of: python-auto, python3-auto, python3.12-auto")
    return "python-auto"


def build_job_dir(task_id: Optional[str]) -> Path:
    job_root = Path(
        os.getenv("CLOUDLINK_JOB_ROOT", str(Path.home() / ".cloudlink" / "jobs"))
    ).expanduser()
    job_id = task_id if task_id and re.match(r"^[A-Za-z0-9_.-]+$", task_id) else str(uuid4())
    job_dir = (job_root / job_id).resolve()
    job_dir.mkdir(parents=True, exist_ok=True)
    return job_dir


def safe_relative_path(raw: Any, field_name: str) -> Path:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{field_name} must be a non-empty string path")
    path = Path(raw.strip())
    if path.is_absolute() or ".." in path.parts:
        raise ValueError(f"{field_name} path must stay inside the job directory")
    if str(path) in {".", ""}:
        raise ValueError(f"{field_name} path must be a file path")
    return path


def write_input_files(job_dir: Path, input_files: Any) -> None:
    if input_files in (None, ""):
        return
    if not isinstance(input_files, list):
        raise ValueError("input_files must be a list")
    for item in input_files:
        if not isinstance(item, dict):
            raise ValueError("input_files entries must be objects")
        relative_path = safe_relative_path(item.get("path"), "input_files.path")
        target = job_dir / relative_path
        target.parent.mkdir(parents=True, exist_ok=True)
        if "content_base64" in item:
            data = base64.b64decode(str(item["content_base64"]), validate=True)
            target.write_bytes(data)
        elif "content" in item:
            target.write_text(str(item["content"]), encoding="utf-8")
        else:
            raise ValueError("input_files entries require content or content_base64")


def write_dataset_manifest(job_dir: Path, dataset_records: List[Dict[str, Any]]) -> None:
    if not dataset_records:
        return
    (job_dir / "datasets.json").write_text(
        json.dumps(dataset_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def read_artifact_manifest(output_dir: Path) -> Dict[str, Any]:
    path = output_dir / "cloudlink_artifacts.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("cloudlink_artifacts.json must be an object")
    artifacts = data.get("artifacts", [])
    if artifacts is not None and not isinstance(artifacts, list):
        raise ValueError("cloudlink_artifacts.json artifacts must be a list")
    return data


def normalize_requirements_payload(value: Any) -> List[str]:
    if value in (None, ""):
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if not isinstance(value, list):
        raise ValueError("requirements must be a list or newline separated string")
    return [str(item).strip() for item in value if str(item).strip()]


def normalize_string_list(value: Any, field_name: str) -> List[str]:
    if value in (None, ""):
        return []
    if not isinstance(value, list):
        raise ValueError(f"{field_name} must be a list")
    return [str(item) for item in value]


def normalize_stdin(value: Any) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError("stdin must be a string")
    return value


def bounded_timeout(value: Any) -> int:
    max_timeout = int(os.getenv("CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS", "3600"))
    default_timeout = min(1800, max_timeout)
    if value is None:
        return default_timeout
    try:
        requested = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout_seconds must be an integer") from exc
    if requested <= 0:
        raise ValueError("timeout_seconds must be positive")
    return min(requested, max_timeout)


def timeout_output_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def build_job_env(
    job_dir: Path,
    output_dir: Path,
    payload_env: Any,
    dataset_env: Dict[str, str],
) -> Dict[str, str]:
    env = {
        "PATH": os.getenv("PATH", ""),
        "HOME": str(Path(os.getenv("CLOUDLINK_HOME", "~/.cloudlink")).expanduser()),
        "LANG": os.getenv("LANG", "en_US.UTF-8"),
        "PYTHONUNBUFFERED": "1",
        "CLOUDLINK_JOB_DIR": str(job_dir),
        "CLOUDLINK_OUTPUT_DIR": str(output_dir),
    }
    if payload_env in (None, ""):
        return env
    if not isinstance(payload_env, dict):
        raise ValueError("env must be an object")
    for key, value in payload_env.items():
        if not isinstance(key, str) or not SAFE_ENV_KEY.match(key):
            raise ValueError("env keys must be shell-safe names")
        env[key] = str(value)
    for key, value in dataset_env.items():
        if not SAFE_ENV_KEY.match(key):
            raise ValueError("dataset env keys must be shell-safe names")
        env[key] = str(value)
    return env


def trim_text(value: str) -> str:
    limit = int(os.getenv("CLOUDLINK_SCRIPT_LOG_LIMIT_BYTES", "200000"))
    encoded = value.encode("utf-8", errors="replace")
    if len(encoded) <= limit:
        return value
    tail = encoded[-limit:].decode("utf-8", errors="replace")
    return f"[truncated to last {limit} bytes]\n{tail}"


def first_nonempty_line(stdout: str, stderr: str) -> str:
    for text in (stdout, stderr):
        for line in text.splitlines():
            if line.strip():
                return line.strip()[:4000]
    return ""


def list_output_files(output_dir: Path, artifact_uploader: Any = None) -> List[Dict[str, Any]]:
    if not output_dir.exists():
        return []
    max_file_bytes = int(os.getenv("CLOUDLINK_OUTPUT_FILE_MAX_BYTES", "200000"))
    max_total_bytes = int(os.getenv("CLOUDLINK_OUTPUT_FILES_MAX_TOTAL_BYTES", "800000"))
    used_bytes = 0
    files: List[Dict[str, Any]] = []
    paths = sorted(path for path in output_dir.rglob("*") if path.is_file())
    for path in paths:
        if len(files) >= 200:
            break
        if path.is_file():
            relative_path = str(path.relative_to(output_dir))
            if (
                relative_path == "cloudlink_artifacts.json"
                and not is_expected_output(relative_path, artifact_uploader)
            ):
                continue
            size_bytes = path.stat().st_size
            files.append(
                build_output_file_entry(
                    path,
                    output_dir,
                    size_bytes,
                    max_file_bytes,
                    max_total_bytes,
                    used_bytes,
                    artifact_uploader,
                )
            )
            if size_bytes <= max_file_bytes and used_bytes + size_bytes <= max_total_bytes:
                used_bytes += size_bytes
    return files


def build_output_file_entry(
    path: Path,
    output_dir: Path,
    size_bytes: int,
    max_file_bytes: int,
    max_total_bytes: int,
    used_bytes: int,
    artifact_uploader: Any = None,
) -> Dict[str, Any]:
    entry: Dict[str, Any] = {
        "path": str(path.relative_to(output_dir)),
        "size_bytes": size_bytes,
    }
    if size_bytes > max_file_bytes or used_bytes + size_bytes > max_total_bytes:
        if artifact_uploader is not None:
            return artifact_uploader.upload(path, output_dir)
        entry["content_omitted"] = True
        return entry

    data = path.read_bytes()
    try:
        entry["content"] = data.decode("utf-8")
    except UnicodeDecodeError:
        entry["content_base64"] = base64.b64encode(data).decode("ascii")
    return entry


def is_expected_output(relative_path: str, artifact_uploader: Any = None) -> bool:
    if artifact_uploader is None or not hasattr(artifact_uploader, "is_expected"):
        return False
    return bool(artifact_uploader.is_expected(relative_path))


def build_logs(command: List[str], exit_code: int, stdout: str, stderr: str) -> str:
    return (
        f"$ {' '.join(command)}\n"
        f"exit_code={exit_code}\n\n"
        f"--- stdout ---\n{stdout}\n"
        f"--- stderr ---\n{stderr}"
    )
