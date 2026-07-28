# Cloudlink Result Delivery Fix 2026-07-29

Cloudlink `2026.07.29.1` fixes three infrastructure failures that could
invalidate long-running research jobs after their computation had already
finished.

## Dataset Cache Concurrency

The worker previously used one fixed `.tmp` path for every download of a
dataset version. Two tasks downloading the same uncached version could move or
delete each other's temporary file.

The worker now serializes cache creation by dataset version with an in-process
lock and a POSIX file lock. Every download has its own temporary filename. The
single writer validates the checksum and atomically publishes the final file;
waiters then reuse that cache. Extraction is covered by the same lock.

Dataset preparation failures now report `dataset_cache_failed`.

## Artifact Upload Recovery

Artifact chunks now default to 256 KiB and use a dedicated 300-second request
timeout. A chunk request has one transport attempt. If its response is lost,
the worker immediately queries the server's uploaded offset before deciding
whether to retry.

The server continues to accept identical duplicate chunks, so a lost response
does not corrupt or restart an upload. Exhausted artifact uploads report
`artifact_upload_failed`.

Reverse proxies should stream worker request bodies and allow 300 seconds:

```nginx
location / {
    client_body_timeout 300s;
    proxy_request_buffering off;
    proxy_read_timeout 300s;
    proxy_send_timeout 300s;
    proxy_pass http://127.0.0.1:8010;
}
```

## Idempotent Success Reports

Workers now include a canonical SHA-256 of the result in `/success`. The server
stores the completion lease and result hash. Repeating the same success report
returns success instead of HTTP 409.

Before reporting success, the worker writes the payload to
`$CLOUDLINK_HOME/completion-outbox/<task_id>.json`. Network timeouts leave this
file in place and retry the same completion while the renewable task lease is
active. The worker no longer reports an already completed calculation as
`failed` merely because the success response was lost. Preserved completions
are replayed when the worker starts again.

The normal API timeout remains short for polling and heartbeat requests.
Artifact upload and result reporting use separate 300-second timeouts.

## Worker Upgrade

Both CPU and GPU workers must be updated to `2026.07.29.1` through the
dashboard deployment command. Existing data caches and job files are retained.
