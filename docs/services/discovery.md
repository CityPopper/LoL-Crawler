# Discovery Service

The Discovery service is the automatic player fan-out engine. It monitors the `discover:players`
sorted set (populated by the Parser as it processes match participants) and promotes discovered
players into the pipeline when the pipeline is idle and no seeded players are in-flight.

---

## What It Does

1. **Polls** `discover:players` on a configurable interval.
2. **Checks idleness** across all four pipeline streams and any in-flight priority players.
3. **Resolves names** for each candidate player using cached Redis data or the Riot API.
4. **Promotes** players to `stream:puuid` using an at-least-once atomic write pattern.
5. **Cleans up** each promoted entry from `discover:players` atomically.

---

## Data Flow

```
Parser writes participant PUUIDs
         |
         v
 discover:players  (sorted set, score = game_start_timestamp)
         |
  [Discovery polls every DISCOVERY_POLL_INTERVAL_MS ms]
         |
   _is_idle() check
    - All consumer group pending == 0 and lag == 0? (XINFO GROUPS)
    - No priority players in-flight? (SCAN player:priority:*)
         |
         v (idle == True)
   _promote_batch()
    - ZREVRANGE discover:players [0, DISCOVERY_BATCH_SIZE - 1]
    - For each member: resolve (game_name, tag_line)
    - XADD to stream:puuid
    - MULTI/EXEC: HSET player:{puuid}, ZADD players:all, ZREM discover:players
         |
         v
  stream:puuid  (consumed by Crawler)
```

---

## Idle Check Logic

Discovery only promotes when the entire pipeline is idle. The `_is_idle()` function
checks all four pipeline streams:
`stream:puuid`, `stream:match_id`, `stream:parse`, and `stream:analyze`.

**Consumer group pending/lag check via XINFO GROUPS**

For each stream, all registered consumer groups must report `pending == 0` and
`lag == 0`. A non-zero pending count means messages are delivered but not yet
ACKed; a non-zero lag means messages exist that no consumer has fetched yet.

Streams that do not exist yet (`ResponseError`) or have no consumer groups registered
are treated as idle for that stream.

**Priority gate**

Even when streams are fully drained, Discovery will not promote if any
`player:priority:{puuid}` keys exist (detected via `SCAN player:priority:*`).
These keys are written by `set_priority()` before a seeded player is published to
`stream:puuid` and deleted by `clear_priority()` after the Crawler finishes. Discovery
pauses until all manually-seeded players complete, preventing discovered players from
competing for API quota with user-triggered seeds.

Each `player:priority:{puuid}` key carries a TTL (default 86400 s). If the pipeline
stalls and a seeded player never completes, the key expires naturally and Discovery
resumes.

---

## Priority Integration

| Redis key | Set by | Cleared by | TTL |
|-----------|--------|-----------|-----|
| `player:priority:{puuid}` | Seed via `set_priority()` (before publishing to `stream:puuid`) | Crawler via `clear_priority()` (after all match IDs published) | `PRIORITY_KEY_TTL` (default 86400 s) |

Both operations are performed by atomic Lua scripts in `lol_pipeline.priority` to
prevent races between concurrent Seed and Crawler instances.

---

## Promotion Atomicity

The promotion sequence is designed for at-least-once delivery:

1. **XADD** to `stream:puuid` first. If the process crashes here, the player remains
   in `discover:players` and will be re-promoted on the next cycle. Downstream consumers
   handle duplicate PUUIDs idempotently.
2. **MULTI/EXEC pipeline**: `HSET player:{puuid}` (game_name, tag_line, region,
   seeded_at), `ZADD players:all`, `ZREM discover:players`. All three writes are atomic.
   A crash before this pipeline leaves the player in `discover:players` for re-promotion.

A player that was seeded manually after being added to `discover:players` is skipped
(`HEXISTS player:{puuid} seeded_at`) and removed from `discover:players` without
re-publishing to avoid duplicate processing.

---

## Name Resolution

`_resolve_names()` returns `(game_name, tag_line)` via a two-step lookup:

1. **Redis cache**: `HMGET player:{puuid} game_name tag_line`. If both fields are
   present, the API call is skipped.
2. **Riot API**: `GET /riot/account/v1/accounts/by-puuid/{puuid}`. Extracts `gameName`
   and `tagLine` from the response.

