from urllib.parse import urlparse

from tests.test_tasks_api import admin_auth, make_client


def _installed_worker(client, platform="linux"):
    invite = client.post(
        "/api/admin/worker-install-invites",
        auth=admin_auth(),
        json={
            "platform": platform,
            "worker_id": "worker-remove-a",
            "display_name": "Worker Remove A",
        },
    ).json()
    token = [part for part in urlparse(invite["script_url"]).path.split("/") if part][2]
    register = client.post(
        f"/install/worker/{token}/register",
        json={"platform": platform},
    )
    assert register.status_code == 200
    return register.json()


def test_scripted_uninstall_requires_worker_confirmation(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)
    worker = _installed_worker(client)

    invite = client.post(
        f"/api/admin/workers/{worker['worker_id']}/uninstall-invite",
        auth=admin_auth(),
    )
    assert invite.status_code == 200
    script_path = urlparse(invite.json()["script_url"]).path
    token = [part for part in script_path.split("/") if part][2]
    script = client.get(script_path)
    assert script.status_code == 200
    assert "systemctl disable --now" in script.text
    assert 'rm -rf "$INSTALL_DIR/current"' in script.text
    assert '.cloudlink/datasets' not in script.text
    assert '.cloudlink/venvs' not in script.text

    before = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert any(item["worker_id"] == worker["worker_id"] for item in before["workers"])

    headers = {"X-Worker-Secret": worker["worker_secret"]}
    begin = client.post(
        f"/uninstall/worker/{token}/begin",
        headers=headers,
        json={"worker_id": worker["worker_id"]},
    )
    assert begin.status_code == 200
    complete = client.post(
        f"/uninstall/worker/{token}/complete",
        headers=headers,
        json={"worker_id": worker["worker_id"]},
    )
    assert complete.status_code == 200

    after = client.get("/api/admin/overview", auth=admin_auth()).json()
    assert all(item["worker_id"] != worker["worker_id"] for item in after["workers"])


def test_gpu_invite_requires_linux_absolute_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_PUBLIC_BASE_URL", "https://tasks.example.test")
    client = make_client(monkeypatch, tmp_path)

    macos = client.post(
        "/api/admin/worker-install-invites",
        auth=admin_auth(),
        json={
            "platform": "macos",
            "worker_id": "gpu-macos",
            "gpu_requested": True,
            "gpu_environment_path": "/opt/gpu",
        },
    )
    assert macos.status_code == 400

    relative = client.post(
        "/api/admin/worker-install-invites",
        auth=admin_auth(),
        json={
            "platform": "linux",
            "worker_id": "gpu-linux",
            "gpu_requested": True,
            "gpu_environment_path": "envs/gpu",
        },
    )
    assert relative.status_code == 400

    valid = client.post(
        "/api/admin/worker-install-invites",
        auth=admin_auth(),
        json={
            "platform": "linux",
            "worker_id": "gpu-linux",
            "gpu_requested": True,
            "gpu_environment_path": "/home/user/micromamba/envs/gpu",
        },
    )
    assert valid.status_code == 200
    script = client.get(urlparse(valid.json()["script_url"]).path)
    assert "micromamba is not available in PATH" in script.text
    assert "nvidia-smi is not available inside Linux/WSL" in script.text
    assert "validate_gpu_runtime" in script.text
