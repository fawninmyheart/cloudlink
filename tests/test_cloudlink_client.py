import importlib.util
from pathlib import Path


def load_client_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "cloudlink_client.py"
    spec = importlib.util.spec_from_file_location("cloudlink_client", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_auth_headers_prefers_internal_secret(monkeypatch, tmp_path):
    module = load_client_module()
    token_file = tmp_path / "codex-token"
    token_file.write_text("codex-secret\n", encoding="utf-8")
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN_FILE", str(token_file))

    assert module.auth_headers() == {"X-Internal-API-Secret": "internal-secret"}


def test_auth_headers_uses_codex_token_file_without_sudo(monkeypatch, tmp_path):
    module = load_client_module()
    token_file = tmp_path / "codex-token"
    token_file.write_text("codex-secret\n", encoding="utf-8")
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.delenv("CLOUDLINK_CODEX_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN_FILE", str(token_file))

    assert module.auth_headers() == {"X-Cloudlink-Codex-Token": "codex-secret"}


def test_auth_headers_errors_without_any_secret(monkeypatch, tmp_path):
    module = load_client_module()
    monkeypatch.delenv("INTERNAL_API_SECRET", raising=False)
    monkeypatch.delenv("CLOUDLINK_CODEX_TOKEN", raising=False)
    monkeypatch.setenv("CLOUDLINK_CODEX_TOKEN_FILE", str(tmp_path / "missing"))

    try:
        module.auth_headers()
    except module.CloudlinkAuthError as exc:
        assert "CLOUDLINK_CODEX_TOKEN" in str(exc)
    else:
        raise AssertionError("expected CloudlinkAuthError")


def test_artifact_download_url_builder():
    module = load_client_module()

    assert module.artifact_download_path("task-a", "artifact-b") == (
        "/api/internal/tasks/task-a/artifacts/artifact-b/download"
    )
