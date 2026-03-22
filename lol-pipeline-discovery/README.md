# lol-pipeline-discovery

Promotes co-discovered players from the `discover:players` sorted set to `stream:puuid` when the pipeline is idle.

## Purpose

As the Crawler processes matches it encounters PUUIDs for co-players. Those PUUIDs are written to `discover:players` (a sorted set keyed by game-start timestamp) by the Analyzer. Discovery acts as the fan-out valve: it only drains `discover:players` when the pipeline has fully caught up, preventing discovery traffic from competing with seeded (high-priority) players.

## Behaviour

1. Polls `discover:players` every `DISCOVERY_POLL_INTERVAL_MS` milliseconds.
2. Checks whether the pipeline is idle (see Idle Detection below).
3. When idle, takes up to `DISCOVERY_BATCH_SIZE` members from `discover:players` (highest score first — most recently active players promoted first).
4. For each member:
   - Skips players already seeded whose `recrawl_after` has not yet passed.
   - Resolves `game_name` / `tag_line` from the `player:{puuid}` hash or from the Riot Account API.
   - Publishes a `puuid` envelope to `stream:puuid` with priority `PRIORITY_AUTO_20`.
   - Atomically marks the player as seeded (`HSET player:{puuid} seeded_at …`) and removes the member from `discover:players` (`ZREM`).
5. Logs a heartbeat (at DEBUG level) roughly every 60 seconds with the current queue size.

## Input

| Source | Type | Key |
|--------|------|-----|
| `discover:players` | Sorted Set | Members are `{puuid}:{region}`; score is the game-start Unix timestamp |

## Output

| Destination | Envelope type | Notes |
|-------------|---------------|-------|
| `stream:puuid` | `puuid` | Fields: `puuid`, `game_name`, `tag_line`, `region`; priority `PRIORITY_AUTO_20` |

## Idle Detection

Discovery promotes players only when **all** of the following conditions hold:

1. No `player:priority:*` keys exist in Redis (no seeded/high-priority players are in-flight).
2. Every consumer group on every pipeline stream has both `pending == 0` and `lag == 0`.

The pipeline streams checked are:

| Stream | Consumer group(s) |
|--------|-------------------|
| `stream:puuid` | `crawlers` |
| `stream:match_id` | `fetchers` |
| `stream:parse` | `parsers` |
| `stream:analyze` | `analyzers` |

A stream that does not yet exist, or one with no consumer groups registered, is treated as idle for the purposes of this check.

## Key Configuration

| Environment variable | `Config` field | Default | Description |
|---------------------|----------------|---------|-------------|
| `DISCOVERY_POLL_INTERVAL_MS` | `discovery_poll_interval_ms` | `5000` | Milliseconds between idle checks and promotion attempts |
| `DISCOVERY_BATCH_SIZE` | `discovery_batch_size` | `10` | Maximum players promoted per poll cycle |

All other config vars (Redis URL, Riot API key, log level, etc.) are inherited from the shared `Config` in `lol-pipeline-common`.

## Error Handling

| Error | Action |
|-------|--------|
| `AuthError` (403) | Sets `system:halted=1`, stops the loop |
| `RiotAPIError` (transient) | Leaves member in `discover:players`; retries next cycle |
| `NotFoundError` (404) | Removes member from `discover:players` permanently |
| `RedisError` / `OSError` | Logs exception; sleeps 1 s; retries |

## Scaling

Run a single instance. Discovery reads from a sorted set (not a stream consumer group), so multiple instances would race on the same members and over-promote. The idle-check and atomic `MULTI/EXEC` promotion pipeline make single-instance operation safe for at-least-once delivery: if the service crashes after `XADD` but before the cleanup pipeline, the player is re-promoted on the next cycle (downstream consumers handle duplicate PUUIDs idempotently).
