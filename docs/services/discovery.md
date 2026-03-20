# Discovery Service

The Discovery service is the automatic player fan-out engine. It monitors the `discover:players`
sorted set (populated by the Parser as it processes match participants) and promotes discovered
players into the pipeline when the pipeline is idle and no seeded players are in-flight.

---

## What It Does

1. **Polls** `discover:players` on a configurable interval.
2. **Checks idleness** across all four pipeline streams and the priority counter.
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
    - XLEN each of 4 streams <= MAX_STREAM_BACKLOG?
    - All consumer group pending + lag == 0?
    - system:priority_count == 0?
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
performs a two-layer check across all four pipeline streams:
`stream:puuid`, `stream:match_id`, `stream:parse`, and `stream:analyze`.

**Layer 1 — absolute backlog check via XLEN**

If any stream contains more than `MAX_STREAM_BACKLOG` messages, the pipeline is
considered backlogged regardless of consumer group state.

**Layer 2 — consumer group pending/lag check via XINFO GROUPS**

For each stream, all registered consumer groups must report `pending == 0` and
`lag == 0`. A non-zero pending count means messages are delivered but not yet
ACKed; a non-zero lag means messages exist that no consumer has fetched yet.

Streams that do not exist yet (`ResponseError`) or have no consumer groups registered
are treated as idle for that stream.

**Priority gate**

Even when streams are fully drained, Discovery will not promote if
`system:priority_count > 0`. This key is incremented atomically by `set_priority()`
when a player is seeded with `priority="high"` and decremented by `clear_priority()`
when the Crawler finishes processing that player. Discovery pauses until all
manually-seeded players complete, preventing discovered players from competing for
API quota with user-triggered seeds.

The priority key `player:priority:{puuid}` carries a TTL (default 86400 s). If the
pipeline stalls and a seeded player never completes, the key expires naturally and
Discovery resumes.

---

## Priority Integration

| Redis key | Set by | Cleared by | TTL |
|-----------|--------|-----------|-----|
| `player:priority:{puuid}` | Seed (before publishing to `stream:puuid`) | Crawler (after all match IDs published) | `PRIORITY_KEY_TTL` (default 86400 s) |
| `system:priority_count` | Incremented by `set_priority()` Lua script | Decremented by `clear_priority()` Lua script | No TTL — derived from individual priority keys |

Both operations are performed by atomic Lua scripts in `lol_pipeline.priority` to
prevent race conditions between concurrent Seed and Crawler instances.

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
| `MAX_STREAM_BACKLOG` | — (read directly via `os.getenv`) | `500` | XLEN threshold above which pipeline is considered backlogged |
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
| Riot API auth error (403) | Sets `system:halted`, exits | Requires valid API key and manual `just admin unhalt` |
| Riot API not found (404) | Player permanently removed from `discover:players` | None — account is deleted/banned |
| Missing `gameName`/`tagLine` in API response | Player permanently removed from `discover:players` | None — account is unusable |
| `system:halted` set by any service | Loop exits immediately | Requires `just admin unhalt` |
| `system:priority_count > 0` | Promotion skipped; pipeline serves seeded players first | Automatic — resumes when priority clears or TTL expires |
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
| `system:priority_count` | String (integer) | `priority.set_priority()` | Discovery | Number of in-flight seeded players |
| `player:priority:{puuid}` | String | Seed via `set_priority()` | Crawler via `clear_priority()` | Presence indicates player is high-priority |
| `stream:puuid` | Stream | Discovery, Seed | Crawler | Published player PUUIDs |

---

## Known Limitations (Open Issues)

- **I2-C2**: Discovery idle check watches all four pipeline streams but does not observe
  `stream:dlq` or `delayed:messages`. Players can be promoted while DLQ retries are
  still in-flight if those retries target a downstream stream that appears drained.
- **I2-H3**: Stream `MAXLEN=10,000` on `stream:puuid` can still silently trim undelivered
  messages under sustained discovery load.
- Discovery assumes a single instance. Running multiple Discovery replicas without a
  distributed lock would cause double-promotion races on the same `discover:players` entries.
