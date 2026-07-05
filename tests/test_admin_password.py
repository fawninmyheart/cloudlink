from tests.test_tasks_api import admin_auth, make_client


def test_admin_can_change_password_and_new_password_takes_over(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/admin/password",
        auth=admin_auth(),
        json={
            "current_password": "admin-pass",
            "new_password": "new-admin-pass",
            "confirm_password": "new-admin-pass",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert client.get("/api/admin/overview", auth=admin_auth()).status_code == 401
    assert (
        client.get("/api/admin/overview", auth=("admin", "new-admin-pass")).status_code
        == 200
    )


def test_admin_password_change_rejects_wrong_current_password(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/admin/password",
        auth=admin_auth(),
        json={
            "current_password": "wrong-pass",
            "new_password": "new-admin-pass",
            "confirm_password": "new-admin-pass",
        },
    )

    assert response.status_code == 403
    assert client.get("/api/admin/overview", auth=admin_auth()).status_code == 200


def test_admin_password_change_rejects_mismatched_confirmation(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/admin/password",
        auth=admin_auth(),
        json={
            "current_password": "admin-pass",
            "new_password": "new-admin-pass",
            "confirm_password": "different-pass",
        },
    )

    assert response.status_code == 400


def test_admin_password_change_rejects_short_password(monkeypatch, tmp_path):
    client = make_client(monkeypatch, tmp_path)

    response = client.post(
        "/api/admin/password",
        auth=admin_auth(),
        json={
            "current_password": "admin-pass",
            "new_password": "short",
            "confirm_password": "short",
        },
    )

    assert response.status_code == 422
