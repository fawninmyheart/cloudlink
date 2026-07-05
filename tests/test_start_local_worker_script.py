import os
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_start_script_uses_cached_worker_secret_without_ssh(tmp_path):
    secret_file = tmp_path / "worker_secret"
    secret_file.write_text("cached-secret\n", encoding="utf-8")
    env_file = tmp_path / "local_worker.env"
    env_file.write_text(
        "\n".join(
            [
                "CLOUD_API_BASE_URL=https://tasks.example.test",
                "WORKER_SECRET=",
                f"CLOUDLINK_HOME={tmp_path / 'home'}",
                f"CLOUDLINK_WORKER_SECRET_FILE={secret_file}",
                "WORKER_SECRET_SSH_HOST=should-not-be-used",
            ]
        ),
        encoding="utf-8",
    )

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    ssh_stub = bin_dir / "ssh"
    ssh_stub.write_text(
        "#!/usr/bin/env bash\n"
        "echo 'ssh should not be called when cache exists' >&2\n"
        "exit 42\n",
        encoding="utf-8",
    )
    ssh_stub.chmod(0o755)

    env = {
        **os.environ,
        "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
        "CLOUDLINK_START_WORKER_DRY_RUN": "1",
    }

    result = subprocess.run(
        ["bash", "scripts/start_local_worker.sh", str(env_file)],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Using cached WORKER_SECRET" in result.stdout
    assert "Dry run: worker not started" in result.stdout
    assert "Command: start" in result.stdout
    assert "Max concurrent tasks:" in result.stdout
    assert "Dataset roots:" in result.stdout
    assert '"label":"default"}]' in result.stdout
    assert "cached-secret" not in result.stdout
    assert "ssh should not be called" not in result.stderr


def test_start_script_dispatches_doctor_without_starting_worker(tmp_path):
    secret_file = tmp_path / "worker_secret"
    secret_file.write_text("cached-secret\n", encoding="utf-8")
    env_file = tmp_path / "local_worker.env"
    env_file.write_text(
        "\n".join(
            [
                "CLOUD_API_BASE_URL=https://tasks.example.test",
                f"CLOUDLINK_WORKER_SECRET_FILE={secret_file}",
                "WORKER_ID=worker-a",
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "CLOUDLINK_START_WORKER_DRY_RUN": "1"}

    result = subprocess.run(
        ["bash", "scripts/start_local_worker.sh", "doctor", str(env_file)],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Command: doctor" in result.stdout
    assert "Worker: worker-a" in result.stdout
    assert "Dry run: worker not started" in result.stdout


def test_start_script_dispatches_print_config_without_printing_secret(tmp_path):
    secret_file = tmp_path / "worker_secret"
    secret_file.write_text("cached-secret\n", encoding="utf-8")
    env_file = tmp_path / "local_worker.env"
    env_file.write_text(
        "\n".join(
            [
                "CLOUD_API_BASE_URL=https://tasks.example.test",
                f"CLOUDLINK_WORKER_SECRET_FILE={secret_file}",
            ]
        ),
        encoding="utf-8",
    )
    env = {**os.environ, "CLOUDLINK_START_WORKER_DRY_RUN": "1"}

    result = subprocess.run(
        ["bash", "scripts/start_local_worker.sh", "print-config", str(env_file)],
        cwd=ROOT_DIR,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert "Command: print-config" in result.stdout
    assert "cached-secret" not in result.stdout


def test_start_script_has_generic_defaults_for_open_source_release():
    text = (ROOT_DIR / "scripts/start_local_worker.sh").read_text(encoding="utf-8")
    personal_host = "sun" + "fawn"

    assert "local-worker-a" not in text
    assert personal_host not in text
