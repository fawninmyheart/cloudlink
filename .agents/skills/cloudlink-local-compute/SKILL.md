---
name: cloudlink-local-compute
description: Use when Codex CLI on the cloud server needs to offload resource-heavy Python computation to a registered local Cloudlink worker instead of running it on the server.
---

# Cloudlink Local Compute

## Overview

Cloudlink lets the cloud server submit a generated Python script to a registered local compute node, wait for execution, and continue analysis from the returned stdout, stderr, exit code, and output files.

Core rule: the cloud-side Codex CLI generates scripts; the local worker executes scripts. The local worker must not invoke Codex CLI.

Security boundary: Cloudlink is not a security sandbox. A `script_job` is trusted
Python code running on the registered worker's OS account inside a dedicated
Python virtualenv, not inside a kernel/container/network sandbox. Do not submit
code intended to test isolation, read secrets, scan private files, or bypass the
worker owner's trust boundary.

## When To Use

Use this skill when a cloud-side Codex session needs computation that is too heavy, memory-hungry, dependency-heavy, or time-consuming for the cloud server.

Good uses:

- Large data analysis, simulation, batch parsing, model evaluation, or CPU-heavy Python work.
- Jobs that can run non-interactively and finish with stdout plus files under `outputs/`.
- Work where the cloud server can safely generate a self-contained Python script.
- Jobs that need large server-provided data already registered as Cloudlink managed datasets.

Do not use this skill for:

- Small shell checks, file reads, service restarts, or lightweight commands that the cloud server can run directly.
- Interactive programs, long-running daemons, GUI automation, browser automation, or jobs requiring user input.
- Arbitrary shell commands. Cloudlink `script_job` runs Python scripts, not shell scripts.
- Secrets collection or exfiltration. Do not put secrets in payloads, stdout, stderr, or output files.
- Unknown or unregistered workers. Workers must already be registered by the server.
- Bulk dataset transfer in task JSON. Large data must be registered as a Cloudlink dataset and referenced by id.
- Registering datasets from paths outside the server's `CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS` when using the Codex token.

## Required Runtime Context

Run commands from the cloud server where Cloudlink is deployed:

```bash
cd /opt/cloudlink
```

Required files and service:

- `/opt/cloudlink/scripts/submit_local_script_job.py`
- `/opt/cloudlink/scripts/cloudlink_client.py`
- `/opt/cloudlink/.codex-token` for restricted cloud-side Codex CLI access
- `/etc/cloudlink.env`
- `cloudlink.service` running on `127.0.0.1:8010`
- A registered local worker that supports `script_job`

Internal task creation and querying must use the server-local API through `127.0.0.1:8010`. Do not submit internal tasks through the public Cloudflare domain.

Cloud-side Codex CLI must not use `sudo` to read `/etc/cloudlink.env`. Use the limited local Codex token loaded by `/opt/cloudlink/scripts/cloudlink_client.py`.
The Codex token can register reusable datasets only from
`CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS`; move or symlink approved source files
into that import area before registration.

The local worker reaches worker APIs through:

```text
https://tasks.example.com
```

## Preflight Checks

Before submitting a heavy job, verify the service and worker state when practical:

```bash
systemctl is-active cloudlink
```

For worker state, use the limited internal status endpoint through the local helper. Do not print secrets:

```bash
cd /opt/cloudlink
.venv/bin/python - <<'PY'
import json, sys

sys.path.insert(0, "/opt/cloudlink/scripts")
from cloudlink_client import request_json

data = request_json("GET", "/api/internal/status")
print(json.dumps({
    "summary": data["summary"],
    "resource_status": data["resource_status"],
    "workers": [
        {
            "worker_id": worker["worker_id"],
            "online": worker["online"],
            "enabled": worker["enabled"],
            "needs_update": worker.get("needs_update", False),
            "worker_version": worker.get("worker_version"),
            "minimum_worker_version": worker.get("minimum_worker_version")
            or worker.get("required_version"),
            "server_version": worker.get("server_version"),
            "supported_types": worker["supported_types"],
            "last_error": worker.get("last_error"),
        }
        for worker in data["workers"]
    ],
}, ensure_ascii=False))
PY
```

Proceed only if at least one enabled worker supports `script_job`, is online, and does not have `needs_update=true`. `needs_update` means the worker is below the server's `minimum_worker_version`, not merely different from `server_version`. Treat `needs_update` workers as unavailable until the user updates the local worker from the console deployment command. If no schedulable worker is online, submission may remain pending until a compatible worker starts.

