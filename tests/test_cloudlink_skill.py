from pathlib import Path


SKILL_PATH = Path(".agents/skills/cloudlink-local-compute/SKILL.md")


def test_skill_requires_resource_preflight_and_automatic_rejection_handling():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "scheduler-visible" in text
    assert "resource_request" in text
    assert "resource_request_unsatisfiable" in text
    assert "Do not ask the user" in text
    assert "parallel" in text
    assert "并发提交" in text
    assert "不要一个一个" in text


def test_skill_defines_queue_pressure_rules_and_task_ownership():
    text = SKILL_PATH.read_text(encoding="utf-8")

    assert "/api/internal/queue/status" in text
    assert "max_pending" in text
    assert "queue_timeout" in text
    assert "execution_timeout" in text
    assert "只返回自己提交的任务" in text
    assert "服务端只返回事实状态" in text
    assert "needs_update" in text
    assert "minimum_worker_version" in text


def test_dashboard_contains_resource_capacity_labels():
    text = Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "可调度资源" in text
    assert "当前可用 / 上限" in text
    assert "workerDiskTotal" in text
    assert "workerDiskFree" in text
    assert 'resourceRow("任务盘", workerDiskTotal(worker, "job"), workerDiskFree(worker, "job"), bytes)' in text
    assert 'resourceRow("数据盘", workerDiskTotal(worker, "dataset"), workerDiskFree(worker, "dataset"), bytes)' in text
    assert "需要更新" in text
    assert "reported_hardware_profile" in text
    assert "待心跳同步" in text
    assert "原始空闲" not in text
    assert "rawDiskLine" not in text
    assert "资源申请" in text
    assert "并发" in text


def test_dashboard_uses_worker_cards_and_task_modal():
    text = Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "worker-card" in text
    assert "worker-card-list" in text
    assert "task-modal" in text
    assert "worker-settings-modal" in text
    assert "data-open-worker-settings" in text
    assert "/api/admin/workers/" in text
    assert "/settings" in text
    assert 'id="details-panel"' not in text
    assert "renderTaskDetails" not in text
    assert "data-save-concurrency" not in text
    assert "编辑并发" not in text


def test_dashboard_removes_redundant_task_type_labels():
    text = Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "连通测试" not in text
    assert "日报生成" not in text
    assert "脚本任务" not in text


def test_dashboard_refresh_is_interaction_friendly():
    text = Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "isInteractiveBusy" in text
    assert "workerSettingsState.dirty" in text
    assert "refresh({forceWorkers: true})" in text
    assert "grid-template-columns: minmax(0, 2.2fr) minmax(320px, .8fr)" in text


def test_dashboard_shows_dataset_root_validation_status():
    text = Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "数据盘验证" in text
    assert "待验证" in text
    assert "rootValidationFor" in text
    assert "dataset_root_checks" in text


def test_dashboard_refreshes_open_worker_settings_validation_when_clean():
    text = Path("app/dashboard.py").read_text(encoding="utf-8")

    assert "refreshOpenWorkerSettingsValidation" in text
    assert "isWorkerSettingsOpen() && !workerSettingsState.dirty" in text


def test_security_boundaries_are_documented_for_open_source_users():
    readme = Path("README.md").read_text(encoding="utf-8")
    skill = SKILL_PATH.read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")
    personal_domain = "tasks." + "sun" + "fawn" + ".com"
    personal_home = "/Users/" + "hua"
    personal_host = "sun" + "fawn"

    assert "not a sandbox" in readme
    assert "CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS" in readme
    assert "CLOUDLINK_ALLOW_INSECURE_WORKER_INSTALL" in readme
    assert "not a security sandbox" in skill
    assert "CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS" in skill
    assert personal_domain not in env_example
    assert personal_home not in env_example
    assert personal_host not in env_example


def test_repository_has_basic_ci_for_open_source_contributions():
    workflow = Path(".github/workflows/ci.yml")

    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "pytest" in text
    assert "pull_request" in text
