import hashlib
from pathlib import Path

from worker.artifact_manager import ResultArtifactUploader


class FakeApiClient:
    def __init__(self):
        self.created = []
        self.uploaded = []

    def post_json(self, path, body, **kwargs):
        self.created.append((path, body))
        return {
            "id": "artifact-1",
            "relative_path": body["relative_path"],
            "title": body.get("title", ""),
            "description": body.get("description", ""),
            "meaning": body.get("meaning", ""),
            "size_bytes": body["size_bytes"],
            "sha256": body["sha256"],
            "status": "created",
        }

    def put_bytes(self, path, data, **kwargs):
        self.uploaded.append((path, data, kwargs))
        return {"status": "uploaded", "id": "artifact-1"}

    def get_json(self, path, **kwargs):
        return {
            "status": "created",
            "uploaded_bytes": 0,
            "size_bytes": 0,
            "sha256": "",
        }


def test_upload_large_output_returns_artifact_metadata(tmp_path):
    output = tmp_path / "event_efficiency.csv"
    content = b"alpha,beta\n1,2\n"
    output.write_bytes(content)
    api = FakeApiClient()
    uploader = ResultArtifactUploader(
        api,
        worker_id="worker-a",
        task_id="task-a",
        lease_id="lease-a",
        expected_artifacts=[
            {
                "path": "event_efficiency.csv",
                "title": "Event efficiency",
                "description": "Detailed rows.",
                "meaning": "Use for filtering.",
                "content_type": "text/csv",
                "required": True,
            }
        ],
    )

    entry = uploader.upload(output, tmp_path)

    assert entry["artifact_id"] == "artifact-1"
    assert entry["stored_on_server"] is True
    assert entry["sha256"] == hashlib.sha256(content).hexdigest()
    assert entry["title"] == "Event efficiency"
    assert api.created[0][0] == "/api/worker/tasks/task-a/artifacts"
    assert api.uploaded[0][0] == "/api/worker/tasks/task-a/artifacts/artifact-1/chunks/0"


class ResumableFakeApiClient:
    def __init__(self, content: bytes):
        self.content = content
        self.created = []
        self.uploaded = []
        self.completed = []
        self.uploaded_bytes = 0
        self.raise_timeout_once = True

    def post_json(self, path, body=None, **kwargs):
        if path == "/api/worker/tasks/task-a/artifacts":
            self.created.append((path, body))
            return {
                "id": "artifact-1",
                "relative_path": body["relative_path"],
                "title": body.get("title", ""),
                "description": body.get("description", ""),
                "meaning": body.get("meaning", ""),
                "size_bytes": body["size_bytes"],
                "sha256": body["sha256"],
                "status": "created",
            }
        if path == "/api/worker/tasks/task-a/artifacts/artifact-1/complete":
            self.completed.append(path)
            self.uploaded_bytes = len(self.content)
            return {"status": "uploaded", "id": "artifact-1"}
        raise AssertionError(f"unexpected post_json path: {path}")

    def get_json(self, path, **kwargs):
        assert path == "/api/worker/tasks/task-a/artifacts/artifact-1/upload-status"
        return {
            "status": "uploaded" if self.uploaded_bytes == len(self.content) else "uploading",
            "uploaded_bytes": self.uploaded_bytes,
            "size_bytes": len(self.content),
            "sha256": hashlib.sha256(self.content).hexdigest(),
        }

    def put_bytes(self, path, data, **kwargs):
        offset = int(Path(path).name)
        self.uploaded.append((path, data))
        assert offset == self.uploaded_bytes
        self.uploaded_bytes += len(data)
        if self.raise_timeout_once:
            self.raise_timeout_once = False
            raise TimeoutError("simulated response timeout after server accepted chunk")
        return {"status": "uploading", "uploaded_bytes": self.uploaded_bytes}


def test_upload_large_output_resumes_after_chunk_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_ARTIFACT_CHUNK_BYTES", "4")
    output = tmp_path / "large.csv"
    content = b"abcdefghij"
    output.write_bytes(content)
    api = ResumableFakeApiClient(content)
    uploader = ResultArtifactUploader(
        api,
        worker_id="worker-a",
        task_id="task-a",
        lease_id="lease-a",
    )

    entry = uploader.upload(output, tmp_path)

    assert entry["artifact_id"] == "artifact-1"
    assert entry["stored_on_server"] is True
    assert api.uploaded == [
        ("/api/worker/tasks/task-a/artifacts/artifact-1/chunks/0", b"abcd"),
        ("/api/worker/tasks/task-a/artifacts/artifact-1/chunks/4", b"efgh"),
        ("/api/worker/tasks/task-a/artifacts/artifact-1/chunks/8", b"ij"),
    ]
    assert api.completed == ["/api/worker/tasks/task-a/artifacts/artifact-1/complete"]


def test_upload_large_output_uses_artifact_retry_policy(tmp_path):
    output = tmp_path / "large.csv"
    output.write_bytes(b"abcdef")
    api = FakeApiClient()
    uploader = ResultArtifactUploader(
        api,
        worker_id="worker-a",
        task_id="task-a",
        lease_id="lease-a",
        upload_retries=6,
        retry_base_seconds=2,
        retry_max_seconds=60,
    )

    uploader.upload(output, tmp_path)

    assert api.uploaded[0][2] == {
        "retries": 6,
        "retry_base_seconds": 2,
        "retry_max_seconds": 60,
    }