Before any batch or parallel submission, query the queue facts:

```bash
cd /opt/cloudlink
.venv/bin/python - <<'PY'
import json, sys

sys.path.insert(0, "/opt/cloudlink/scripts")
from cloudlink_client import request_json

print(json.dumps(request_json("GET", "/api/internal/queue/status"), ensure_ascii=False, indent=2))
PY
```

服务端只返回事实状态：`pending_count`, `max_pending`, `running_count`, `queue_timeout_seconds`, `oldest_pending_age_seconds`, `online_worker_count`, and aggregate `resource_totals`. It does not tell Codex CLI what action to take. Codex CLI must decide from this skill.

Queue rules for Codex CLI:

- If `pending_count >= max_pending`, do not submit more tasks. Poll your own tasks, cancel obsolete pending tasks, or wait before retrying.
- If `oldest_pending_age_seconds` approaches `queue_timeout_seconds`, do not add unrelated small tasks that can starve older large tasks.
- If a task returns `error_code=queue_timeout`, treat it as never executed; reduce queue pressure, lower parallelism, or split differently before resubmitting.
- If a task returns `error_code=execution_timeout`, treat it as executed too long; reduce runtime scope, improve the script, or raise `--timeout` only when resources and workflow justify it.
- Cloudlink enforces `max_pending` globally. Codex CLI must not loop on rejected submissions.

For resource-heavy work, inspect scheduler-visible resources before designing the script. Use `hardware_profile.scheduler` and `capacity_state`; do not design against raw physical capacity because Cloudlink subtracts CPU, memory, disk, and GPU reserve for the local operating system before exposing capacity.

Cloud-side Codex CLI must estimate CPU cores, peak memory, temporary job disk, dataset cache or extracted-data disk, expected runtime, and GPU requirements before submitting. If the work can be split into independent chunks and workers have capacity, submit parallel Cloudlink tasks instead of waiting for one large sequential task. Keep each task's `resource_request` honest, and keep total parallelism inside visible worker capacity.

并发提交规则：当分析可以拆成相互独立的年份、品种、参数段、文件分片或候选集合时，应优先一次性提交多个 Cloudlink 任务，并在所有任务完成后汇总结果；不要一个一个任务提交、等待、再提交，除非后一个任务确实依赖前一个任务的输出。提交前仍要检查当前在线 worker 的可调度资源与并发上限，并让每个任务声明自己的 `resource_request`。

Ownership rule: Codex CLI internal queries only return tasks submitted by the same Codex submitter token, plus total resource occupancy and free-capacity facts. 只返回自己提交的任务；do not assume missing task ids belong to the current session. Use `GET /api/internal/tasks` to list your own tasks, and `POST /api/internal/tasks/<task_id>/cancel` only for your own obsolete pending tasks.

If Cloudlink rejects task creation with `resource_request_unsatisfiable`, handle it automatically. Reduce scope, split the workload, stream or batch input data, lower concurrency, or request less memory/disk and resubmit. Do not ask the user just because the scheduler rejected an oversized first attempt. Only report back after repeated automatic reductions still cannot produce a valid task.

## Script Contract

Generate one Python script file. The script must:

- Run non-interactively.
- Create directories before writing files, or write only under `outputs/`.
- Put durable artifacts under `outputs/`.
- For non-trivial jobs, write `outputs/cloudlink_artifacts.json` describing important output files, actual metrics, and why the result matters.
- Print a concise result summary to stdout.
- Exit with code `0` on success and nonzero on failure.
- Avoid absolute local paths unless the task explicitly needs them and they are known to exist on the local worker.
- Avoid reading local secrets or user-private files unless the user explicitly asked for that exact data path.

Cloudlink returns:

- `result.summary`
- `result.stdout`
- `result.stderr`
- `result.exit_code`
- `result.worker_id`
- `result.job_dir`
- `result.output_files`

Small output files include `content` for UTF-8 text or `content_base64` for binary. Large uploaded artifacts include `stored_on_server=true`, `artifact_id`, `download_url`, `sha256`, `title`, `description`, and `meaning`.

Resource-aware tasks should include `payload.resource_request`:

