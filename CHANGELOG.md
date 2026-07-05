# Changelog

## 2026.07.06.2

### Installer

- Fixed the generated Windows worker installer so its private Python runtime
  probe accepts an empty prefix-argument list. This avoids PowerShell's
  `PrefixArguments` empty-array binding error when probing Cloudlink's private
  `python.exe`.

### Dashboard

- Worker install invites now require an explicit worker ID. The dashboard shows
  a Chinese validation prompt before submitting, and the backend rejects blank
  `worker_id` values instead of silently generating `worker-<token>`.

### Compatibility

- Server version: `2026.07.06.2`.
- Minimum supported worker version remains `2026.07.05.6`.

## 2026.07.06.1

### Installer

- Windows worker installs now bootstrap a Cloudlink-private Python 3.12.10
  runtime under the worker install directory when the runtime is missing. The
  generated script downloads the official Python installer over HTTPS, verifies
  its SHA256 checksum, installs it without modifying system `PATH`, and uses
  that private runtime to create the worker virtualenv.

### Compatibility

- Server version: `2026.07.06.1`.
- Minimum supported worker version remains `2026.07.05.6`.

## 2026.07.05.10

### Installer

- Improved the generated Windows worker installer so it verifies that `py -3`
  or `python` can actually run `--version` before selecting it. Broken Windows
  App Execution Alias entries or missing Python installs now produce a clear
  "Usable Python 3 was not found" message instead of `python failed with exit
  code 9009`.

### Compatibility

- Server version: `2026.07.05.10`.
- Minimum supported worker version remains `2026.07.05.6`.

## 2026.07.05.9

### Installer

- Hardened the generated Windows worker installer so it resolves `py -3` or
  `python`, checks native command exit codes, verifies that
  `.venv\Scripts\python.exe` was created, and reports a clear Python/venv setup
  error instead of continuing until PowerShell fails to invoke a missing venv
  runtime.

### Compatibility

- Server version: `2026.07.05.9`.
- Minimum supported worker version remains `2026.07.05.6`.

## 2026.07.05.8

### Reliability

- Made worker artifact record creation idempotent for repeated
  same-task/same-path/same-content requests, so a worker retry after a lost
  create response can continue uploading instead of failing the task with `409
  Conflict`.
- Kept conflicting same-path artifact metadata as `409 Conflict` so changed
  output content is still rejected instead of silently reusing the wrong result.

### Compatibility

- Server version: `2026.07.05.8`.
- Minimum supported worker version remains `2026.07.05.6`.

## 2026.07.05.7

### Security

- Added the full PolyForm Noncommercial 1.0.0 license text for the public
  source-available, non-commercial release.
- Added `COMMERCIAL-LICENSE.md` and `NOTICE` to clarify commercial licensing,
  required notices, and white-label/rebranding/resale/hosted-service limits.
- Removed internal planning/spec documents from the public repository and ignored future local planning drafts under `docs/superpowers/`.
- Added startup configuration checks that reject missing required deployment secrets and known example placeholder values.
- Replaced project-specific README dataset examples with neutral sample paths.

### Operations

- Added artifact upload retry settings to generated worker install environment files so newly installed workers inherit the stronger upload retry policy.

### Compatibility

- Server version: `2026.07.05.7`.
- Minimum supported worker version remains `2026.07.05.6`.

## 2026.07.05.6

### Reliability

- Changed worker API retry waits from short linear backoff to capped exponential backoff.
- Added dedicated artifact upload retry controls with longer defaults for public HTTPS and Cloudflare/VPN path instability.

### Compatibility

- Server version: `2026.07.05.6`.
- Minimum supported worker version: `2026.07.05.6`.
- Workers should be updated from the console-generated deployment command to pick up the stronger artifact upload retry policy.

## 2026.07.05.5

### Reliability

- Retries HTTP 500 errors during worker artifact chunk uploads so transient server-side upload failures can resume instead of marking a completed computation as failed immediately.
- Skips repeat full-file dataset cache hashing when cached file size and mtime are unchanged.

### Console

- Shows task and dataset disk bars from the worker's raw reported disk free/total values instead of scheduler-reserved disk capacity.

### Compatibility

- Server version: `2026.07.05.5`.
- Minimum supported worker version: `2026.07.05.5`.
- Existing workers should be updated from the console-generated deployment command to pick up artifact upload retry behavior.

## 2026.07.05.4

### Security

- Restricted Codex-token dataset registration to `CLOUDLINK_ALLOWED_DATASET_SOURCE_ROOTS`; the internal admin secret remains the privileged server-side path.
- Blocked public HTTP worker install invite generation by default. Set `CLOUDLINK_ALLOW_INSECURE_WORKER_INSTALL=1` only for explicit private testing.
- Added deterministic worker package generation and SHA256 verification in generated macOS/Linux/Windows install scripts.
- Tightened installed worker credential file permissions for generated worker installs.
- Removed personal host/path defaults from public examples and local worker startup defaults.
- Documented that `script_job` is trusted Python execution, not a sandbox.
- Added a basic GitHub Actions CI workflow for pull requests.

### Compatibility

- Server version: `2026.07.05.4`.
- Minimum supported worker version remains `2026.07.05.2`.

## 2026.07.05.3

### Server

- Added queue pressure controls: `CLOUDLINK_MAX_PENDING_TASKS`, queue timeout, starvation protection, and Codex-owned pending task cancellation.
- Added resource-aware scheduling with worker capacity checks, reserved system resources, resource request validation, and schedulable capacity reporting.
- Added minimum worker version enforcement through `CLOUDLINK_MINIMUM_WORKER_VERSION`, decoupling server releases from worker updates.
- Added per-submitter Codex token scoping so internal task listing returns only tasks owned by the current submitter while still exposing aggregate resource facts.
- Added admin password changes backed by PBKDF2 hashes in SQLite.
- Added worker install invites and generated platform-specific worker install commands.
- Added resumable result artifact uploads with chunk status recovery, sha256 verification, retained server-side artifacts, and download endpoints.
- Added managed dataset and worker cache controls, including dataset-root validation and multiple dataset-root support.

### Worker

- Added hardware and capacity reporting for CPU, memory, disk, GPU, reserved resources, and active task counts.
- Added concurrent task execution controls and server-managed worker settings.
- Added task-first claim behavior so dataset maintenance cannot block task pickup.
- Added background heartbeat and retrying API calls to tolerate transient HTTPS failures.
- Added resumable artifact upload and dataset cache validation by checksum.
- Added automatic dependency installation inside the dedicated Cloudlink Python runtime.

### Console

- Redesigned the Chinese operations console with stable task queues, worker cards, resource bars, task detail modals, local cache management, worker settings, install command generation, and password changes.
- Reduced disruptive refresh behavior by preserving interaction state while updating dashboard sections.

### Docs

- Updated `README.md` as the primary deployment and operations runbook.
- Updated the `cloudlink-local-compute` skill with queue, resource, artifact, dataset, concurrency, and failure-handling rules for cloud-side Codex CLI.
- Added design notes for dataset-root validation and worker dataset-root settings.

### Compatibility

- Server version: `2026.07.05.3`.
- Minimum supported worker version: `2026.07.05.2`.
- Workers below the minimum version remain registered but cannot claim new tasks until updated from the console-generated installer.
