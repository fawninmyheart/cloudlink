import os
import sys
import threading
import time

import pytest

from worker.script_runner import (
    ScriptExecutionCancelled,
    ScriptExecutionTimeout,
    bounded_timeout,
    run_script_job,
)


def test_script_job_runs_python_in_cloudlink_runtime(monkeypatch, tmp_path):
    job_root = tmp_path / "jobs"
    install_calls = []

    def fake_runtime(requirements):
        install_calls.append(requirements)
        return sys.executable

    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(job_root))
    monkeypatch.setenv("CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS", "30")
    monkeypatch.setattr("worker.script_runner.ensure_python_auto_runtime", fake_runtime)

    result, logs = run_script_job(
        {
            "runtime": "python-auto",
            "script": (
                "from pathlib import Path\n"
                "print('hello from script')\n"
                "Path('outputs/result.txt').write_text('42', encoding='utf-8')\n"
            ),
            "requirements": ["requests==2.32.3"],
            "timeout_seconds": 5,
        },
        "local-worker-1",
        task_id="task-1",
    )

    assert install_calls == [["requests==2.32.3"]]
    assert result["exit_code"] == 0
    assert result["stdout"] == "hello from script\n"
    assert result["stderr"] == ""
    assert result["output_files"] == [
        {"path": "result.txt", "size_bytes": 2, "content": "42"}
    ]
    assert result["runtime"] == "python-auto"
    assert result["worker_id"] == "local-worker-1"
    assert result["job_dir"] == str(job_root / "task-1")
    assert sys.executable in logs


def test_script_job_passes_args_stdin_and_input_files(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )

    result, _logs = run_script_job(
        {
            "script": (
                "import sys\n"
                "from pathlib import Path\n"
                "name = Path('inputs/name.txt').read_text(encoding='utf-8')\n"
                "print(name + ':' + sys.stdin.read() + ':' + ','.join(sys.argv[1:]))\n"
            ),
            "stdin": "payload",
            "args": ["--mode", "fast"],
            "input_files": [{"path": "inputs/name.txt", "content": "cloudlink"}],
        },
        "local-worker-1",
    )

    assert result["exit_code"] == 0
    assert result["stdout"] == "cloudlink:payload:--mode,fast\n"


def test_script_job_uploads_large_outputs_with_artifact_uploader(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("CLOUDLINK_OUTPUT_FILE_MAX_BYTES", "5")
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )

    class FakeUploader:
        def __init__(self):
            self.uploaded = []

        def upload(self, path, output_dir):
            self.uploaded.append(path.name)
            return {
                "path": str(path.relative_to(output_dir)),
                "size_bytes": path.stat().st_size,
                "sha256": "abc",
                "content_omitted": True,
                "stored_on_server": True,
                "artifact_id": "artifact-1",
                "download_url": "/api/internal/tasks/task-large/artifacts/artifact-1/download",
            }

    uploader = FakeUploader()
    result, _logs = run_script_job(
        {
            "script": (
                "from pathlib import Path\n"
                "Path('outputs/big.txt').write_text('abcdef', encoding='utf-8')\n"
            )
        },
        "worker-a",
        task_id="task-large",
        artifact_uploader=uploader,
    )

    assert uploader.uploaded == ["big.txt"]
    assert result["output_files"][0]["stored_on_server"] is True
    assert "content" not in result["output_files"][0]


def test_script_job_persists_completion_before_artifact_upload(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("CLOUDLINK_OUTPUT_FILE_MAX_BYTES", "5")
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )
    events = []

    class FailingUploader:
        def upload(self, _path, _output_dir):
            events.append("upload")
            raise RuntimeError("network disconnected")

    def execution_completed(result, logs, output_dir, manifest):
        events.append("persist")
        assert result["exit_code"] == 0
        assert "output_files" not in result
        assert "exit_code=0" in logs
        assert output_dir.is_dir()
        assert manifest == {}

    with pytest.raises(RuntimeError, match="network disconnected"):
        run_script_job(
            {
                "script": (
                    "from pathlib import Path\n"
                    "Path('outputs/big.bin').write_bytes(b'abcdef')\n"
                )
            },
            "worker-a",
            task_id="task-persist-first",
            artifact_uploader=FailingUploader(),
            execution_completed=execution_completed,
        )

    assert events == ["persist", "upload"]


