# Persistent Artifact Delivery 2026-07-30

Cloudlink `2026.07.30.1` separates successful computation from result
delivery. A temporary network outage no longer turns a completed script job
into a failed computation.

## Delivery Outbox

After a script exits successfully, the worker writes a delivery record to:

```text
$CLOUDLINK_HOME/delivery-outbox/<task_id>.json
```

The record is written before the first artifact request and contains the job
output directory, result metadata, expected artifacts, and runtime artifact
manifest. It does not contain the worker secret.

If an artifact request fails, the worker keeps renewing the task lease and
retries delivery with capped backoff. There is no fixed retry deadline. The
server-provided uploaded byte offset remains authoritative, so reconnects
continue rather than restart a file.

On worker restart, preserved delivery records are replayed. If the prior lease
expired, the original worker may recover a delivery-only lease for a task
whose failure was `artifact_upload_failed` or `worker_lost`. This recovery
cannot revive a successful, cancelled, execution-timeout, or unrelated failed
task.

## Recover An Existing Job

An intact job directory from an older worker can be delivered without
rerunning computation:

```bash
scripts/start_local_worker.sh recover-delivery scripts/local_worker.env \
  --task-id TASK_ID \
  --lease-id ORIGINAL_LEASE_ID \
  --job-dir /path/to/jobs/TASK_ID/ORIGINAL_LEASE_ID
```

The command reconstructs the result from the existing `outputs` and
`datasets.json`, renews a delivery lease, uploads or resumes artifacts, and
reports task success.
