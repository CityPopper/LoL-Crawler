# Delay Scheduler Service

The Delay Scheduler moves ready messages from `delayed:messages` (a sorted set
scored by delivery timestamp in milliseconds) back to their target streams so
they can be reprocessed by the appropriate pipeline consumer.

---

## What It Does

1. **Polls** `delayed:messages` every `DELAY_SCHEDULER_INTERVAL_MS` milliseconds.
2. **Fetches** up to `_BATCH_SIZE` (100) members with score ≤ current time.
3. **Dispatches** each ready member atomically to its target stream via Lua script
   (XADD + ZREM in one round-trip).
4. **Circuit-breaks** members that fail repeatedly: after `_MAX_MEMBER_FAILURES`
   (10) consecutive failures, the member is pushed to a future score so it
   doesn't block subsequent messages. The circuit resets after `_CIRCUIT_OPEN_TTL_S`
   (300 s).

---

## Data Flow

```
Recovery or nack_to_dlq writes delayed message
              |
              v
    delayed:messages  (sorted set, score = delivery_timestamp_ms)
              |
  [Delay Scheduler polls every DELAY_SCHEDULER_INTERVAL_MS ms]
              |
      score <= now_ms?
              |
              v (yes)
      _DISPATCH_LUA
       - XADD to target stream (with per-stream MAXLEN)
       - ZREM from delayed:messages
              |
              v
   Target stream (stream:puuid, stream:match_id, stream:parse, or stream:analyze)
```

---

## Atomic Dispatch (Lua Script)

The `_DISPATCH_LUA` script performs `XADD` and `ZREM` atomically:

```lua
XADD  target_stream  MAXLEN ~ maxlen  *  field1 val1 ...
ZREM  delayed:messages  member
```

**Delivery guarantee:** at-least-once. If the process crashes after `XADD` but
before `ZREM`, the member remains in `delayed:messages` and will be dispatched
again on the next tick. Downstream consumers handle duplicate delivery
idempotently.

---

## Circuit Breaker

Per-member failure state is tracked in module-level dicts (`_member_failures`,
`_circuit_open`). When a member fails `_MAX_MEMBER_FAILURES` times:

1. The member's score in `delayed:messages` is advanced to
   `now + _CIRCUIT_OPEN_TTL_S * 1000` ms using `ZADD ... XX` (update-only).
2. The member is skipped for the current batch window.
3. After `_CIRCUIT_OPEN_TTL_S` seconds, the circuit resets and one retry is
   allowed.

**Note:** Failure counters are in-memory and reset on service restart. After a
restart, a poison message will fail up to 10 more times before the circuit
re-opens — approximately 5 seconds at 500 ms/tick.

---

## Per-Stream MAXLEN Policy

| Stream | MAXLEN |
|--------|--------|
| `stream:match_id` | `MATCH_ID_STREAM_MAXLEN` (unbounded by default) |
| `stream:analyze` | `ANALYZE_STREAM_MAXLEN` |
| All others | `_DEFAULT_MAXLEN` (10,000) |

MAXLEN is applied as `XADD MAXLEN ~ <n>` (approximate trimming) to avoid exact
trimming overhead.

---

## Configuration

| Env variable | Default | Description |
|-------------|---------|-------------|
| `DELAY_SCHEDULER_INTERVAL_MS` | `500` | Milliseconds between ticks |
| `REDIS_URL` | (required) | Redis connection string |

---

## Failure Modes and Recovery

| Failure | Behavior | Recovery |
|---------|---------|---------|
| Redis connection error | Logs exception, sleeps 1 s, retries | Automatic |
| Corrupt delayed member (bad JSON) | Logs error, ZREMs member, continues | None — invalid data discarded |
| Dispatch Redis error | Increments failure counter; circuit opens at 10 failures | Automatic after `_CIRCUIT_OPEN_TTL_S` (300 s) |
| Circuit-open member | Pushed to `now + 300s` score in `delayed:messages` | Automatic — retried after TTL |
| SIGTERM | Shuts down gracefully after current tick completes | Automatic |

---

## Redis Keys Used

| Key | Type | Written by | Read by | Description |
|-----|------|-----------|---------|-------------|
| `delayed:messages` | Sorted set | Recovery (`nack_to_dlq`), Admin replay | Delay Scheduler | Delayed messages, score = delivery timestamp ms |
| Target streams | Stream | Delay Scheduler (via Lua) | Respective consumers | Published ready messages |
