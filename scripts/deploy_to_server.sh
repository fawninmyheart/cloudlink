#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-}"
DEST="${2:-/opt/cloudlink/}"

if [[ -z "$HOST" ]]; then
  echo "Usage: $0 <ssh-host> [remote-destination]" >&2
  exit 2
fi

RSYNC_ARGS=(
  -av
  --delete
  --exclude "/.git/***"
  --exclude "/.venv/***"
  --exclude "__pycache__/***"
  --exclude "*.pyc"
  --exclude "/.pytest_cache/***"
  --exclude "/.DS_Store"
  --exclude "/data/***"
  --exclude "/.codex-token"
  --exclude "/scripts/local_worker.env"
)

echo "Deploying Cloudlink to ${HOST}:${DEST}"
echo "Protected remote paths: /data, /.codex-token, /scripts/local_worker.env, /.venv"

if [[ "${CLOUDLINK_DEPLOY_DRY_RUN:-0}" == "1" ]]; then
  printf 'Dry run: rsync'
  printf ' %q' "${RSYNC_ARGS[@]}" "${ROOT_DIR}/" "${HOST}:${DEST}"
  printf '\n'
  exit 0
fi

rsync "${RSYNC_ARGS[@]}" "${ROOT_DIR}/" "${HOST}:${DEST}"
