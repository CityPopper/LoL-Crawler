# Failure & Resilience

## system:halted Flag

**Key:** `system:halted` (Redis String)

Set to `"1"` by:
- Fetcher on HTTP 403
- Recovery on processing an `http_403` DLQ entry

**Effect:**
- Every service checks this flag on startup and before processing each message
- If set: do not ACK the current message; log `CRITICAL: system halted`; exit the worker loop
- Unprocessed messages remain in the stream's pending list and re-appear after `STREAM_ACK_TIMEOUT`
- The **Delay Scheduler** does NOT check `system:halted` — it only moves messages; services won't process them once halted
- Recovery continues processing `stream:dlq` while halted to handle the 403 entries already in the DLQ

**Clearing the flag:**
```
admin system-resume
```
This DELs `system:halted`. When a service detects `system:halted` it exits its worker loop,
causing the container to exit. Docker's `restart: unless-stopped` policy will restart the
container automatically. On restart, if the flag is still set the service exits again
immediately; if cleared, it proceeds normally. After `admin system-resume`, force an
immediate restart of all containers with `docker compose restart` rather than waiting for
Docker's restart backoff.

---

## Failure Modes

| Failure                            | Behavior                                                             |
|------------------------------------|----------------------------------------------------------------------|
| API 429                            | `nack_to_dlq`; Recovery requeueues via `delayed:messages` with `Retry-After` delay |
| API 5xx                            | `nack_to_dlq`; Recovery requeues via `delayed:messages` with exponential backoff |
| API 404 (match)                    | Fetcher sets `match.status = not_found`; ACK and discard            |
| API 403 (bad key)                  | Service sets `system:halted = 1`; exits; all services stop          |
| `RawStore.set` failure             | Fetcher does not publish to `stream:parse`; `nack_to_dlq`           |
| `RawStore.get` returns None        | Parser sends to DLQ with `parse_error`; raw blob preserved          |
| JSON parse failure                 | Parser sends to DLQ with `parse_error`; raw blob preserved          |
| Service crash mid-job              | Message re-appears after `STREAM_ACK_TIMEOUT` via `XAUTOCLAIM`      |
| Duplicate match_id                 | All writes are idempotent; safe to process twice                     |
| DLQ `dlq_attempts` exhausted       | Recovery archives to `stream:dlq:archive`; ACK from active DLQ      |
| Analyzer lock contention           | Non-lock-holder ACKs and discards; lock-holder processes all pending |
| Delay Scheduler crash              | Messages stay safely in `delayed:messages`; re-delivered on restart  |
| XADD succeeds, ZREM fails (Scheduler) | Duplicate delivered to target stream; handled idempotently        |

---

## DLQ Lifecycle

```
Message delivered to service handler
        │
        │  Phase 1: In-process retry (service.py _handle_with_retry)
        │  Retries the handler up to 3 times on exception before giving up.
        │  No DLQ involvement — retries happen in the same consumer loop iteration.
        │
        ├── Handler succeeds (attempt 1-3)
        │       ACK message
        │       Done
        │
        └── Handler fails all 3 in-process retries
                nack_to_dlq() called
                │
                │  Phase 2: Stream-level redelivery
                │
                ├── attempts < max_attempts
                │       ZADD delayed:messages (backoff delay)
                │       ACK original
                │       Delay Scheduler moves to original_stream when ready
                │       Service retries (back to Phase 1)...
                │
                └── attempts >= max_attempts
                        XADD stream:dlq (with full DLQ envelope)
                        ACK original
                                │
                                │  Phase 3: DLQ recovery
                                │
                                Recovery processes stream:dlq
                                │
                                ├── Recoverable (http_429, http_5xx)
                                │       dlq_attempts++
                                │       if dlq_attempts < DLQ_MAX_ATTEMPTS:
                                │           ZADD delayed:messages
                                │       else:
                                │           XADD stream:dlq:archive
                                │
                                └── Permanent (http_404, parse_error, unknown)
                                        Log and discard (or archive)
```

---

## Pending Entry Redelivery

The `pending_redelivery_loop` runs as a background task within each consumer process:

1. Every `STREAM_ACK_TIMEOUT` seconds, call `XAUTOCLAIM` on the consumer group's pending
   entry list for entries idle longer than `STREAM_ACK_TIMEOUT`
2. For each claimed entry: re-deliver to the current consumer as if newly received
3. This handles the case where a worker crashes after dequeuing but before ACKing

This loop is part of `streams.py` and is started automatically by every service worker.

---

## Admin: Incident Recovery

| Scenario                         | Steps                                                          |
|----------------------------------|----------------------------------------------------------------|
| API key expired (403)            | 1. Rotate key; update `RIOT_API_KEY` env; 2. `admin system-resume`; 3. Restart all workers; 4. `admin dlq replay --all` |
| Stuck DLQ entries               | `admin dlq list` to inspect; `admin dlq replay --all` or `admin dlq clear --all` |
| Parser schema mismatch (new API patch) | Fix parser; `admin dlq replay --all` to reprocess raw blobs |
| Data corruption                  | Analyzer stats are fully recomputable: DEL `player:stats:*` keys, replay `stream:analyze` for affected PUUIDs |
