import os
import subprocess
from pathlib import Path


ROOT_DIR = Path(__file__).resolve().parents[1]


def test_deploy_script_protects_runtime_state_in_dry_run():
    result = subprocess.run(
        ["bash", "scripts/deploy_to_server.sh", "example.test", "/opt/cloudlink/"],
        cwd=ROOT_DIR,
        env={**os.environ, "CLOUDLINK_DEPLOY_DRY_RUN": "1"},
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    output = result.stdout.replace("\\", "")
    assert "Dry run: rsync" in result.stdout
    assert "--delete" in output
    assert "/data/***" in output
    assert "/.codex-token" in output
    assert "/scripts/local_worker.env" in output
    assert "/.venv/***" in output
