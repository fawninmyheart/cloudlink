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
    gpu_enabled: bool = False,
    gpu_environment_path: str = "",
    micromamba_executable: str = "",
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
            f"CLOUDLINK_GPU_ENABLED={1 if gpu_enabled else 0}",
            f"CLOUDLINK_GPU_ENVIRONMENT_PATH={gpu_environment_path}",
            f"CLOUDLINK_MICROMAMBA_EXE={micromamba_executable}",
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


def worker_uninstall_command(platform: str, script_url: str) -> str:
    if platform == "windows":
        return (
            'powershell -NoProfile -ExecutionPolicy Bypass -Command '
            f'"irm {script_url} | iex"'
        )
    if platform not in {"macos", "linux"}:
        raise ValueError("worker uninstall platform must be macos, linux, or windows")
    return f"curl -fsSL {script_url} | bash"


def render_legacy_windows_uninstall_script(
    *,
    base_url: str,
    token: str,
    worker_id: str,
) -> str:
    return f"""$ErrorActionPreference = "Stop"

$BaseUrl = "{base_url.rstrip("/")}"
$Token = "{token}"
$WorkerId = "{worker_id}"
$InstallDir = $env:CLOUDLINK_INSTALL_DIR
if (-not $InstallDir) {{
  $InstallDir = Join-Path $env:LOCALAPPDATA "Cloudlink\\worker"
}}
$Current = Join-Path $InstallDir "current"
$EnvPath = Join-Path $Current "scripts\\local_worker.env"
$SecretFile = Join-Path $env:USERPROFILE ".cloudlink\\worker_secret"

function Read-CloudlinkEnvValue {{
  param([string]$Path, [string]$Name)
  if (-not (Test-Path $Path)) {{ return "" }}
  $Prefix = "$Name="
  $Line = Get-Content -Path $Path |
    Where-Object {{ $_.StartsWith($Prefix) }} |
    Select-Object -Last 1
  if (-not $Line) {{ return "" }}
  return $Line.Substring($Prefix.Length).Trim()
}}

$WorkerSecret = Read-CloudlinkEnvValue $EnvPath "WORKER_SECRET"
if (-not $WorkerSecret) {{ $WorkerSecret = $env:WORKER_SECRET }}
if (-not $WorkerSecret -and (Test-Path $SecretFile)) {{
  $WorkerSecret = (Get-Content -Raw -Path $SecretFile).Trim()
}}
if (-not $WorkerSecret) {{
  throw "Cloudlink worker secret was not found."
}}

$Headers = @{{ "X-Worker-Secret" = $WorkerSecret }}
$Body = @{{ worker_id = $WorkerId }} | ConvertTo-Json -Compress
Invoke-RestMethod `
  -Method Post `
  -Uri "$BaseUrl/uninstall/worker/$Token/begin" `
  -Headers $Headers `
  -ContentType "application/json" `
  -Body $Body | Out-Null

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object {{ $_.CommandLine -like "*worker.local_worker*" }} |
  ForEach-Object {{ Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }}
Start-Sleep -Seconds 1

if (Test-Path $Current) {{
  $Removed = $false
  for ($Attempt = 1; $Attempt -le 5; $Attempt++) {{
    try {{
      Remove-Item -Recurse -Force $Current
      $Removed = $true
      break
    }} catch {{
      if ($Attempt -eq 5) {{ throw }}
      Start-Sleep -Seconds 1
    }}
  }}
  if (-not $Removed -and (Test-Path $Current)) {{
    throw "Cloudlink worker code could not be removed."
  }}
}}
if (Test-Path $SecretFile) {{ Remove-Item -Force $SecretFile }}

Invoke-RestMethod `
  -Method Post `
  -Uri "$BaseUrl/uninstall/worker/$Token/complete" `
  -Headers $Headers `
  -ContentType "application/json" `
  -Body $Body | Out-Null

@(
  "CLOUD_API_BASE_URL",
  "WORKER_SECRET",
  "WORKER_ID",
  "WORKER_SUPPORTED_TYPES",
  "CLOUDLINK_GPU_ENABLED",
  "CLOUDLINK_GPU_ENVIRONMENT_PATH",
  "CLOUDLINK_MICROMAMBA_EXE"
) | ForEach-Object {{
  [Environment]::SetEnvironmentVariable($_, $null, "User")
}}

Write-Host "Cloudlink worker removed."
Write-Host "Jobs, datasets, environments, outputs, runtimes, and logs were preserved."
"""


