from urllib.parse import urlparse
import hashlib
import io
import tarfile

from tests.test_tasks_api import admin_auth, make_client


def create_invite(client, platform="macos", worker_id="worker-install-a"):
    response = client.post(
        "/api/admin/worker-install-invites",
        auth=admin_auth(),
        json={
            "platform": platform,
            "worker_id": worker_id,
            "display_name": "Install Test Worker",
        },
    )
    assert response.status_code == 200
    return response.json()


def token_from_url(url):
    parts = [part for part in urlparse(url).path.split("/") if part]
    return parts[2]


def test_admin_creates_macos_worker_install_invite_without_exposing_secret(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)

    invite = create_invite(client, platform="macos")

    assert invite["platform"] == "macos"
    assert invite["worker_id"] == "worker-install-a"
    assert len(invite["package_sha256"]) == 64
    assert invite["command"].startswith("curl -fsSL ")
    assert invite["script_url"].startswith("https://tasks.example.test/install/worker/")
    assert "test-secret" not in invite["command"]

    script = client.get(urlparse(invite["script_url"]).path)
    assert script.status_code == 200
    assert "package.tar.gz" in script.text
    assert "/register" in script.text
    assert "curl -fsSL -X POST" in script.text
    assert 'pkill -f "worker.local_worker"' in script.text
    assert "Existing Cloudlink worker processes stopped." in script.text
    assert "urllib.request" not in script.text
    assert "test-secret" not in script.text
    assert invite["package_sha256"] in script.text
    assert "sha256sum" in script.text or "shasum -a 256" in script.text
    assert "env_file.chmod(0o600)" in script.text


def test_admin_creates_windows_worker_install_invite(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)

    invite = create_invite(client, platform="windows")

    assert invite["platform"] == "windows"
    assert "powershell" in invite["command"].lower()
    assert invite["script_url"].endswith("/install.ps1")

    script = client.get(urlparse(invite["script_url"]).path)
    assert script.status_code == 200
    assert "Invoke-RestMethod" in script.text
    assert 'CommandLine -like "*worker.local_worker*"' in script.text
    assert "Existing Cloudlink worker processes stopped." in script.text
    assert "test-secret" not in script.text
    assert invite["package_sha256"] in script.text
    assert "Get-FileHash -Algorithm SHA256" in script.text
    assert "icacls" in script.text


def test_worker_install_invite_rejects_public_http_base_url_by_default(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "http://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/admin/worker-install-invites",
        auth=admin_auth(),
        json={"platform": "linux", "worker_id": "worker-insecure"},
    )

    assert response.status_code == 400
    assert "HTTPS" in response.json()["detail"]


def test_worker_install_invite_can_explicitly_allow_insecure_local_testing(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "http://tasks.example.test")
    monkeypatch.setenv("CLOUDLINK_ALLOW_INSECURE_WORKER_INSTALL", "1")
    client = make_client(monkeypatch, tmp_path)

    invite = create_invite(client, platform="linux", worker_id="worker-insecure")

    assert invite["script_url"].startswith("http://tasks.example.test/")


def test_worker_install_registers_worker_once(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)
    invite = create_invite(client, platform="linux", worker_id="linux-worker-a")
    token = token_from_url(invite["script_url"])

    package = client.get(f"/install/worker/{token}/package.tar.gz")
    assert package.status_code == 200
    assert hashlib.sha256(package.content).hexdigest() == invite["package_sha256"]
    assert package.content.startswith(b"\x1f\x8b")
    with tarfile.open(fileobj=io.BytesIO(package.content), mode="r:gz") as archive:
        names = set(archive.getnames())
    assert "cloudlink/app/version.py" in names

    register = client.post(
        f"/install/worker/{token}/register",
        json={"hostname": "linux-host", "platform": "linux"},
    )

    assert register.status_code == 200
    body = register.json()
    assert body["worker_id"] == "linux-worker-a"
    assert body["worker_secret"] != "test-secret"
    assert body["api_base_url"] == "https://tasks.example.test"
    assert "WORKER_ID=linux-worker-a" in body["env"]
    assert f"WORKER_SECRET={body['worker_secret']}" in body["env"]
    assert "WORKER_API_RETRY_MAX_SECONDS=15" in body["env"]
    assert "CLOUDLINK_ARTIFACT_UPLOAD_RETRIES=6" in body["env"]
    assert "CLOUDLINK_ARTIFACT_RETRY_BASE_SECONDS=2" in body["env"]
    assert "CLOUDLINK_ARTIFACT_RETRY_MAX_SECONDS=60" in body["env"]

    repeat = client.post(
        f"/install/worker/{token}/register",
        json={"hostname": "linux-host", "platform": "linux"},
    )
    assert repeat.status_code == 410

    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert overview["workers"][0]["worker_id"] == "linux-worker-a"
    assert overview["workers"][0]["install_platform"] == "linux"


