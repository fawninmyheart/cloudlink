#!/usr/bin/env python3
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, Optional


DEFAULT_BASE_URL = "http://127.0.0.1:8010"
DEFAULT_CODEX_TOKEN_FILES = (
    "/opt/cloudlink/.codex-token",
    "~/.cloudlink/codex-token",
)


class CloudlinkAuthError(RuntimeError):
    pass


def base_url() -> str:
    return os.getenv("CLOUDLINK_INTERNAL_BASE_URL", DEFAULT_BASE_URL).rstrip("/")


def read_codex_token_file() -> str:
    explicit = os.getenv("CLOUDLINK_CODEX_TOKEN_FILE", "").strip()
    paths = [explicit] if explicit else list(DEFAULT_CODEX_TOKEN_FILES)
    for item in paths:
        if not item:
            continue
        path = Path(item).expanduser()
        try:
            token = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError:
            continue
        if token:
            return token
    return ""


def auth_headers() -> Dict[str, str]:
    internal_secret = os.getenv("INTERNAL_API_SECRET", "").strip()
    if internal_secret:
        return {"X-Internal-API-Secret": internal_secret}

    codex_token = os.getenv("CLOUDLINK_CODEX_TOKEN", "").strip()
    if not codex_token:
        codex_token = read_codex_token_file()
    if codex_token:
        return {"X-Cloudlink-Codex-Token": codex_token}

    raise CloudlinkAuthError(
        "Set INTERNAL_API_SECRET or CLOUDLINK_CODEX_TOKEN, "
        "or create /opt/cloudlink/.codex-token for cloud-side Codex CLI."
    )


def artifact_download_path(task_id: str, artifact_id: str) -> str:
    return f"/api/internal/tasks/{task_id}/artifacts/{artifact_id}/download"


def request_json(
    method: str,
    path_or_url: str,
    body: Optional[Dict[str, Any]] = None,
    timeout: int = 30,
) -> Dict[str, Any]:
    url = path_or_url
    if path_or_url.startswith("/"):
        url = f"{base_url()}{path_or_url}"

    data = None
    headers = auth_headers()
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = response.read().decode("utf-8")
            return json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code} from {url}: {detail}") from exc