On a `NotFoundError` (404), the player is permanently removed from `discover:players`
(deleted or banned account). On a transient `RiotAPIError`, the player is left in the
queue for the next cycle. On an `AuthError` (403), `system:halted` is set and the
service exits.

---

## Configuration

All variables are read via `lol_pipeline.config.Config` (pydantic-settings, sourced
from environment or `.env`).

| Env variable | Config field | Default | Description |
|-------------|-------------|---------|-------------|
| `DISCOVERY_POLL_INTERVAL_MS` | `discovery_poll_interval_ms` | `5000` | Milliseconds between idle checks |
| `DISCOVERY_BATCH_SIZE` | `discovery_batch_size` | `10` | Max players promoted per idle cycle |
| `RIOT_API_KEY` | `riot_api_key` | (required) | Riot API key for name resolution |
| `REDIS_URL` | `redis_url` | (required) | Redis connection string |
| `MAX_ATTEMPTS` | `max_attempts` | `5` | Max delivery attempts for stream messages |
| `PRIORITY_KEY_TTL` | — (read directly via `os.getenv` in `priority.py`) | `86400` | Seconds before a priority key expires |

---

## Failure Modes and Recovery

| Failure | Behavior | Recovery |
|---------|---------|---------|
| Redis connection error | Logs exception, sleeps 1 s, retries | Automatic — no data loss |
| Riot API transient error (5xx, 429) | Player left in `discover:players`, skipped this cycle | Automatic on next poll |
| Riot API auth error (403) | Sets `system:halted`, exits | Requires valid API key and manual `just admin system-resume` |
| Riot API not found (404) | Player permanently removed from `discover:players` | None — account is deleted/banned |
| Missing `gameName`/`tagLine` in API response | Player permanently removed from `discover:players` | None — account is unusable |
| `system:halted` set by any service | Loop exits immediately | Requires `just admin system-resume` |
| `player:priority:{puuid}` keys exist (SCAN) | Promotion skipped; pipeline serves seeded players first | Automatic — resumes when all priority keys cleared or TTL expires |
| Process crash mid-promotion | Player remains in `discover:players` (at-least-once) | Re-promoted on next cycle |

---

## Logging

The service uses structured JSON logging via `lol_pipeline.log.get_logger("discovery")`.

Key log events:

| Level | Message | Extra fields |
|-------|---------|-------------|
| `INFO` | `discovery started` | `poll_interval_ms`, `batch_size` |
| `INFO` | `promoted discovered players` | `count` |
| `DEBUG` | `heartbeat` | `idle`, `queue` (size of `discover:players`) |
| `WARNING` | `account not found by puuid` | `puuid` |
| `WARNING` | `account missing gameName/tagLine (deleted/banned?)` | `puuid` |
| `ERROR` | `auth error (403) — halting system` | `puuid` |
| `ERROR` | `transient api error — will retry` | `puuid` |
| `CRITICAL` | `system halted — exiting` | — |

A heartbeat log is emitted roughly every 60 s (based on `DISCOVERY_POLL_INTERVAL_MS`)
when no promotion activity occurs, to confirm the service is alive.

---

## Redis Keys Used

| Key | Type | Written by | Read by | Description |
|-----|------|-----------|---------|-------------|
| `discover:players` | Sorted set | Parser | Discovery | Candidate PUUIDs, score = game_start_timestamp |
| `player:{puuid}` | Hash | Discovery (promote), Seed, UI | Discovery (cache lookup) | Player metadata including `seeded_at`, `game_name`, `tag_line`, `region` |
| `players:all` | Sorted set | Discovery, Seed, UI | UI `/players` | All known players, score = seeded_at epoch |
| `system:halted` | String | Any service on 403 | All services | Set to `"1"` to stop the pipeline |
| `player:priority:{puuid}` | String | Seed via `set_priority()` | Crawler via `clear_priority()` | Presence (TTL 86400 s) indicates player is high-priority; Discovery scans for these |
| `stream:puuid` | Stream | Discovery, Seed | Crawler | Published player PUUIDs |

---

## Known Limitations (Open Issues)

- Discovery does not observe `stream:dlq` or `delayed:messages`. Players can be promoted
  while DLQ retries are still in-flight if those retries target a downstream stream that
  appears drained.
- Discovery assumes a single instance. Running multiple Discovery replicas without a
  distributed lock would cause double-promotion races on the same `discover:players` entries.