def test_worker_install_package_is_deterministic_and_matches_invite_hash(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)
    invite = create_invite(client, platform="linux", worker_id="linux-worker-a")
    token = token_from_url(invite["script_url"])

    first = client.get(f"/install/worker/{token}/package.tar.gz")
    second = client.get(f"/install/worker/{token}/package.tar.gz")

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.content == second.content
    assert hashlib.sha256(first.content).hexdigest() == invite["package_sha256"]


def test_worker_install_register_preserves_macos_platform_for_update_command(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)
    invite = create_invite(client, platform="macos", worker_id="mac-worker-a")
    token = token_from_url(invite["script_url"])

    register = client.post(
        f"/install/worker/{token}/register",
        json={"hostname": "mac-host", "platform": "darwin"},
    )

    assert register.status_code == 200
    overview = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert overview["workers"][0]["worker_id"] == "mac-worker-a"
    assert overview["workers"][0]["install_platform"] == "macos"


def test_installed_worker_secret_cannot_impersonate_another_worker(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)

    invite_a = create_invite(client, platform="linux", worker_id="linux-worker-a")
    token_a = token_from_url(invite_a["script_url"])
    secret_a = client.post(
        f"/install/worker/{token_a}/register",
        json={"hostname": "linux-a", "platform": "linux"},
    ).json()["worker_secret"]

    invite_b = create_invite(client, platform="linux", worker_id="linux-worker-b")
    token_b = token_from_url(invite_b["script_url"])
    secret_b = client.post(
        f"/install/worker/{token_b}/register",
        json={"hostname": "linux-b", "platform": "linux"},
    ).json()["worker_secret"]
    assert secret_a != secret_b

    response = client.post(
        "/api/worker/heartbeat",
        headers={"Authorization": f"Bearer {secret_a}"},
        json={"worker_id": "linux-worker-b", "supported_types": ["script_job"]},
    )

    assert response.status_code == 401


def test_worker_install_package_includes_shared_resource_model(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)
    invite = create_invite(client, platform="linux", worker_id="linux-worker-a")
    token = token_from_url(invite["script_url"])

    package = client.get(f"/install/worker/{token}/package.tar.gz")

    assert package.status_code == 200
    with tarfile.open(fileobj=io.BytesIO(package.content), mode="r:gz") as archive:
        names = set(archive.getnames())
    assert "cloudlink/app/__init__.py" in names
    assert "cloudlink/app/resource_model.py" in names
    assert "cloudlink/worker/hardware.py" in names


def test_dashboard_exposes_password_and_worker_install_controls():
    text = __import__("pathlib").Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "修改密码" in text
    assert "添加节点" in text
    assert "worker-install-modal" in text
    assert "data-open-worker-install-command" in text
    assert "获取部署命令" in text
    assert "inferWorkerInstallPlatform" in text
    assert "openWorkerInstallModal(button.dataset.openWorkerInstallCommand)" in text
    assert "/api/admin/password" in text
    assert "/api/admin/worker-install-invites" in text


def test_dashboard_platform_inference_treats_darwin_as_macos_not_windows():
    text = __import__("pathlib").Path("app/dashboard.py").read_text(encoding="utf-8")

    assert 'hint === "darwin"' in text
    assert 'hint === "windows"' in text
    assert text.index('hint === "darwin"') < text.index('hint === "windows"')
    assert 'system.includes("win")' not in text


def test_dashboard_keeps_stable_local_render_state():
    text = __import__("pathlib").Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "function stableSortByKey" in text
    assert "const renderState" in text
    assert "renderDatasetCachesIfChanged" in text
    assert "worker.reserved_resources" in text
    assert "reserve-overrides" in text


def test_readme_documents_codex_github_install_and_dashboard_worker_installs():
    text = __import__("pathlib").Path("README.md").read_text(encoding="utf-8")

    assert "Codex CLI Deployment Runbook" in text
    assert "git clone" in text
    assert "CLOUDLINK_PUBLIC_BASE_URL" in text
    assert "添加节点" in text
    assert "/install/worker/<token>/" in text
    assert "PBKDF2" in text
