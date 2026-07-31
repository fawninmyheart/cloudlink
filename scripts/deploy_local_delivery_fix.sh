#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST_DIR="/opt/cloudlink"
EXPECTED_OLD_VERSION="2026.07.30.1"
NEW_VERSION="2026.07.31.1"
MODE="${1:-apply}"

FILES=(
  "CHANGELOG.md"
  "README.md"
  "app/task_store.py"
  "app/version.py"
  "worker/api_client.py"
  "worker/local_worker.py"
)

usage() {
  cat <<'EOF'
Usage: sudo scripts/deploy_local_delivery_fix.sh [apply|--dry-run]

Applies the 2026.07.31.1 persistent-delivery lease fix to /opt/cloudlink,
backs up replaced files under /opt/cloudlink/data/deployment-backups, validates
Python syntax, restarts cloudlink.service, and verifies the deployed version.
EOF
}

if [[ "$MODE" == "--help" || "$MODE" == "-h" ]]; then
  usage
  exit 0
fi
if [[ "$MODE" != "apply" && "$MODE" != "--dry-run" ]]; then
  usage >&2
  exit 2
fi
if [[ ! -x "$DEST_DIR/.venv/bin/python" ]]; then
  echo "Cloudlink runtime is missing: $DEST_DIR/.venv/bin/python" >&2
  exit 1
fi

for file in "${FILES[@]}"; do
  [[ -f "$ROOT_DIR/$file" ]] || {
    echo "Patch source is missing: $ROOT_DIR/$file" >&2
    exit 1
  }
  [[ -f "$DEST_DIR/$file" ]] || {
    echo "Deployed file is missing: $DEST_DIR/$file" >&2
    exit 1
  }
done

deployed_version="$(
  cd "$DEST_DIR"
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c \
    'from app.version import CLOUDLINK_VERSION; print(CLOUDLINK_VERSION)'
)"
if [[ "$deployed_version" != "$EXPECTED_OLD_VERSION" && "$deployed_version" != "$NEW_VERSION" ]]; then
  echo "Refusing to patch unexpected Cloudlink version: $deployed_version" >&2
  exit 1
fi

if [[ "$MODE" == "--dry-run" ]]; then
  echo "Would patch Cloudlink $deployed_version to $NEW_VERSION."
  printf 'Would replace: %s\n' "${FILES[@]}"
  echo "Would restart cloudlink.service."
  exit 0
fi
if [[ "$EUID" -ne 0 ]]; then
  echo "Run this deployment entrypoint with sudo." >&2
  exit 2
fi

backup_dir="$DEST_DIR/data/deployment-backups/$(date -u +%Y%m%dT%H%M%SZ)-delivery-lease-fix"
for file in "${FILES[@]}"; do
  install -D -m 0644 "$DEST_DIR/$file" "$backup_dir/$file"
  install -m 0644 "$ROOT_DIR/$file" "$DEST_DIR/$file"
done

DEST_DIR="$DEST_DIR" "$DEST_DIR/.venv/bin/python" - <<'PY'
import ast
import os
from pathlib import Path

root = Path(os.environ["DEST_DIR"])
for relative in (
    "app/task_store.py",
    "app/version.py",
    "worker/api_client.py",
    "worker/local_worker.py",
):
    ast.parse((root / relative).read_text(encoding="utf-8"), filename=relative)
PY

systemctl restart cloudlink
systemctl is-active --quiet cloudlink

actual_version="$(
  cd "$DEST_DIR"
  PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -c \
    'from app.version import CLOUDLINK_VERSION; print(CLOUDLINK_VERSION)'
)"
if [[ "$actual_version" != "$NEW_VERSION" ]]; then
  echo "Cloudlink restarted with unexpected version: $actual_version" >&2
  exit 1
fi

echo "Cloudlink $actual_version is active."
echo "Backup: $backup_dir"
echo "Existing workers must now be updated from the Cloudlink console."
