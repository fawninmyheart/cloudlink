# Cloudlink Task Lifecycle Fix For Codex

Cloudlink server and workers now require version `2026.07.27.1` for the task
lifecycle protocol described below.

## What Changed

1. `timeout_seconds` is honored as the real script runtime after process launch.
   On timeout, the worker terminates the complete micromamba, Python, and CUDA
   process group before reporting terminal `timeout` with
   `error_code=execution_timeout`.
2. Workers renew each running task lease every five seconds. The lease is no
   longer calculated as `timeout + 300`.
3. An expired lease becomes terminal `timeout` with `error_code=worker_lost`.
   Cloudlink never automatically reclaims or reruns that task.
4. The task owner may cancel a running task with
   `POST /api/internal/tasks/<task_id>/cancel`. The request first records
   `cancel_requested_at`; poll until the worker confirms terminal `cancelled`.
5. CPU, memory, disk, and GPU reservations remain attached while termination is
   pending and are released only at a terminal state.
6. Every attempt uses `<job_root>/<task_id>/<lease_id>`, preventing output from
   an older attempt from contaminating a later explicit submission.
7. The default maximum accepted timeout is one year (`31536000` seconds).
   Multi-hour and multi-day model jobs are supported. A timeout above the
   configured maximum fails explicitly and is never silently shortened.

## Required Codex Behavior

- Treat `execution_timeout`, `cancelled`, and `worker_lost` as terminal
  infrastructure outcomes. Do not wait for an automatic retry; there is none.
- Resubmit only as a new task after checking the worker and deciding that a new
  execution is scientifically valid.
- For long jobs, choose `--timeout` from the expected model runtime plus a
  deliberate margin. Do not shorten a legitimate large-model run merely to fit
  the old 7200-second incident limit.
- For multi-day jobs, submit without `--wait` and poll with
  `GET /api/internal/tasks/<task_id>`, or set `--wait-timeout-seconds` beyond the
  requested task timeout. When `--wait` is used without an explicit wait
  timeout, the CLI now waits for the requested task timeout plus five minutes.
- After requesting cancellation of a running task, keep polling until
  `status=cancelled`. Do not assume the HTTP response alone means the GPU
  process has already exited.
- Download required artifacts within the normal 24-hour retention window.

## Batch 019 Incident

Task `685c6b7a-b645-4c46-b5c5-b548346a908d` used the old protocol and reached
`retry_count=1`. Its eventual `success` came from an automatically reclaimed
execution that reused the original task directory. Do not treat that result as
a trusted formal model result without an independent scientific audit. Submit a
new task under the fixed protocol when the research is resumed.
