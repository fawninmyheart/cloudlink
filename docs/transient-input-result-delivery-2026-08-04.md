# Transient Input And Result Delivery

## Goal

Cloudlink schedules and transfers work without becoming a long-term data
archive. Input files remain in the submitter's server directory, workers keep a
short-lived reusable cache, and task outputs end in the directory selected at
submission.

## New Task Contract

Submit each input as an existing server file:

```json
{
  "type": "script_job",
  "result_path": "/data/project/results/run-001",
  "payload": {
    "script": "...",
    "input_paths": [
      {
        "source_path": "/data/project/input.tar",
        "path": "inputs/input.tar"
      }
    ]
  }
}
```

`result_path` is the exact per-task destination and must not already exist.
Using a new directory lets Cloudlink publish the complete result atomically.

At submission Cloudlink checks the source and destination allowlists, records
file size and modification time, and computes SHA-256. It does not copy the
source. A claiming worker receives a lease-bound download URL without seeing
the server source path.

The worker downloads to:

```text
<dataset-root>/transfers/archives/<sha256>/...
```

The job directory contains a symbolic link at the requested relative path. A
corrected task that references identical bytes reuses this cache.

## Result Commit

When `result_path` is present, every file under `outputs/` uses resumable upload.
Cloudlink verifies each upload, builds a temporary directory next to the final
destination, writes `_cloudlink_task.json`, and renames the complete directory
into place. Only after publication succeeds does the task become terminal.

The task record is written for success, failure, timeout, and cancellation.
Published output files are not part of artifact retention cleanup.

## Cache Release

After Codex has inspected the terminal task and its result directory:

```bash
/opt/cloudlink/scripts/release_task_input_cache.py <task-id>
```

Cloudlink records one release request per content cache key. The assigned worker
polls these requests during maintenance, removes matching archive/extracted
directories, and acknowledges completion. A request is rejected while the task
is still active.

## Deployment Settings

Set explicit roots in `/etc/cloudlink.env`:

```bash
CLOUDLINK_TRANSFER_SOURCE_ROOTS=/home/ubuntu/research:/data
CLOUDLINK_RESULT_DESTINATION_ROOTS=/home/ubuntu/research:/data
CLOUDLINK_LEGACY_DATASET_REGISTRATION_ENABLED=0
```

Use the narrowest practical roots. Neither setting should be `/`.

Deploy server and workers together because workers older than `2026.08.04.1`
cannot consume `input_paths` or process release commands. Existing managed
dataset rows and worker caches remain available to already-created tasks. This
release does not delete them; clean them only after old tasks and references
have been audited.

## Compatibility

- Existing tasks containing `datasets` continue through the legacy resolver.
- New managed dataset registration through Codex tokens returns HTTP 410 by
  default. The internal administrator endpoint remains available for migration.
- Setting `CLOUDLINK_LEGACY_DATASET_REGISTRATION_ENABLED=1` temporarily restores
  registration during migration.
- Script jobs without `result_path` retain legacy inline/24-hour artifact
  behavior, but the submission helper now supports the published-result path.
