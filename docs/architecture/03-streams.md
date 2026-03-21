# Streams & Messaging

## Stream Registry

All inter-service communication uses Redis Streams with consumer groups.

| Stream               | Producer                          | Consumer(s)          | Purpose                              |
|----------------------|-----------------------------------|----------------------|--------------------------------------|
| `stream:puuid`       | Seed, Discovery, Web UI (auto-seed) | Crawler            | PUUIDs to crawl for match history    |
| `stream:match_id`    | Crawler             | Fetcher              | Match IDs to fetch from Riot API     |
| `stream:parse`       | Fetcher             | Parser               | Match IDs with raw blob ready        |
| `stream:analyze`     | Parser              | Analyzer             | PUUIDs whose stats need updating     |
| `stream:dlq`         | Any service         | Recovery             | Failed messages with failure context |
| `stream:dlq:archive` | Recovery            | (manual only)        | Exhausted messages for inspection    |

Additionally, the **Delay Scheduler** consumes the `delayed:messages` Sorted Set (not a
stream) and produces into the appropriate stream when a message's delay expires.

---

## Message Envelope

Every message on every stream is a flat Redis Stream entry. Fields:

| Field           | Type     | Description                                      |
|-----------------|----------|--------------------------------------------------|
| `id`            | string   | UUID; unique message identity                    |
| `source_stream` | string   | Stream this message originated from              |
| `type`          | string   | `puuid \| match_id \| parse \| analyze \| dlq`   |
| `payload`       | string   | JSON-encoded payload (see per-stream schemas below) |
| `attempts`      | integer  | Delivery attempt count (starts at 0)             |
| `max_attempts`  | integer  | Max before DLQ (from `MAX_ATTEMPTS` env)         |
| `enqueued_at`   | string   | ISO 8601 timestamp when first published          |
| `priority`      | string   | Message priority level (`"normal"` or `"high"`)  |

Redis Streams store entries as flat string maps; `payload` is JSON-serialized within the entry.

### Per-Stream Payload Schemas

**`stream:puuid`**
```json
{ "puuid": "<string>", "game_name": "<string>", "tag_line": "<string>", "region": "<string>" }
```

**`stream:match_id`**
```json
{ "match_id": "<string>", "puuid": "<string>", "region": "<string>" }
```

**`stream:parse`**
```json
{ "match_id": "<string>", "region": "<string>" }
```

**`stream:analyze`**
```json
{ "puuid": "<string>" }
```

---

## DLQ Envelope

DLQ messages include all standard envelope fields plus:

| Field               | Type       | Description                                          |
|---------------------|------------|------------------------------------------------------|
| `failure_reason`    | string     | Human-readable description of what failed            |
| `failure_code`      | string     | Machine-readable code (see table below)              |
| `failed_at`         | string     | ISO 8601 timestamp of failure                        |
| `failed_by`         | string     | Name of the service that produced this DLQ entry     |
| `dlq_attempts`      | int        | Number of recovery attempts (starts at 0)            |
| `retry_after_ms`    | int\|null  | `http_429` only: ms to delay before retry (includes 1s buffer); `null` for all other codes |
| `original_stream`   | string     | Stream the message originally entered the pipeline on |
| `original_message_id` | string   | Redis Stream entry ID of the original message (for audit/replay) |

### Failure Codes

| Code          | Meaning                                                   |
|---------------|-----------------------------------------------------------|
| `http_429`    | Riot API rate limit exceeded                              |
| `http_5xx`    | Riot API server error                                     |
| `http_404`    | Match or player not found (permanent)                     |
| `http_403`    | API key invalid or expired (critical — halts all services)|
| `parse_error` | Raw blob exists but parsing failed                        |
| `unknown`     | Uncategorized error                                       |

---

## Delivery Guarantees

- **At-least-once delivery.** All service writes are idempotent; duplicate processing is safe.
- Messages are not ACK'd until processing fully succeeds.
- Unacknowledged messages re-appear for redelivery after `STREAM_ACK_TIMEOUT` seconds (default: `60`).
- After `MAX_ATTEMPTS` failed deliveries, the message is routed to `stream:dlq`.
- **PEL drain on startup:** `consume()` reads from `id="0"` (own pending entry list) before
  blocking for new messages, so a worker that restarts and reconnects with the same consumer
  name will re-process any messages it had not ACKed. If a worker crashes and restarts
  with a different consumer name (e.g. new PID), its pending entries are reclaimed by
  `XAUTOCLAIM` — the `consume()` function in `streams.py` uses the `autoclaim_min_idle_ms`
  parameter to automatically claim entries that have been idle longer than
  `STREAM_ACK_TIMEOUT`.

---

## Delayed Message Pattern

Redis Streams have no native delayed delivery. Delayed messages (e.g., retry after backoff,
retry after Retry-After header) use a dedicated Redis Sorted Set instead of being re-added
directly to a stream.

### Mechanism

```
Service calls nack_to_dlq()
   │
   └── XADD stream:dlq  ← DLQ envelope with failure context
       (source_stream = "stream:dlq", type = "dlq",
        original_stream = source stream, original_message_id = Redis entry ID)

Recovery processes stream:dlq
   │
   ├── Recoverable (http_429, http_5xx) and dlq_attempts < DLQ_MAX_ATTEMPTS:
   │     ZADD delayed:messages score={ready_epoch_ms} member={serialized_MessageEnvelope}
   │     (source_stream = original_stream, type = original type)
   │     ACK from stream:dlq
   │
   └── Exhausted or permanent:
         XADD stream:dlq:archive
         ACK from stream:dlq
```

The **Delay Scheduler** polls `delayed:messages` on a fixed interval:

```
loop every DELAY_SCHEDULER_INTERVAL_MS:
    ready = ZRANGEBYSCORE delayed:messages 0 now_ms
    for each ready message:
        XADD → target stream (source_stream field)
        ZREM delayed:messages member
```

### Key: `delayed:messages`

- **Type:** Sorted Set
- **Member:** JSON-serialized envelope string
- **Score:** Unix epoch ms at which the message becomes ready
- **Persistence:** Survives Redis restart (AOF + RDB)
- **Safety:** If XADD succeeds but ZREM fails, the message is re-delivered; the target
  service handles the duplicate idempotently.

### Backoff Schedule

| Attempt | Delay      |
|---------|------------|
| 1       | 5s         |
| 2       | 15s        |
| 3       | 60s        |
| 4       | 5min       |
| 5+      | → DLQ      |

For `http_429`: delay = `(Retry-After header seconds + 1) * 1000` ms (1s buffer included). This value is stored as `retry_after_ms` in `DLQEnvelope` and used as-is by both the direct retry path and Recovery. Default when header absent: 61000ms (60s + 1s buffer).