def test_failed_script_uploads_generated_outputs_before_raising(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )
    uploaded = []
    persisted = []

    class UploadAll:
        upload_all = True

        def upload(self, path, output_dir):
            uploaded.append(str(path.relative_to(output_dir)))
            return {
                "path": str(path.relative_to(output_dir)),
                "size_bytes": path.stat().st_size,
                "artifact_id": f"artifact-{len(uploaded)}",
            }

    with pytest.raises(RuntimeError, match="exit code 3"):
        run_script_job(
            {
                "script": (
                    "from pathlib import Path\n"
                    "Path('outputs/diagnostic.txt').write_text('partial result')\n"
                    "raise SystemExit(3)\n"
                )
            },
            "worker-a",
            task_id="task-failed-results",
            artifact_uploader=UploadAll(),
            execution_completed=lambda result, *_args: persisted.append(result),
        )

    assert persisted[0]["exit_code"] == 3
    assert uploaded == ["diagnostic.txt"]


def test_timed_out_script_uploads_outputs_written_before_termination(
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )
    uploaded = []
    persisted = []

    class UploadAll:
        upload_all = True

        def upload(self, path, output_dir):
            relative = str(path.relative_to(output_dir))
            uploaded.append(relative)
            return {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "artifact_id": "artifact-timeout",
            }

    def fake_run_process(_command, **kwargs):
        (kwargs["cwd"] / "outputs" / "progress.json").write_text("{}")
        raise ScriptExecutionTimeout(
            "execution timed out",
            timeout_seconds=5,
            stdout="partial stdout",
        )

    monkeypatch.setattr("worker.script_runner.run_process", fake_run_process)

    with pytest.raises(ScriptExecutionTimeout):
        run_script_job(
            {"script": "print('unreached')"},
            "worker-a",
            task_id="task-timeout-results",
            artifact_uploader=UploadAll(),
            execution_completed=lambda result, *_args: persisted.append(result),
        )

    assert persisted[0]["_delivery_status"] == "timeout"
    assert persisted[0]["_delivery_error_code"] == "execution_timeout"
    assert uploaded == ["progress.json"]


def test_result_directory_upload_is_not_limited_to_200_files(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )
    uploaded = []

    class UploadAll:
        upload_all = True

        def upload(self, path, output_dir):
            relative = str(path.relative_to(output_dir))
            uploaded.append(relative)
            return {
                "path": relative,
                "size_bytes": path.stat().st_size,
                "artifact_id": f"artifact-{len(uploaded)}",
            }

    result, _logs = run_script_job(
        {
            "script": (
                "from pathlib import Path\n"
                "out = Path('outputs')\n"
                "[(out / f'{index:03}.txt').write_text(str(index)) "
                "for index in range(205)]\n"
            )
        },
        "worker-a",
        task_id="task-many-results",
        artifact_uploader=UploadAll(),
    )

    assert len(uploaded) == 205
    assert len(result["output_files"]) == 205


def test_script_job_uses_runtime_artifact_manifest(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("CLOUDLINK_OUTPUT_FILE_MAX_BYTES", "5")
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )

    class FakeUploader:
        def __init__(self, manifest=None):
            self.manifest = manifest or {}

        def with_manifest(self, manifest):
            return FakeUploader(manifest)

        def upload(self, path, output_dir):
            item = self.manifest["artifacts"][0]
            return {
                "path": str(path.relative_to(output_dir)),
                "title": item["title"],
                "description": item["description"],
                "meaning": item["meaning"],
                "size_bytes": path.stat().st_size,
                "sha256": "abc",
                "content_omitted": True,
                "stored_on_server": True,
                "artifact_id": "artifact-1",
                "download_url": "/api/internal/tasks/task-manifest/artifacts/artifact-1/download",
            }

    result, _logs = run_script_job(
        {
            "script": (
                "from pathlib import Path\n"
                "Path('outputs/big.csv').write_text('abcdef', encoding='utf-8')\n"
                "Path('outputs/cloudlink_artifacts.json').write_text("
                "'{\"artifacts\":[{\"path\":\"big.csv\",\"title\":\"Runtime title\","
                "\"description\":\"Runtime rows.\",\"meaning\":\"Use downstream.\"}]}',"
                " encoding='utf-8')\n"
            )
        },
        "worker-a",
        task_id="task-manifest",
        artifact_uploader=FakeUploader(),
    )

    assert [item["path"] for item in result["output_files"]] == ["big.csv"]
    assert result["output_files"][0]["title"] == "Runtime title"
    assert result["output_files"][0]["meaning"] == "Use downstream."