```json
{
  "cpu_cores": 4,
  "memory_bytes": 17179869184,
  "job_disk_bytes": 5368709120,
  "dataset_disk_bytes": 0,
  "expected_runtime_seconds": 1800,
  "concurrency_slots": 1,
  "gpu": {
    "required": false,
    "count": 0,
    "memory_bytes": 0
  }
}
```

Use scheduler-visible units, not physical machine totals. If unsure, choose a conservative estimate and split the job into smaller independent tasks.

For important outputs:

- Always provide a human-readable task title and description at submission time.
- Declare expected outputs with `--expected-artifact`.
- Ask the script to write `outputs/cloudlink_artifacts.json` with actual result meaning and metrics when those details are known only after execution.
- If `result.output_files` contains `stored_on_server=true`, download it through `/api/internal/tasks/<task_id>/artifacts/<artifact_id>/download`.

## Dependency Rules

The local worker uses the dedicated Cloudlink virtualenv:

```text
~/.cloudlink/venvs/python-auto
```

Declare Python dependencies with repeated `--requirement` flags:

```bash
--requirement pandas==2.2.2 --requirement numpy==2.0.2
```

Rules:

- Prefer pinned package versions when known.
- Include only packages the generated script imports.
- Do not install dependencies on the cloud server just to satisfy the local job.
- Do not use pip option strings such as `--extra-index-url` as requirements.
- If a package needs native system libraries and installation fails, report the failure and required system dependency.

## Large Dataset Rules

Cloudlink transfers dataset references, not large dataset contents.

When a task needs K-line data, tick data, model files, archives, or other large server-side files:

1. Query existing managed datasets first.
2. If the dataset is missing, register the server-local data into Cloudlink.
3. Submit the script with `--dataset <dataset_version_id>:<mount_name>`.
4. In the script, read `CLOUDLINK_DATASET_<MOUNT_NAME>` or `datasets.json`.

Do not embed large CSV, JSON, or binary data in `script_job.payload`.

Register a plain server-local file as a symlink-managed dataset:

```bash
cd /opt/cloudlink
.venv/bin/python - <<'PY'
import json, sys

sys.path.insert(0, "/opt/cloudlink/scripts")
from cloudlink_client import request_json

body = {
    "name": "btcusdt-15m",
    "version": "2024-2026-v1",
    "title": "BTCUSDT 15m klines",
    "description": "Server-local kline csv for Cloudlink jobs.",
    "source_kind": "symlink_file",
    "source_path": "/home/ubuntu/research/btcusdt/data/BTCUSDT_15m.csv",
    "content_type": "text/csv",
    "manifest": {"schema": ["timestamp", "open", "high", "low", "close", "volume"]},
}
print(json.dumps(request_json("POST", "/api/internal/datasets", body), ensure_ascii=False))
PY
```

Register an archive as a Cloudlink-owned dataset only when Cloudlink should take ownership and move the file into its managed directory:

```json
{
  "source_kind": "owned_archive",
  "source_path": "/tmp/btcusdt-15m.zip",
  "archive_format": "zip",
  "extract_required": true
}
```

Plain-file deletion removes only the Cloudlink symlink and records. Owned-archive deletion removes the real managed archive.

## Submission Pattern

Create the script on the cloud server, then submit it through the internal helper:

```bash
cd /opt/cloudlink

.venv/bin/python scripts/submit_local_script_job.py \
  --title "BTCUSDT event efficiency" \
  --description "Measure event efficiency and return summarized local-compute outputs." \
  --script-file /tmp/cloudlink-job.py \
  --cpu-cores 4 \
  --memory-gb 8 \
  --job-disk-gb 5 \
  --expected-runtime-seconds 1800 \
  --timeout 3600 \
  --wait \
  --wait-timeout-seconds 7200
```

With dependencies:

```bash
.venv/bin/python scripts/submit_local_script_job.py \
  --title "BTCUSDT dependency analysis" \
  --description "Run generated Python analysis on local compute and return described artifacts." \
  --script-file /tmp/cloudlink-job.py \
  --cpu-cores 4 \
  --memory-gb 12 \
  --job-disk-gb 10 \
  --expected-runtime-seconds 3600 \
  --requirement pandas==2.2.2 \
  --requirement numpy==2.0.2 \
  --timeout 3600 \
  --wait
```

With stdin, args, or input files:

