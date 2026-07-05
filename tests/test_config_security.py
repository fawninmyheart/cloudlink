import json

import pytest

from app.config import get_settings


def set_required_env(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_DATABASE_PATH", str(tmp_path / "tasks.db"))
    monkeypatch.setenv("INTERNAL_API_SECRET", "internal-secret")
    monkeypatch.setenv("ADMIN_PASSWORD", "admin-pass")
    monkeypatch.delenv("WORKER_SECRET", raising=False)
    monkeypatch.delenv("CLOUDLINK_CODEX_TOKEN", raising=False)
    monkeypatch.delenv("CLOUDLINK_CODEX_TOKENS", raising=False)


def test_settings_accepts_required_real_secrets(monkeypatch, tmp_path):
    set_required_env(monkeypatch, tmp_path)

    settings = get_settings()

    assert settings.internal_api_secret == "internal-secret"
    assert settings.admin_password == "admin-pass"
    assert settings.worker_secret == ""
    assert settings.codex_tokens == {}


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("INTERNAL_API_SECRET", ""),
        ("INTERNAL_API_SECRET", "change-this-internal-secret"),
        ("ADMIN_PASSWORD", ""),
        ("ADMIN_PASSWORD", "change-this-admin-password"),
    ],
)
def test_settings_rejects_missing_or_placeholder_required_secrets(
    monkeypatch,
    tmp_path,
    name,
    value,
):
    set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        get_settings()


@pytest.mark.parametrize(
    ("name", "value"),
    [
        ("WORKER_SECRET", "please-change-me"),
        ("WORKER_SECRET", "please-change-me-legacy-worker-fallback"),
        ("CLOUDLINK_CODEX_TOKEN", "change-this-local-codex-token"),
        ("CLOUDLINK_CODEX_TOKEN", "<generate-a-long-random-secret>"),
    ],
)
def test_settings_rejects_placeholder_optional_secrets(
    monkeypatch,
    tmp_path,
    name,
    value,
):
    set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=name):
        get_settings()


def test_settings_rejects_placeholder_multi_codex_tokens(monkeypatch, tmp_path):
    set_required_env(monkeypatch, tmp_path)
    monkeypatch.setenv(
        "CLOUDLINK_CODEX_TOKENS",
        json.dumps({"codex-a": "change-this-local-codex-token"}),
    )

    with pytest.raises(ValueError, match="CLOUDLINK_CODEX_TOKENS"):
        get_settings()
