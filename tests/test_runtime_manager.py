from worker.runtime_manager import ensure_python_auto_runtime


def test_python_auto_runtime_creates_dedicated_venv_and_installs_missing_requirements(
    monkeypatch,
    tmp_path,
):
    venv_path = tmp_path / "cloudlink" / "venvs" / "python-auto"
    calls = []

    def fake_run(command, check, timeout):
        calls.append(command)
        if command[1:] == ["-m", "venv", str(venv_path)]:
            python_path = venv_path / "bin" / "python"
            python_path.parent.mkdir(parents=True, exist_ok=True)
            python_path.write_text("# fake python\n", encoding="utf-8")

    monkeypatch.setenv("CLOUDLINK_PYTHON_AUTO_VENV", str(venv_path))
    monkeypatch.setenv("CLOUDLINK_BASE_PYTHON", "/usr/bin/python3")
    monkeypatch.setattr("worker.runtime_manager.subprocess.run", fake_run)

    python_path = ensure_python_auto_runtime(["requests==2.32.3"])

    assert python_path == venv_path / "bin" / "python"
    assert calls == [
        ["/usr/bin/python3", "-m", "venv", str(venv_path)],
        [str(python_path), "-m", "pip", "install", "requests==2.32.3"],
    ]
    state_file = venv_path / ".cloudlink-installed-requirements.txt"
    assert state_file.read_text(encoding="utf-8") == "requests==2.32.3\n"

    calls.clear()
    ensure_python_auto_runtime(["requests==2.32.3"])

    assert calls == []