def test_script_job_rejects_unknown_runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))

    with pytest.raises(ValueError, match="runtime"):
        run_script_job(
            {"runtime": "shell", "script": "echo no"},
            "local-worker-1",
        )


def test_script_job_raises_execution_timeout(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))
    monkeypatch.setenv("CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )

    with pytest.raises(ScriptExecutionTimeout) as excinfo:
        run_script_job(
            {
                "script": "import time\ntime.sleep(2)\n",
                "timeout_seconds": 1,
            },
            "local-worker-1",
            task_id="task-timeout",
        )

    assert excinfo.value.error_code == "execution_timeout"


def test_script_job_timeout_terminates_descendant_processes(monkeypatch, tmp_path):
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(job_root))
    monkeypatch.setenv("CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS", "5")
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )

    with pytest.raises(ScriptExecutionTimeout):
        run_script_job(
            {
                "script": (
                    "import subprocess,sys,time\n"
                    "from pathlib import Path\n"
                    "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(60)'])\n"
                    "Path('outputs/child.pid').write_text(str(child.pid))\n"
                    "time.sleep(60)\n"
                ),
                "timeout_seconds": 1,
            },
            "local-worker-1",
            task_id="task-tree-timeout",
        )

    child_pid = int(
        (job_root / "task-tree-timeout" / "outputs" / "child.pid").read_text()
    )
    deadline = time.time() + 2
    while time.time() < deadline:
        try:
            os.kill(child_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.05)
    else:
        pytest.fail("descendant process survived task timeout")


def test_script_job_honors_cancel_event_and_isolates_attempt_directory(
    monkeypatch,
    tmp_path,
):
    job_root = tmp_path / "jobs"
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(job_root))
    monkeypatch.setattr(
        "worker.script_runner.ensure_python_auto_runtime",
        lambda requirements: sys.executable,
    )
    cancel_event = threading.Event()
    timer = threading.Timer(0.2, cancel_event.set)
    timer.start()
    try:
        with pytest.raises(ScriptExecutionCancelled):
            run_script_job(
                {
                    "script": "import time\ntime.sleep(60)\n",
                    "timeout_seconds": 30,
                },
                "local-worker-1",
                task_id="task-cancel",
                lease_id="lease-attempt-1",
                cancel_event=cancel_event,
            )
    finally:
        timer.cancel()

    assert (job_root / "task-cancel" / "lease-attempt-1" / "main.py").is_file()


def test_timeout_allows_explicit_multi_day_jobs(monkeypatch):
    monkeypatch.delenv("CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS", raising=False)
    assert bounded_timeout(14 * 24 * 60 * 60) == 14 * 24 * 60 * 60


def test_timeout_rejects_instead_of_silently_clamping(monkeypatch):
    monkeypatch.setenv("CLOUDLINK_SCRIPT_MAX_TIMEOUT_SECONDS", "100")
    with pytest.raises(ValueError, match="exceeds worker maximum"):
        bounded_timeout(101)


def test_script_job_rejects_unsafe_input_path(monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDLINK_JOB_ROOT", str(tmp_path / "jobs"))

    with pytest.raises(ValueError, match="path"):
        run_script_job(
            {
                "script": "print('ok')",
                "input_files": [{"path": "../outside.txt", "content": "bad"}],
            },
            "local-worker-1",
        )
