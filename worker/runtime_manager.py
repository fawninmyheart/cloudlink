import os
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Set


def cloudlink_home() -> Path:
    return Path(os.getenv("CLOUDLINK_HOME", "~/.cloudlink")).expanduser()


def python_auto_venv_path() -> Path:
    configured = os.getenv("CLOUDLINK_PYTHON_AUTO_VENV", "").strip()
    if configured:
        return Path(configured).expanduser()
    runtime_root = Path(
        os.getenv("CLOUDLINK_RUNTIME_ROOT", str(cloudlink_home() / "venvs"))
    ).expanduser()
    return runtime_root / "python-auto"


def venv_python_path(venv_path: Path) -> Path:
    if os.name == "nt":
        return venv_path / "Scripts" / "python.exe"
    return venv_path / "bin" / "python"


def ensure_python_auto_runtime(requirements: Iterable[str]) -> Path:
    venv_path = python_auto_venv_path()
    python_path = venv_python_path(venv_path)
    if not python_path.exists():
        create_python_venv(venv_path)

    normalized_requirements = normalize_requirements(requirements)
    if normalized_requirements:
        install_requirements_if_needed(python_path, venv_path, normalized_requirements)
    return python_path


def create_python_venv(venv_path: Path) -> None:
    venv_path.parent.mkdir(parents=True, exist_ok=True)
    base_python = os.getenv("CLOUDLINK_BASE_PYTHON", sys.executable).strip() or sys.executable
    timeout = int(os.getenv("CLOUDLINK_RUNTIME_SETUP_TIMEOUT_SECONDS", "600"))
    subprocess.run(
        [base_python, "-m", "venv", str(venv_path)],
        check=True,
        timeout=timeout,
    )


def normalize_requirements(requirements: Iterable[str]) -> List[str]:
    normalized: List[str] = []
    for raw in requirements:
        if not isinstance(raw, str):
            raise ValueError("requirements must be strings")
        item = raw.strip()
        if not item:
            continue
        if "\n" in item or "\r" in item:
            raise ValueError("requirement entries must be single lines")
        if item.startswith("-"):
            raise ValueError("pip option entries are not allowed in requirements")
        normalized.append(item)
    return normalized


def install_requirements_if_needed(
    python_path: Path,
    venv_path: Path,
    requirements: List[str],
) -> None:
    if os.getenv("CLOUDLINK_AUTO_INSTALL_REQUIREMENTS", "1").lower() in {
        "0",
        "false",
        "no",
    }:
        raise ValueError("requirements were provided but auto install is disabled")

    state_file = venv_path / ".cloudlink-installed-requirements.txt"
    installed = read_installed_requirements(state_file)
    missing = [item for item in requirements if item not in installed]
    if not missing:
        return

    command = [str(python_path), "-m", "pip", "install"]
    pip_index_url = os.getenv("CLOUDLINK_PIP_INDEX_URL", "").strip()
    if pip_index_url:
        command.extend(["--index-url", pip_index_url])
    command.extend(missing)

    subprocess.run(
        command,
        check=True,
        timeout=int(os.getenv("CLOUDLINK_PIP_INSTALL_TIMEOUT_SECONDS", "1800")),
    )

    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.write_text(
        "\n".join(sorted(installed.union(missing))) + "\n",
        encoding="utf-8",
    )


def read_installed_requirements(path: Path) -> Set[str]:
    if not path.exists():
        return set()
    return {
        line.strip()
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
        if line.strip()
    }