def render_posix_uninstall_script(
    *,
    base_url: str,
    token: str,
    platform: str,
    worker_id: str,
) -> str:
    if platform not in {"macos", "linux"}:
        raise ValueError("worker uninstall platform must be macos or linux")
    service_id = "".join(
        char if char.isalnum() or char in "._-" else "-"
        for char in worker_id
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail

BASE_URL="{base_url.rstrip("/")}"
TOKEN="{token}"
WORKER_ID="{worker_id}"
PLATFORM="{platform}"
SERVICE_ID="{service_id}"
INSTALL_DIR="${{CLOUDLINK_INSTALL_DIR:-$HOME/.cloudlink/worker}}"
ENV_FILE="$INSTALL_DIR/current/scripts/local_worker.env"
SECRET_FILE="$HOME/.cloudlink/worker_secret"

WORKER_SECRET=""
if [[ -f "$ENV_FILE" ]]; then
  WORKER_SECRET="$(sed -n 's/^WORKER_SECRET=//p' "$ENV_FILE" | tail -1)"
fi
if [[ -z "$WORKER_SECRET" && -f "$SECRET_FILE" ]]; then
  WORKER_SECRET="$(tr -d '\\r\\n' < "$SECRET_FILE")"
fi
[[ -n "$WORKER_SECRET" ]] || {{ echo "Cloudlink worker secret was not found." >&2; exit 2; }}

curl -fsSL -X POST "$BASE_URL/uninstall/worker/$TOKEN/begin" \
  -H "X-Worker-Secret: $WORKER_SECRET" \
  -H "Content-Type: application/json" \
  --data "{{\\"worker_id\\":\\"$WORKER_ID\\"}}" >/dev/null

if [[ "$PLATFORM" == "linux" ]]; then
  sudo systemctl disable --now "cloudlink-worker-$SERVICE_ID.service" 2>/dev/null || true
  sudo rm -f "/etc/systemd/system/cloudlink-worker-$SERVICE_ID.service"
  sudo systemctl daemon-reload
else
  launchctl bootout "gui/$(id -u)/com.cloudlink.worker.$SERVICE_ID" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/com.cloudlink.worker.$SERVICE_ID.plist"
fi

rm -rf "$INSTALL_DIR/current"
rm -f "$SECRET_FILE"

curl -fsSL -X POST "$BASE_URL/uninstall/worker/$TOKEN/complete" \
  -H "X-Worker-Secret: $WORKER_SECRET" \
  -H "Content-Type: application/json" \
  --data "{{\\"worker_id\\":\\"$WORKER_ID\\"}}"
echo
echo "Cloudlink worker removed. Jobs, datasets, environments, outputs, and logs were preserved."
"""


def render_posix_install_script(
    *,
    base_url: str,
    token: str,
    package_sha256: str,
    platform: str,
    worker_id: str,
    gpu_requested: bool = False,
    gpu_environment_path: str = "",
) -> str:
    if platform not in {"macos", "linux"}:
        raise ValueError("worker install platform must be macos or linux")
    service_id = "".join(
        char if char.isalnum() or char in "._-" else "-"
        for char in worker_id
    )
    return f"""#!/usr/bin/env bash
set -euo pipefail

BASE_URL="{base_url.rstrip("/")}"
TOKEN="{token}"
PACKAGE_SHA256="{package_sha256}"
INSTALL_PLATFORM="{platform}"
WORKER_ID="{worker_id}"
SERVICE_ID="{service_id}"
GPU_REQUESTED="{1 if gpu_requested else 0}"
GPU_ENVIRONMENT_PATH="{gpu_environment_path}"
INSTALL_DIR="${{CLOUDLINK_INSTALL_DIR:-$HOME/.cloudlink/worker}}"
PYTHON_BIN="${{CLOUDLINK_BASE_PYTHON:-python3}}"
TMP_DIR="$(mktemp -d)"
cleanup() {{ rm -rf "$TMP_DIR"; }}
trap cleanup EXIT

if [[ "$INSTALL_PLATFORM" == "linux" ]]; then
  [[ "$(uname -s)" == "Linux" ]] || {{ echo "This invite requires Linux or WSL." >&2; exit 2; }}
  command -v systemctl >/dev/null 2>&1 || {{ echo "systemd is required." >&2; exit 2; }}
  command -v sudo >/dev/null 2>&1 || {{ echo "sudo is required to install the worker service." >&2; exit 2; }}
else
  [[ "$(uname -s)" == "Darwin" ]] || {{ echo "This invite requires macOS." >&2; exit 2; }}
  command -v launchctl >/dev/null 2>&1 || {{ echo "launchd is required." >&2; exit 2; }}
fi

MICROMAMBA_EXE=""
if [[ "$GPU_REQUESTED" == "1" ]]; then
  [[ -d "$GPU_ENVIRONMENT_PATH" ]] || {{ echo "GPU environment does not exist: $GPU_ENVIRONMENT_PATH" >&2; exit 2; }}
  MICROMAMBA_EXE="$(command -v micromamba || true)"
  [[ -n "$MICROMAMBA_EXE" ]] || {{ echo "micromamba is not available in PATH." >&2; exit 2; }}
  [[ -x "$MICROMAMBA_EXE" ]] || {{ echo "micromamba is not executable." >&2; exit 2; }}
fi

if [[ "$INSTALL_PLATFORM" == "linux" ]]; then
  sudo systemctl stop "cloudlink-worker-$SERVICE_ID.service" 2>/dev/null || true
else
  launchctl bootout "gui/$(id -u)/com.cloudlink.worker.$SERVICE_ID" 2>/dev/null || true
fi

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

export CLOUDLINK_GPU_ENABLED="$GPU_REQUESTED"
export CLOUDLINK_GPU_ENVIRONMENT_PATH="$GPU_ENVIRONMENT_PATH"
export CLOUDLINK_MICROMAMBA_EXE="$MICROMAMBA_EXE"
if [[ "$GPU_REQUESTED" == "1" ]]; then
  command -v nvidia-smi >/dev/null 2>&1 || {{ echo "nvidia-smi is not available inside Linux/WSL." >&2; exit 2; }}
  nvidia-smi >/dev/null
  .venv/bin/python - <<'PY'
from worker.gpu_runtime import validate_gpu_runtime
profile = validate_gpu_runtime()
print(
    "Validated GPU runtime: "
    f"torch={{profile.get('torch_version')}} "
    f"cuda={{profile.get('torch_cuda_version')}} "
    f"gpus={{profile.get('gpu_count')}}"
)
PY
fi

export CLOUDLINK_INSTALL_BASE_URL="$BASE_URL"
export CLOUDLINK_INSTALL_TOKEN="$TOKEN"
export MICROMAMBA_EXE
REGISTER_BODY="$(.venv/bin/python - <<'PY'
import json
import os
import platform
import socket

print(json.dumps({{
    "hostname": socket.gethostname(),
    "platform": platform.system().lower(),
    "micromamba_executable": os.environ.get("MICROMAMBA_EXE") or None,
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
mkdir -p "$HOME/.cloudlink/logs"
if [[ "$INSTALL_PLATFORM" == "linux" ]]; then
  SERVICE_FILE="$TMP_DIR/cloudlink-worker-$SERVICE_ID.service"
  cat > "$SERVICE_FILE" <<EOF
[Unit]
Description=Cloudlink Worker $WORKER_ID
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$(id -un)
WorkingDirectory=$INSTALL_DIR/current
ExecStart=$INSTALL_DIR/current/scripts/start_local_worker.sh $INSTALL_DIR/current/scripts/local_worker.env
Restart=always
RestartSec=5
StandardOutput=append:$HOME/.cloudlink/logs/worker.log
StandardError=append:$HOME/.cloudlink/logs/worker.log

[Install]
WantedBy=multi-user.target
EOF
  sudo install -m 0644 "$SERVICE_FILE" "/etc/systemd/system/cloudlink-worker-$SERVICE_ID.service"
  sudo systemctl daemon-reload
  sudo systemctl enable --now "cloudlink-worker-$SERVICE_ID.service"
  sudo systemctl --no-pager --full status "cloudlink-worker-$SERVICE_ID.service" || true
else
  PLIST="$HOME/Library/LaunchAgents/com.cloudlink.worker.$SERVICE_ID.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.cloudlink.worker.$SERVICE_ID</string>
  <key>ProgramArguments</key><array>
    <string>$INSTALL_DIR/current/scripts/start_local_worker.sh</string>
    <string>$INSTALL_DIR/current/scripts/local_worker.env</string>
  </array>
  <key>WorkingDirectory</key><string>$INSTALL_DIR/current</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>$HOME/.cloudlink/logs/worker.log</string>
  <key>StandardErrorPath</key><string>$HOME/.cloudlink/logs/worker.log</string>
</dict></plist>
EOF
  chmod 600 "$PLIST"
  launchctl bootstrap "gui/$(id -u)" "$PLIST"
  launchctl enable "gui/$(id -u)/com.cloudlink.worker.$SERVICE_ID"
  launchctl kickstart -k "gui/$(id -u)/com.cloudlink.worker.$SERVICE_ID"
fi
echo "Cloudlink worker service installed. Log: $HOME/.cloudlink/logs/worker.log"
"""
