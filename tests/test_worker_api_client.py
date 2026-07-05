import io
import json
import http.client
import urllib.error

import pytest

from worker.api_client import ApiRequestError, WorkerApiClient


class FakeResponse:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload if payload is not None else {}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, *_args):
        return json.dumps(self.payload).encode("utf-8")


def test_post_json_retries_transient_errors_and_logs_endpoint(monkeypatch):
    attempts = []
    logs = []

    def fake_sleep(_seconds):
        return None

    def fake_urlopen(request, timeout):
        attempts.append((request.full_url, timeout))
        if len(attempts) < 3:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return FakeResponse(payload={"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", fake_sleep)

    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=2,
        retry_base_seconds=0.01,
        log=logs.append,
    )

    result = client.post_json("/api/worker/claim", {"worker_id": "worker-a"})

    assert result == {"ok": True}
    assert len(attempts) == 3
    assert attempts[-1][1] == 7
    assert any("endpoint=/api/worker/claim" in line for line in logs)
    assert any("attempt=1" in line for line in logs)


def test_post_json_uses_exponential_backoff_with_cap(monkeypatch):
    attempts = []
    sleeps = []

    def fake_sleep(seconds):
        sleeps.append(seconds)

    def fake_urlopen(request, timeout):
        attempts.append((request.full_url, timeout))
        if len(attempts) < 5:
            raise urllib.error.URLError(TimeoutError("timed out"))
        return FakeResponse(payload={"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", fake_sleep)
    monkeypatch.setattr("random.uniform", lambda _lower, _upper: 0)

    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=4,
        retry_base_seconds=2,
        retry_max_seconds=5,
    )

    result = client.post_json("/api/worker/claim", {"worker_id": "worker-a"})

    assert result == {"ok": True}
    assert len(attempts) == 5
    assert sleeps == [2, 4, 5, 5]


def test_post_json_does_not_retry_auth_failures(monkeypatch):
    attempts = []

    def fake_urlopen(request, timeout):
        attempts.append(request.full_url)
        raise urllib.error.HTTPError(
            request.full_url,
            403,
            "Forbidden",
            hdrs={},
            fp=io.BytesIO(b'{"detail":"forbidden"}'),
        )

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=5,
        retry_base_seconds=0.01,
    )

    with pytest.raises(ApiRequestError) as excinfo:
        client.post_json("/api/worker/claim", {"worker_id": "worker-a"})

    assert len(attempts) == 1
    assert excinfo.value.status_code == 403


def test_probe_json_can_run_without_auth(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["auth"] = request.headers.get("Authorization")
        captured["method"] = request.get_method()
        return FakeResponse(payload={"trace": "ok"})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=0,
        retry_base_seconds=0.01,
    )

    result = client.probe_json("GET", "/cdn-cgi/trace", auth=False)

    assert result == {"trace": "ok"}
    assert captured == {"auth": None, "method": "GET"}


def test_get_text_can_probe_cloudflare_trace_without_auth(monkeypatch):
    captured = {}

    class TextResponse(FakeResponse):
        def read(self, *_args):
            return b"colo=SIN\nhttp=http/1.1\n"

    def fake_urlopen(request, timeout):
        captured["auth"] = request.headers.get("Authorization")
        captured["timeout"] = timeout
        return TextResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=0,
        retry_base_seconds=0.01,
    )

    result = client.get_text("/cdn-cgi/trace", auth=False, timeout=3)

    assert "colo=SIN" in result
    assert captured == {"auth": None, "timeout": 3}


def test_put_bytes_sends_octet_stream_with_worker_auth(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["url"] = request.full_url
        captured["method"] = request.get_method()
        captured["auth"] = request.headers.get("Authorization")
        captured["content_type"] = request.get_header("Content-type")
        captured["data"] = request.data
        captured["timeout"] = timeout
        return FakeResponse(payload={"ok": True})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=0,
        retry_base_seconds=0.01,
    )

    result = client.put_bytes("/api/worker/tasks/task-a/artifacts/artifact-1/content", b"abc")

    assert result == {"ok": True}
    assert captured == {
        "url": "https://tasks.example.test/api/worker/tasks/task-a/artifacts/artifact-1/content",
        "method": "PUT",
        "auth": "Bearer secret",
        "content_type": "application/octet-stream",
        "data": b"abc",
        "timeout": 7,
    }


def test_put_bytes_retries_remote_disconnected(monkeypatch):
    attempts = []

    def fake_sleep(_seconds):
        return None

    def fake_urlopen(request, timeout):
        attempts.append((request.full_url, timeout))
        if len(attempts) == 1:
            raise http.client.RemoteDisconnected(
                "Remote end closed connection without response"
            )
        return FakeResponse(payload={"uploaded_bytes": 3})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", fake_sleep)
    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=2,
        retry_base_seconds=0.01,
    )

    result = client.put_bytes("/api/worker/tasks/task-a/artifacts/artifact-1/chunks/0", b"abc")

    assert result == {"uploaded_bytes": 3}
    assert len(attempts) == 2


def test_put_bytes_retries_http_500_for_resumable_uploads(monkeypatch):
    attempts = []

    def fake_sleep(_seconds):
        return None

    def fake_urlopen(request, timeout):
        attempts.append((request.full_url, timeout))
        if len(attempts) == 1:
            raise urllib.error.HTTPError(
                request.full_url,
                500,
                "Internal Server Error",
                hdrs={},
                fp=io.BytesIO(b'{"detail":"temporary upload failure"}'),
            )
        return FakeResponse(payload={"uploaded_bytes": 4194304})

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", fake_sleep)
    client = WorkerApiClient(
        base_url="https://tasks.example.test",
        worker_secret="secret",
        default_timeout=7,
        default_retries=2,
        retry_base_seconds=0.01,
    )

    result = client.put_bytes(
        "/api/worker/tasks/task-a/artifacts/artifact-1/chunks/4194304",
        b"abc",
    )

    assert result == {"uploaded_bytes": 4194304}
    assert len(attempts) == 2