```bash
.venv/bin/python scripts/submit_local_script_job.py \
  --title "Local compute with request config" \
  --description "Use stdin, args, and input config to produce documented result files." \
  --script-file /tmp/cloudlink-job.py \
  --resource-request-file /tmp/cloudlink-resource-request.json \
  --stdin-file /tmp/request.json \
  --arg=--fast \
  --input-file inputs/config.json=/tmp/config.json \
  --wait
```

With a managed dataset:

```bash
.venv/bin/python scripts/submit_local_script_job.py \
  --title "BTCUSDT managed dataset analysis" \
  --description "Analyze the registered K-line dataset and return documented result artifacts." \
  --script-file /tmp/cloudlink-job.py \
  --dataset <dataset_version_id>:klines \
  --expected-artifact "summary.json|Summary JSON|Aggregated quality metrics and conclusions.|Use to decide follow-up analysis scope.|application/json|true" \
  --cpu-cores 4 \
  --memory-gb 8 \
  --job-disk-gb 5 \
  --dataset-disk-gb 20 \
  --expected-runtime-seconds 1800 \
  --timeout 3600 \
  --wait
```

Script access pattern:

```python
import os
from pathlib import Path

klines_dir_or_file = Path(os.environ["CLOUDLINK_DATASET_KLINES"])
print(klines_dir_or_file)
```

Inside the script, write artifact metadata when possible:

```python
import json
from pathlib import Path

Path("outputs").mkdir(exist_ok=True)
Path("outputs/cloudlink_artifacts.json").write_text(json.dumps({
    "artifacts": [
        {
            "path": "summary.json",
            "title": "Summary JSON",
            "description": "Aggregated metrics and key conclusions.",
            "meaning": "Use this to decide whether deeper analysis is worthwhile.",
            "content_type": "application/json",
            "required": True,
        }
    ]
}, ensure_ascii=False), encoding="utf-8")
```

After completion, read the final JSON printed by the helper. Continue reasoning from `status`, `result.stdout`, `result.stderr`, and `result.output_files`. If an output file has `stored_on_server=true`, use its `artifact_id` with `/api/internal/tasks/<task_id>/artifacts/<artifact_id>/download` before making claims that require the file contents.

## Failure Handling

If `status` is `failed` or `timeout`:

- Inspect `error`, `logs`, `result.stderr` if present, and output files.
- Inspect `error_code`; `queue_timeout` means the job never reached a worker, while `execution_timeout` means the Python script exceeded its declared `--timeout`.
- Fix the generated script or dependency list before retrying.
- Do not retry the same failing payload blindly.
- If task creation fails with `resource_request_unsatisfiable`, automatically revise the resource plan and resubmit a smaller or more parallel-friendly job. Do not ask the user unless automatic reductions cannot produce a feasible plan.
- If task creation fails with `max_pending_exceeded`, poll your own tasks with `GET /api/internal/tasks`, cancel obsolete pending tasks, or wait for queue pressure to fall. Do not submit additional work until facts change.
- If the worker is offline, tell the user to start the local worker:

```bash
cd /path/to/cloudlink
scripts/start_local_worker.sh
```

If a job is stuck in `pending`, check worker online state and supported types.

If a job is stuck in `running`, wait for `TASK_LOCK_SECONDS` or inspect server state before creating duplicates.

## Security Boundaries

Allowed:

- Submitting `script_job` through `/api/internal/tasks` from the cloud server.
- Reading `/opt/cloudlink/.codex-token` through `scripts/cloudlink_client.py` for limited Codex CLI submission.
- Returning sanitized computation results and artifacts.

Forbidden:

- Exposing `INTERNAL_API_SECRET`, `WORKER_SECRET`, admin password, cookies, SSH keys, API tokens, or private env values.
- Using `sudo` from inside the Codex CLI sandbox to read `/etc/cloudlink.env`.
- Calling `/api/internal/*` through public Cloudflare.
- Using unknown worker IDs or bypassing worker registration.
- Asking the local worker to run shell commands or Codex CLI.
- Writing outside the Cloudlink job directory unless the user explicitly requested a safe, known local path.

## Operational Notes

Example worker:

```text
local-worker-1
```

Example public worker API base:

```text
https://tasks.example.com
```

Current Cloudlink task type:

```text
script_job
```

The worker-side runtime root is local to the Mac and normally appears as:

```text
~/.cloudlink/jobs/<task-id>
~/.cloudlink/venvs/python-auto
```

When reporting back to the user, include task id, status, worker id, stdout summary, and important output files. Do not include secrets or full verbose logs unless needed for debugging.
