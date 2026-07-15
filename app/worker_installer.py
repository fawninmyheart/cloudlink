import io
import gzip
import hashlib
import tarfile
from pathlib import Path
from typing import Iterable, List


PACKAGE_MEMBERS = [
    "requirements.txt",
    "app/__init__.py",
    "app/resource_model.py",
    "app/version.py",
    "worker",
    "scripts/start_local_worker.sh",
    "scripts/local_worker.env.example",
]


def _package_files(root: Path, member: str) -> List[tuple[Path, Path]]:
    path = root / member
    arc_root = Path("cloudlink") / member
    if not path.exists():
        return []
    if path.is_file():
        return [(path, arc_root)]
    return [
        (file_path, Path("cloudlink") / file_path.relative_to(root))
        for file_path in sorted(path.rglob("*"))
        if file_path.is_file()
    ]


def build_worker_package() -> bytes:
    root = Path(__file__).resolve().parents[1]
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as gzip_file:
        with tarfile.open(fileobj=gzip_file, mode="w") as archive:
            for member in PACKAGE_MEMBERS:
                for path, arcname in _package_files(root, member):
                    data = path.read_bytes()
                    info = tarfile.TarInfo(str(arcname))
                    info.size = len(data)
                    info.mtime = 0
                    info.uid = 0
                    info.gid = 0
                    info.uname = ""
                    info.gname = ""
                    info.mode = 0o755 if path.suffix == ".sh" else 0o644
                    archive.addfile(info, io.BytesIO(data))
    return buffer.getvalue()


def worker_package_sha256() -> str:
    return hashlib.sha256(build_worker_package()).hexdigest()


def worker_env_text(
    *,
    api_base_url: str,
    worker_secret: str,
    worker_id: str,
    supported_types: Iterable[str],
) -> str:
    supported = ",".join(sorted(set(supported_types)))
    return "\n".join(
        [
            f"CLOUD_API_BASE_URL={api_base_url.rstrip('/')}",
            f"WORKER_SECRET={worker_secret}",
            f"WORKER_ID={worker_id}",
            f"WORKER_SUPPORTED_TYPES={supported}",
            "WORKER_API_TIMEOUT_SECONDS=20",
            "WORKER_API_RETRIES=3",
            "WORKER_API_RETRY_BASE_SECONDS=1",
            "WORKER_API_RETRY_MAX_SECONDS=15",
            "WORKER_POLL_INTERVAL_SECONDS=5",
            "WORKER_HEARTBEAT_SECONDS=30",
            "WORKER_MAINTENANCE_INTERVAL_SECONDS=60",
            "CLOUDLINK_HOME=$HOME/.cloudlink",
            "CLOUDLINK_JOB_ROOT=$HOME/.cloudlink/jobs",
            "CLOUDLINK_RUNTIME_ROOT=$HOME/.cloudlink/venvs",
            "CLOUDLINK_PYTHON_AUTO_VENV=$HOME/.cloudlink/venvs/python-auto",
            "CLOUDLINK_DATASET_ROOT=$HOME/.cloudlink/datasets",
            "CLOUDLINK_BASE_PYTHON=python3",
            "CLOUDLINK_AUTO_INSTALL_REQUIREMENTS=1",
            "CLOUDLINK_ARTIFACT_CHUNK_BYTES=4194304",
            "CLOUDLINK_ARTIFACT_UPLOAD_RETRIES=6",
            "CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS=2",
            "CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS=60",
            "",
        ]
    )


def worker_install_command(platform: str, script_url: str) -> str:
    if platform not in {"macos", "linux"}:
        raise ValueError("worker install platform must be macos or linux")
    return f"curl -fsSL {script_url} | bash"


def render_posix_install_script(*, base_url: str, token: str, package_sha256: str) -> str:
    return f"""#!/usr/bin/env bash
set -euo pipefail

BASE_URL="{base_url.rstrip("/")}"
TOKEN="{token}"
PACKAGE_SHA256="{package_sha256}"
INSTALL_DIR="${{CLOUDLINK_INSTALL_DIR:-$HOME/.cloudlink/worker}}"
PYTHON_BIN="${{CLOUDLINK_BASE_PYTHON:-python3}}"
TMP_DIR="$(mktemp -d)"
cleanup() {{ rm -rf "$TMP_DIR"; }}
trap cleanup EXIT

mkdir -p "$INSTALL_DIR"
chmod 700 "$INSTALL_DIR" 2>/dev/null || true
curl -fsSL "$BASE_URL/install/worker/$TOKEN/package.tar.gz" -o "$TMP_DIR/cloudlink-worker.tar.gz"
if command -v sha256sum >/dev/null 2>&1; then
  ACTUAL_SHA256="$(sha256sum "$TMP_DIR/cloudlink-worker.tar.gz" | awk '{{print $1}}')"
else
  ACTUAL_SHA256="$(shasum -a 256 "$TMP_DIR/cloudlink-worker.tar.gz" | awk '{{print $1}}')"
fi
if [[ "$ACTUAL_SHA256" != "$PACKAGE_SHA256" ]]; then
  echo "Cloudlink worker package checksum mismatch." >&2
  exit 3
fi
rm -rf "$INSTALL_DIR/current"
mkdir -p "$INSTALL_DIR/current"
chmod 700 "$INSTALL_DIR/current" 2>/dev/null || true
tar -xzf "$TMP_DIR/cloudlink-worker.tar.gz" -C "$INSTALL_DIR/current" --strip-components=1

cd "$INSTALL_DIR/current"
find . -type d -name "__pycache__" -exec rm -rf {{}} + 2>/dev/null || true
find . -type f -name "*.pyc" -delete 2>/dev/null || true
find . -type f -name "*.py" -exec touch {{}} +
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -r requirements.txt

export CLOUDLINK_INSTALL_BASE_URL="$BASE_URL"
export CLOUDLINK_INSTALL_TOKEN="$TOKEN"
REGISTER_BODY="$(.venv/bin/python - <<'PY'
import json
import platform
import socket

print(json.dumps({{
    "hostname": socket.gethostname(),
    "platform": platform.system().lower(),
}}))
PY
)"
REGISTER_JSON="$(curl -fsSL -X POST "$BASE_URL/install/worker/$TOKEN/register" \
  -H "Content-Type: application/json" \
  -H "User-Agent: Cloudlink-Worker-Installer/1.0" \
  --data "$REGISTER_BODY")"

export CLOUDLINK_REGISTER_JSON="$REGISTER_JSON"
.venv/bin/python - <<'PY'
import json
import os
from pathlib import Path

data = json.loads(os.environ["CLOUDLINK_REGISTER_JSON"])
env_file = Path("scripts/local_worker.env")
env_file.write_text(data["env"], encoding="utf-8")
env_file.chmod(0o600)
secret_file = Path.home() / ".cloudlink" / "worker_secret"
secret_file.parent.mkdir(parents=True, exist_ok=True)
secret_file.parent.chmod(0o700)
secret_file.write_text(data["worker_secret"] + "\\n", encoding="utf-8")
secret_file.chmod(0o600)
print(f"Registered Cloudlink worker {{data['worker_id']}}")
PY

scripts/start_local_worker.sh doctor scripts/local_worker.env
if pgrep -f "worker.local_worker" >/dev/null 2>&1; then
  pkill -f "worker.local_worker" || true
  sleep 2
  echo "Existing Cloudlink worker processes stopped."
fi
nohup scripts/start_local_worker.sh scripts/local_worker.env > "$HOME/.cloudlink/worker.log" 2>&1 &
echo "Cloudlink worker started. Log: $HOME/.cloudlink/worker.log"
"""
