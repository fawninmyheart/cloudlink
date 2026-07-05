#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMMAND="start"
ENV_FILE="$ROOT_DIR/scripts/local_worker.env"

if [[ $# -gt 0 ]]; then
  case "$1" in
    start|doctor|print-config)
      COMMAND="$1"
      ENV_FILE="${2:-"$ENV_FILE"}"
      ;;
    *)
      COMMAND="start"
      ENV_FILE="$1"
      ;;
  esac
fi

if [[ -f "$ENV_FILE" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  set +a
else
  echo "Config file not found: $ENV_FILE" >&2
  echo "Create it from scripts/local_worker.env.example first." >&2
  exit 2
fi

: "${CLOUD_API_BASE_URL:?CLOUD_API_BASE_URL is required}"
: "${WORKER_ID:=local-worker-1}"
: "${WORKER_SUPPORTED_TYPES:=echo_test,generate_daily_report,script_job}"
: "${WORKER_API_TIMEOUT_SECONDS:=20}"
: "${WORKER_API_RETRIES:=3}"
: "${WORKER_API_RETRY_BASE_SECONDS:=1}"
: "${WORKER_API_RETRY_MAX_SECONDS:=15}"
: "${CLOUDLINK_ARTIFACT_UPLOAD_RETRIES:=6}"
: "${CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS:=2}"
: "${CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS:=60}"
: "${WORKER_POLL_INTERVAL_SECONDS:=5}"
: "${WORKER_HEARTBEAT_SECONDS:=30}"
: "${WORKER_MAINTENANCE_INTERVAL_SECONDS:=60}"
: "${WORKER_MAX_CONCURRENT_TASKS:=1}"
: "${WORKER_RESERVE_CPU_CORES:=}"
: "${WORKER_RESERVE_MEMORY_GB:=}"
: "${WORKER_RESERVE_DISK_GB:=}"
: "${WORKER_RESERVE_JOB_DISK_GB:=}"
: "${WORKER_RESERVE_DATASET_DISK_GB:=}"
: "${WORKER_RESERVE_GPU_MEMORY_GB:=}"
: "${CLOUDLINK_HOME:=$HOME/.cloudlink}"
: "${CLOUDLINK_WORKER_SECRET_FILE:=$CLOUDLINK_HOME/worker_secret}"
: "${CLOUDLINK_JOB_ROOT:=$CLOUDLINK_HOME/jobs}"
: "${CLOUDLINK_RUNTIME_ROOT:=$CLOUDLINK_HOME/venvs}"
: "${CLOUDLINK_PYTHON_AUTO_VENV:=$CLOUDLINK_RUNTIME_ROOT/python-auto}"
: "${CLOUDLINK_DATASET_ROOT:=$CLOUDLINK_HOME/datasets}"
if [[ -z "${CLOUDLINK_DATASET_ROOTS:-}" ]]; then
  CLOUDLINK_DATASET_ROOTS='[{"path":"'"$CLOUDLINK_DATASET_ROOT"'","mode":"active","label":"default"}]'
fi
: "${CLOUDLINK_DATASET_API_TIMEOUT_SECONDS:=20}"
: "${CLOUDLINK_DATASET_DOWNLOAD_TIMEOUT_SECONDS:=300}"
: "${CLOUDLINK_BASE_PYTHON:=python3}"
: "${CLOUDLINK_AUTO_INSTALL_REQUIREMENTS:=1}"
: "${CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS:=3600}"
: "${CLOUDLINK_SCRIPT_LOG_LIMIT_BYTES:=200000}"
: "${CLOUDLINK_OUTPUT_FILE_MAX_BYTES:=200000}"
: "${CLOUDLINK_OUTPUT_FILES_MAX_TOTAL_BYTES:=800000}"
: "${CLOUDLINK_ARTIFACT_CHUNK_BYTES:=4194304}"
: "${CLOUDLINK_RUNTIME_SETUP_TIMEOUT_SECONDS:=600}"
: "${CLOUDLINK_PIP_INSTALL_TIMEOUT_SECONDS:=1800}"

if [[ -z "${WORKER_SECRET:-}" && -f "$CLOUDLINK_WORKER_SECRET_FILE" ]]; then
  echo "Using cached WORKER_SECRET from $CLOUDLINK_WORKER_SECRET_FILE"
  WORKER_SECRET="$(tr -d '\r\n' < "$CLOUDLINK_WORKER_SECRET_FILE")"
  export WORKER_SECRET
fi

if [[ -z "${WORKER_SECRET:-}" ]]; then
  : "${WORKER_SECRET_SSH_HOST:=}"
  if [[ -z "$WORKER_SECRET_SSH_HOST" ]]; then
    echo "WORKER_SECRET is not configured and no cached secret was found." >&2
    echo "Use the dashboard worker install command, or set WORKER_SECRET/WORKER_SECRET_SSH_HOST explicitly." >&2
    exit 2
  fi
  echo "Fetching WORKER_SECRET from $WORKER_SECRET_SSH_HOST:/etc/cloudlink.env"
  if ! WORKER_SECRET="$(
    ssh "$WORKER_SECRET_SSH_HOST" \
      "sudo awk -F= '/^WORKER_SECRET=/{print \$2}' /etc/cloudlink.env"
  )"; then
    echo "Failed to fetch WORKER_SECRET over SSH." >&2
    echo "Retry when SSH is reachable, or set WORKER_SECRET/CLOUDLINK_WORKER_SECRET_FILE locally." >&2
    exit 2
  fi
  export WORKER_SECRET

  if [[ -n "$CLOUDLINK_WORKER_SECRET_FILE" ]]; then
    mkdir -p "$(dirname "$CLOUDLINK_WORKER_SECRET_FILE")"
    umask 077
    printf '%s\n' "$WORKER_SECRET" > "$CLOUDLINK_WORKER_SECRET_FILE"
    chmod 600 "$CLOUDLINK_WORKER_SECRET_FILE" 2>/dev/null || true
    echo "Cached WORKER_SECRET to $CLOUDLINK_WORKER_SECRET_FILE"
  fi
fi

if [[ -z "$WORKER_SECRET" ]]; then
  echo "WORKER_SECRET is empty after config loading." >&2
  exit 2
fi

export CLOUD_API_BASE_URL
export WORKER_ID
export WORKER_SUPPORTED_TYPES
export WORKER_API_TIMEOUT_SECONDS
export WORKER_API_RETRIES
export WORKER_API_RETRY_BASE_SECONDS
export WORKER_API_RETRY_MAX_SECONDS
export CLOUDLINK_ARTIFACT_UPLOAD_RETRIES
export CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS
export CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS
export WORKER_POLL_INTERVAL_SECONDS
export WORKER_HEARTBEAT_SECONDS
export WORKER_MAINTENANCE_INTERVAL_SECONDS
export WORKER_MAX_CONCURRENT_TASKS
export WORKER_RESERVE_CPU_CORES
export WORKER_RESERVE_MEMORY_GB
export WORKER_RESERVE_DISK_GB
export WORKER_RESERVE_JOB_DISK_GB
export WORKER_RESERVE_DATASET_DISK_GB
export WORKER_RESERVE_GPU_MEMORY_GB
export CLOUDLINK_HOME
export CLOUDLINK_WORKER_SECRET_FILE
export CLOUDLINK_JOB_ROOT
export CLOUDLINK_RUNTIME_ROOT
export CLOUDLINK_PYTHON_AUTO_VENV
export CLOUDLINK_DATASET_ROOT
export CLOUDLINK_DATASET_ROOTS
export CLOUDLINK_DATASET_API_TIMEOUT_SECONDS
export CLOUDLINK_DATASET_DOWNLOAD_TIMEOUT_SECONDS
export CLOUDLINK_BASE_PYTHON
export CLOUDLINK_AUTO_INSTALL_REQUIREMENTS
export CLOUDLINK_PIP_INDEX_URL="${CLOUDLINK_PIP_INDEX_URL:-}"
export CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS
export CLOUDLINK_SCRIPT_LOG_LIMIT_BYTES
export CLOUDLINK_OUTPUT_FILE_MAX_BYTES
export CLOUDLINK_OUTPUT_FILES_MAX_TOTAL_BYTES
export CLOUDLINK_ARTIFACT_CHUNK_BYTES
export CLOUDLINK_RUNTIME_SETUP_TIMEOUT_SECONDS
export CLOUDLINK_PIP_INSTALL_TIMEOUT_SECONDS

echo "Cloudlink local worker"
echo "  Command: $COMMAND"
echo "  API: $CLOUD_API_BASE_URL"
echo "  Worker: $WORKER_ID"
echo "  Types: $WORKER_SUPPORTED_TYPES"
echo "  API timeout: ${WORKER_API_TIMEOUT_SECONDS}s"
echo "  API retries: $WORKER_API_RETRIES"
echo "  API retry backoff: base=${WORKER_API_RETRY_BASE_SECONDS}s max=${WORKER_API_RETRY_MAX_SECONDS}s"
echo "  Artifact upload retries: $CLOUDLINK_ARTIFACT_UPLOAD_RETRIES"
echo "  Artifact retry backoff: base=${CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS}s max=${CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS}s"
echo "  Maintenance interval: ${WORKER_MAINTENANCE_INTERVAL_SECONDS}s"
echo "  Max concurrent tasks: $WORKER_MAX_CONCURRENT_TASKS"
echo "  Resource reserve: cpu=${WORKER_RESERVE_CPU_CORES:-auto}, memory=${WORKER_RESERVE_MEMORY_GB:-auto}GB, disk=${WORKER_RESERVE_DISK_GB:-auto}GB, job_disk=${WORKER_RESERVE_JOB_DISK_GB:-auto}GB, dataset_disk=${WORKER_RESERVE_DATASET_DISK_GB:-auto}GB, gpu=${WORKER_RESERVE_GPU_MEMORY_GB:-auto}GB"
echo "  Secret cache: $CLOUDLINK_WORKER_SECRET_FILE"
echo "  Job root: $CLOUDLINK_JOB_ROOT"
echo "  Python runtime: $CLOUDLINK_PYTHON_AUTO_VENV"
echo "  Dataset root: $CLOUDLINK_DATASET_ROOT"
echo "  Dataset roots: $CLOUDLINK_DATASET_ROOTS"
echo "  Dataset API timeout: ${CLOUDLINK_DATASET_API_TIMEOUT_SECONDS}s"
echo "  Dataset download timeout: ${CLOUDLINK_DATASET_DOWNLOAD_TIMEOUT_SECONDS}s"
echo "  Artifact chunk bytes: $CLOUDLINK_ARTIFACT_CHUNK_BYTES"

cd "$ROOT_DIR"
if [[ "${CLOUDLINK_START_WORKER_DRY_RUN:-0}" == "1" ]]; then
  echo "Dry run: worker not started"
  exit 0
fi

case "$COMMAND" in
  start)
    exec "$ROOT_DIR/.venv/bin/python" -m worker.local_worker
    ;;
  doctor)
    exec "$ROOT_DIR/.venv/bin/python" -m worker.local_worker doctor
    ;;
  print-config)
    exec "$ROOT_DIR/.venv/bin/python" -m worker.local_worker print-config
    ;;
esac
