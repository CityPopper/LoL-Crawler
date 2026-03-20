# Service Contracts

## Service Isolation Principle

Each service is intentionally **unaware** of all other services. A service knows only:
- **Its input stream** — name, schema, and what invariants hold when a message arrives
- **Its output stream** — name and schema of messages it publishes

No service may reference, import, or depend on knowledge of another service's logic,
internal state, or position in the pipeline. This is enforced by consumer-driven contract
tests (Pact v3). See [Testing Plan](../testing/01-testing-plan.md#contract-tests) and
`lol-pipeline-common/contracts/README.md`.

---

All services check the `system:halted` flag (Redis String key) **on startup** and **before
processing each message**. If set, the service logs `CRITICAL: system halted` and exits
without processing. See [06-failure-resilience.md](06-failure-resilience.md) for details.

---

## 1. Seed Service

**Consumes:** CLI argument or API call

**Produces:** `stream:puuid`
```json
{ "puuid": "<string>", "game_name": "<string>", "tag_line": "<string>", "region": "<string>" }
```

**Reads:**
- `system:halted` (String) — halt check
- `player:{puuid}` (Hash) → fields `seeded_at`, `last_crawled_at`

**Writes:**
- `player:{puuid}` (Hash) → fields: `game_name`, `tag_line`, `region`, `seeded_at`

**Behavior:**
1. Check `system:halted`; exit if set
2. Accept Riot ID as `GameName#TagLine`; reject if no `#` with a clear error
3. Resolve PUUID via Account-v1 API (regional routing)
4. Read `player:{puuid}` fields `seeded_at` and `last_crawled_at`; compute `last_activity = MAX(seeded_at, last_crawled_at)` (treating missing values as epoch 0); if `last_activity` is within `SEED_COOLDOWN_MINUTES` (default: `30`): log skip reason including which field triggered the cooldown, exit 0
5. HSET `player:{puuid}` with `game_name`, `tag_line`, `region`, `seeded_at = now`
6. Publish envelope to `stream:puuid`

**Error handling:**
- Riot 404 → log "player not found", exit 1
- Riot 403 → set `system:halted`, log CRITICAL, exit 1
- Riot 429 / 5xx → log error, exit 1 (caller retries; Seed is a one-shot process)

---

## 2. Crawler Service

**Consumes:** `stream:puuid`
```json
{ "puuid": "<string>", "region": "<string>" }
```

**Produces:** `stream:match_id` — one message per new match ID
```json
{ "match_id": "<string>", "puuid": "<string>", "region": "<string>" }
```

**Reads:**
- `system:halted` (String) — halt check per message
- `player:matches:{puuid}` (Sorted Set) — full known match ID set (fetched once into memory for dedup)

**Writes:**
- `player:{puuid}` (Hash) → field `last_crawled_at` (set only after full successful crawl)

**Behavior:**
1. Check `system:halted`; if set: do not ACK, exit worker loop
2. Fetch `player:matches:{puuid}` into a local set (ZRANGE 0 -1) for in-memory dedup
3. Paginate `get_match_ids()` (100 per page) from Match-v5 API
4. For each match ID not in the local known set: publish to `stream:match_id`
5. Stop paginating early when a full page consists entirely of known IDs
6. After full crawl: HSET `player:{puuid}.last_crawled_at = now`
7. ACK message

**Error handling:**
- Riot 403 → set `system:halted`, do not ACK, exit
- Riot 429 / 5xx → `nack_to_dlq` with backoff

**Note:** Match IDs are only added to `player:matches:{puuid}` by the Parser after a match
is fully processed. At crawl time, newly discovered IDs will not be in the known set even
if they are already in-flight in `stream:match_id`. Re-delivery of a `stream:puuid` message
will re-publish in-flight IDs; the Fetcher handles these idempotently.

---

## 3. Fetcher Service

**Consumes:** `stream:match_id`
```json
{ "match_id": "<string>", "region": "<string>" }
```

**Produces:**
- Raw blob via `RawStore.set(match_id, json)` (write-once; no-op if already exists)
- `stream:parse`
  ```json
  { "match_id": "<string>", "region": "<string>" }
  ```

**Reads:**
- `system:halted` — halt check per message
- `RawStore.exists(match_id)` — idempotency check

**Writes:**
- `RawStore.set(match_id, raw_json)` — raw blob (no expiry)
- `match:{match_id}` (Hash) → field `status`

**Behavior:**
1. Check `system:halted`; if set: do not ACK, exit
2. If `RawStore.exists(match_id)`: publish to `stream:parse`, ACK, return (idempotent re-delivery)
3. Call `riot_api.get_match(match_id, region)`
4. Write raw JSON to `RawStore`
5. HSET `match:{match_id}.status = fetched`
6. Publish to `stream:parse`
7. ACK

**Error handling:**
- Riot 404 → HSET `match:{match_id}.status = not_found`; ACK and discard (do not publish to parse)
- Riot 403 → HSET `system:halted = 1`; do not ACK; exit
- Riot 429 → `nack_to_dlq` with `failure_code: http_429`; Recovery will requeue via `delayed:messages`
- Riot 5xx → `nack_to_dlq` with `failure_code: http_5xx`; exponential backoff
- `RawStore.set` failure → do not publish to `stream:parse`; `nack_to_dlq` (raw blob must exist before parse is enqueued)

---

## 4. Parser Service

**Consumes:** `stream:parse`
```json
{ "match_id": "<string>", "region": "<string>" }
```

**Produces:**
- `match:{match_id}` (Hash) — match metadata + status
- `match:participants:{match_id}` (Set) — PUUIDs of all participants
- `match:status:parsed` (Set) — secondary index of parsed match IDs
- `participant:{match_id}:{puuid}` (Hash) — per-player stats (one per participant)
- `player:matches:{puuid}` (Sorted Set, score = `game_start` epoch ms) — one entry per participant
- `stream:analyze` — one message per unique PUUID in the match
  ```json
  { "puuid": "<string>" }
  ```

**Reads:**
- `system:halted` — halt check per message
- `RawStore.get(match_id)` — raw JSON blob

**Behavior:**
1. Check `system:halted`; if set: do not ACK, exit
2. Read raw blob via `RawStore.get(match_id)`; if None: `nack_to_dlq` with `parse_error`
3. Parse JSON; validate required fields (`metadata`, `info`, `info.participants`, `info.gameStartTimestamp`); on failure: `nack_to_dlq` with `parse_error`; raw blob preserved
4. Write `match:{match_id}` Hash: `queue_id`, `game_mode`, `game_type`, `game_version`, `game_duration`, `game_start`, `platform_id`, `region`, `status = parsed`
5. SADD `match:status:parsed` with `match_id` (secondary index)
6. For each participant:
   - Write `participant:{match_id}:{puuid}` Hash: `champion_id`, `champion_name`, `team_id`, `team_position`, `role`, `win`, `kills`, `deaths`, `assists`, `gold_earned`, `total_damage_dealt_to_champions`, `total_minions_killed`, `vision_score`, `items` (JSON array of 7 item IDs)
   - SADD `match:participants:{match_id}` with `puuid`
   - ZADD `player:matches:{puuid}` score=`game_start` member=`match_id`
7. Publish one `stream:analyze` message per unique PUUID in the match
8. ACK

**Error handling:**
- `RawStore` returns None → `nack_to_dlq` with `parse_error`
- JSON parse failure → `nack_to_dlq` with `parse_error`
- Missing required field → `nack_to_dlq` with `parse_error`
- All `nack_to_dlq` here are permanent (parse errors are not retried by Recovery automatically)

**Co-player discovery (step 6b):** After writing participants, for each PUUID where `player:{puuid}` does NOT exist (unknown player), the Parser writes to `discover:players` Sorted Set:
- Member: `{puuid}:{region}` — encodes both identity and routing region
- Score: `game_start` epoch ms (with `GT` flag — score only increases, so newest match wins)

This enables the Discovery service to fan out to new players discovered from processed matches.

**Notes:**
- All Redis writes in step 4–6 are idempotent (HSET overwrites; SADD and ZADD ignore duplicates)
- Participant count varies by game mode; do not assume exactly 10

---

## 5. Analyzer Service

**Consumes:** `stream:analyze`
```json
{ "puuid": "<string>" }
```

**Produces:**
- `player:stats:{puuid}` (Hash) — running raw totals + derived metrics
- `player:champions:{puuid}` (Sorted Set) — member=champion_name, score=games played
- `player:roles:{puuid}` (Sorted Set) — member=role, score=games played
- `player:stats:cursor:{puuid}` (String) — `game_start` epoch ms of last processed match

**Reads:**
- `system:halted` — halt check per message
- `player:stats:lock:{puuid}` (String w/ TTL) — distributed lock
- `player:stats:cursor:{puuid}` (String)
- `player:matches:{puuid}` (Sorted Set) — ZRANGEBYSCORE from cursor to +inf
- `participant:{match_id}:{puuid}` (Hash) — one per new match

**Behavior:**
1. Check `system:halted`; if set: do not ACK, exit
2. Attempt `SET player:stats:lock:{puuid} {worker_id} NX PX {ANALYZER_LOCK_TTL_SECONDS * 1000}`
   - If lock held by another worker: ACK and discard (lock-holder will process all pending matches)
3. Read `player:stats:cursor:{puuid}` (default: `0`)
4. ZRANGEBYSCORE `player:matches:{puuid}` `(cursor` `+inf` (exclusive lower bound)
5. For each new match:
   - HGETALL `participant:{match_id}:{puuid}`
   - HINCRBY `player:stats:{puuid}.total_games` by 1
   - HINCRBY `player:stats:{puuid}.total_wins` by `win` (0 or 1)
   - HINCRBY `player:stats:{puuid}.total_kills` by `kills`
   - HINCRBY `player:stats:{puuid}.total_deaths` by `deaths`
   - HINCRBY `player:stats:{puuid}.total_assists` by `assists`
   - ZINCRBY `player:champions:{puuid}` by 1 for `champion_name`
   - ZINCRBY `player:roles:{puuid}` by 1 for `role`
6. Recompute derived fields:
   - `win_rate = total_wins / total_games` (guard: total_games > 0)
   - `avg_kills = total_kills / total_games`
   - `avg_deaths = total_deaths / total_games`
   - `avg_assists = total_assists / total_games`
   - `kda = (total_kills + total_assists) / max(total_deaths, 1)`
   - HSET all derived fields to `player:stats:{puuid}`
7. If any new matches were processed: SET `player:stats:cursor:{puuid}` to the highest `game_start` score processed. If zero new matches, cursor is left unchanged.
8. Release lock via atomic Lua ownership check (safe release):
   ```lua
   -- KEYS[1] = lock key, ARGV[1] = worker_id
   if redis.call("get", KEYS[1]) == ARGV[1] then
       return redis.call("del", KEYS[1])
   else
       return 0
   end
   ```
   If the lock was not owned by this worker (TTL expired, stolen), log a warning but do not error.
9. ACK

**Notes:**
- Multiple `stream:analyze` messages for the same PUUID arrive when a match is parsed (one per participant × all participants). The cursor naturally deduplicates: the first worker to acquire the lock processes all new matches; subsequent workers find the cursor already up-to-date.
- Lock TTL default is 300s. This covers even very large histories (3000 matches × ~1ms/Redis call = ~3s).
- Division-by-zero guards: `total_games > 0` for averages; `max(deaths, 1)` for KDA.

---

## 6. Recovery Service

**Consumes:** `stream:dlq`

**Produces:**
- Requeues to `original_stream` via `delayed:messages` (Sorted Set)
- Or publishes to `stream:dlq:archive`

**Reads:**
- `system:halted` — halt check per message (Recovery still runs when halted to process 403 entries)

**Behavior:**
1. Read messages from `stream:dlq`
2. Classify by `failure_code`:

   | `failure_code`  | Action                                                                    |
   |-----------------|---------------------------------------------------------------------------|
   | `http_429`      | Requeue to `original_stream` via `delayed:messages` with delay = `retry_after_ms` from envelope (or 61000ms default if null; `retry_after_ms` already includes 1s buffer) |
   | `http_5xx`      | Requeue to `original_stream` via `delayed:messages` with exponential backoff |
   | `http_404`      | Log and discard — permanent; no raw data expected                         |
   | `parse_error`   | Log for operator; do not requeue — raw blob preserved in RawStore          |
   | `http_403`      | SET `system:halted = 1`; emit CRITICAL log; **immediately archive** to `stream:dlq:archive` (do not retry — requires API key rotation) |
   | `unknown`       | Log for operator; do not requeue automatically                            |

3. For requeued messages: reset `attempts = 0`; increment `dlq_attempts`
4. If `dlq_attempts >= DLQ_MAX_ATTEMPTS`:
   - XADD to `stream:dlq:archive`
   - If `payload.match_id` is present: HSET `match:{match_id}.status = failed`; SADD `match:status:failed` `{match_id}`
   - ACK from `stream:dlq`
5. Otherwise (recoverable + dlq_attempts < DLQ_MAX_ATTEMPTS): ZADD `delayed:messages` with score = ready time; ACK from `stream:dlq`

**Halt behaviour:** Recovery implements its own consume loop (`_consume_dlq`) that does not
check `system:halted` before reading from `stream:dlq`, so it continues processing DLQ
entries even when the system is halted. This is necessary to process the `http_403` entries
that caused the halt. **However, when `system:halted` is set, Recovery does NOT requeue
recoverable entries** (`http_429`, `http_5xx`): it leaves them in `stream:dlq` unACKed so
they are re-processed after the system resumes. Only `http_403` is handled (archived +
halted) regardless of system state.

---

## 7. Delay Scheduler Service

**Consumes:** `delayed:messages` (Sorted Set, scored by ready-time epoch ms)

**Produces:** Target streams (as specified in each delayed message's `source_stream` field)

**Reads:**
- `delayed:messages` (Sorted Set) — ZRANGEBYSCORE 0 now_ms

**Writes:**
- Target stream via XADD
- Removes delivered entries from `delayed:messages` via ZREM

**Behavior:**
1. Loop every `DELAY_SCHEDULER_INTERVAL_MS` (default: `500` ms)
2. `now_ms = current epoch ms`
3. `ready = ZRANGEBYSCORE delayed:messages 0 now_ms WITHSCORES`
4. For each ready message:
   a. Deserialize envelope from member string
   b. XADD to `envelope.source_stream`
   c. ZREM `delayed:messages` member
5. Sleep `DELAY_SCHEDULER_INTERVAL_MS` and repeat

**Failure handling:**
- If XADD succeeds but ZREM fails (rare): message stays in `delayed:messages` and will be
  re-delivered to the target stream. Target service handles the duplicate idempotently.
- If the Delay Scheduler crashes: on restart, it picks up all ready messages from the Sorted Set.
  Messages remain safely in `delayed:messages` (persistent Redis) during downtime.
- `system:halted` does NOT stop the Delay Scheduler — it only moves messages; individual
  services won't process them if halted.

---

## 8. Discovery Service

**Reads:**
- `discover:players` (Sorted Set) — member=`{puuid}:{region}`, score=most-recent `game_start` ms
- `stream:puuid` (Stream) — via `XINFO GROUPS` to detect idle pipeline
- `player:{puuid}` (Hash) — existence check (skip if already seeded)

**Produces:** `stream:puuid`
```json
{ "puuid": "<string>", "region": "<string>" }
```

**Writes:**
- `player:{puuid}` (Hash) → fields: `region`, `seeded_at`

**Behavior:**
1. Loop every `DISCOVERY_POLL_INTERVAL_MS` (default: `5000` ms)
2. Check if `stream:puuid` is idle: call `XINFO GROUPS stream:puuid` and verify that all consumer groups have `lag=0` (no undelivered entries) and `pending=0` (no unACKed entries in-flight). If no groups exist or the stream doesn't exist yet, treat as idle.
3. If idle:
   a. `ZREVRANGE discover:players 0 {DISCOVERY_BATCH_SIZE - 1}` — highest score = most recent activity = highest priority
   b. For each member `{puuid}:{region}`:
      - If `player:{puuid}` already exists: ZREM from `discover:players` and skip
      - Otherwise: HSET `player:{puuid}` `{region, seeded_at}`, publish to `stream:puuid`, ZREM from `discover:players`
3. Sleep `DISCOVERY_POLL_INTERVAL_MS` and repeat

**Priority model:**
- Recursive fan-out: Parser adds co-players from every parsed match; Discovery promotes them
- Newest-first: `GT` scoring in ZADD ensures highest game_start score wins; `ZREVRANGE` iterates from highest score
- Idle-only: Discovery never competes with user-triggered seeds (`stream:puuid` length gate)
- User requests (direct seed or UI stats lookup) always take precedence — they publish directly to `stream:puuid` without going through `discover:players`

---

## 9. Admin Service

**Type:** One-shot CLI tool (not a long-running worker)

**Entry point:** `just admin <command>` (runs via Docker/Podman with `profiles: ["tools"]`)

**Commands:**

| Command | Description |
|---------|-------------|
| `stats <GameName#TagLine> [--region]` | Look up player stats from Redis (resolves Riot ID to PUUID first) |
| `system-halt` | Set `system:halted = "1"` — all consumers stop on next message |
| `system-resume` | Clear `system:halted` — resume normal operation |
| `dlq list` | List all entries in `stream:dlq` |
| `dlq replay [--all \| <id>]` | Replay DLQ entries back to their original streams |
| `dlq clear --all` | Delete all DLQ entries |
| `replay-parse --all` | Re-enqueue all parsed matches to `stream:parse` for re-processing |
| `replay-fetch <match_id>` | Re-enqueue a single match ID to `stream:match_id` |
| `reseed <GameName#TagLine> [--region]` | Clear cooldown and re-enqueue a player to `stream:puuid` |
| `recalc-priority` | Recalculate `system:priority_count` by scanning `player:priority:*` keys |

**Reads:**
- `player:stats:{puuid}` (Hash) — for `stats` command
- `stream:dlq` (Stream) — for `dlq list` and `dlq replay`
- `match:status:parsed` (Set) — for `replay-parse`
- `player:priority:*` (String) — for `recalc-priority`

**Writes:**
- `system:halted` (String) — `system-halt` / `system-resume`
- Target streams via XADD — `dlq replay`, `replay-parse`, `replay-fetch`, `reseed`
- `player:{puuid}` (Hash) — `reseed` clears cooldown fields
- `system:priority_count` (String) — `recalc-priority`

---

## 10. Web UI

**Port:** `8080`

**Pages:**

| Route | Description |
|-------|-------------|
| `/` | Redirect to `/stats` |
| `/stats?riot_id=...&region=...` | Player stats — Riot API data + lazy-load match history; auto-seeds player if no data found |
| `/stats/matches?puuid=...&region=...&riot_id=...&page=N` | Fragment: paginated match history rows (lazy-loaded by `/stats`) |
| `/players?page=N` | Paginated player list. Uses `SCAN` to find all `player:{puuid}` keys, fetches metadata via pipeline, sorts by `seeded_at` descending (newest first). 25 players per page with prev/next navigation. Each row links to the player's `/stats` page. |
| `/streams` | Redis stream depths + system halted status + priority player count |
| `/logs` | Merged structured JSON logs from all services with auto-refresh (2s polling). Reads `*.log` files from `LOG_DIR`, tails last 50 lines per file, merges by timestamp via `heapq.merge`. Includes pause/resume button. |
| `/logs/fragment` | Fragment: raw log lines HTML for AJAX polling (used by `/logs` auto-refresh) |

**Auto-seed:** If `/stats` is requested for a player with no data, the UI automatically publishes to `stream:puuid` (same as a manual seed) and shows a "processing" message. No manual seed step required.

**Match history lazy-load:** After stats are displayed, a "Load match history" link fetches `/stats/matches` (paginated, 20 per page) without reloading the page. Each page shows date, result, champion, role, K/D/A, and game mode.

**Env vars:**
- `LOG_DIR` (default unset; set to `/logs` in Docker via `x-service-defaults` environment; required for `/logs` route to function)
