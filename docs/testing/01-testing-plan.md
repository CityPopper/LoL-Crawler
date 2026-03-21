# Testing Plan

## Philosophy

This project uses **red/green TDD**:

1. **Red** — Write a failing test that describes the desired behaviour
2. **Green** — Write the minimum code to make it pass
3. **Refactor** — Clean up if needed; tests must remain green
4. **Never modify a failing test without explicit confirmation from the project owner**

Tests are the specification. If a test fails, the implementation is wrong — not the test.

---

## Test Infrastructure

| Tool                    | Purpose                                               |
|-------------------------|-------------------------------------------------------|
| `pytest`                | Test runner                                           |
| `pytest-asyncio`        | Async test support (`asyncio_mode = auto`)            |
| `fakeredis[aioredis]`   | In-memory Redis for unit tests (no real Redis needed) |
| `respx`                 | Mock `httpx` HTTP calls with real-shaped responses    |
| `testcontainers`        | Real Redis container for integration tests            |
| `pytest-cov`            | Coverage reporting                                    |
| `freezegun`             | Freeze/manipulate time in tests                       |
| `pytest-xdist`          | Parallel test execution for unit test suites          |

### Test Layout

Each repo has its own `tests/` directory:

```
tests/
├── conftest.py              # shared fixtures (fakeredis, settings override, respx routers)
├── fixtures/
│   ├── match_normal.json    # standard 5v5 match (real Riot API response shape)
│   ├── match_aram.json      # ARAM match
│   ├── match_remake.json    # early surrender / remake (short duration, zeroed stats)
│   ├── match_large.json     # maximum-size match JSON (~200KB, many stats populated)
│   ├── account.json         # standard Account-v1 response
│   └── account_unicode.json # Riot ID with non-ASCII characters (Korean, accent marks)
├── unit/
│   ├── test_config.py
│   ├── test_models.py
│   ├── test_streams.py
│   ├── test_rate_limiter.py
│   ├── test_raw_store.py
│   ├── test_riot_api.py
│   └── test_service.py      # service-specific unit tests
└── integration/
    ├── test_pipeline.py     # end-to-end happy paths
    ├── test_resilience.py   # failure and recovery scenarios
    └── test_admin.py        # admin command integration tests
```

### Fixture Maintenance

Test fixtures in `fixtures/` are real Riot API responses with PUUIDs and player names
anonymized. When the Riot API changes schema on a new patch, fixtures must be updated before
any parser changes. Tests failing due to a fixture update require owner confirmation before
the fixture is modified.

### Test Naming Convention

```python
def test_{component}__{scenario}__[outcome]():
    # e.g.
def test_seed__within_cooldown__skips_publish():
def test_rate_limiter__short_window_full__denies_and_returns_sleep_time():
def test_parser__missing_game_start__routes_to_dlq():
```

---

## Unit Tests: Common Library (`lol-pipeline-common`)

### `config.py`

- All required env vars present → settings object created successfully
- Missing `RIOT_API_KEY` → `ValidationError` on instantiation; message identifies the field
- Missing `REDIS_URL` → `ValidationError`
- `SEED_COOLDOWN_HOURS` provided as `"24"` (string) → coerced to int `24`
- `SEED_COOLDOWN_HOURS` provided as `"abc"` → `ValidationError`
- `MAX_ATTEMPTS` default = `5`; `DLQ_MAX_ATTEMPTS` default = `3`; all defaults verified
- `RAW_STORE_BACKEND = "s3"` → valid; `RAW_STORE_BACKEND = "postgres"` → `ValidationError`
- `ANALYZER_LOCK_TTL_SECONDS` default = `300`
- `DELAY_SCHEDULER_INTERVAL_MS` default = `500`

### `log.py`

- Output is newline-delimited valid JSON parseable by `json.loads`
- Every log record contains: `timestamp` (ISO 8601 UTC), `level`, `service`, `message`
- Extra keyword args appear as top-level JSON fields: `log.info("msg", match_id="x")` → `{"match_id": "x", ...}`
- `level=ERROR` → `"level": "ERROR"` (uppercase)
- Output goes to stdout only (capture stderr in test; assert empty)
- Logging two messages produces two separate JSON lines (not a JSON array)

### `models.py`

- `MessageEnvelope` → `to_redis_fields()` → all values are strings (Redis requirement)
- `MessageEnvelope.from_redis_fields(fields)` round-trips: deserialize → serialize → same dict
- `DLQEnvelope` inherits all envelope fields and adds: `failure_reason`, `failure_code`, `failed_at`, `failed_by`, `dlq_attempts`, `retry_after_ms` (int|None), `original_stream`, `original_message_id`
- `DLQEnvelope.from_redis_fields` raises `ValueError` if `failure_code` is absent
- `DLQEnvelope.from_redis_fields` raises `ValueError` if `failure_code` is not a known code
- Each payload type validates required fields: `PuuidPayload` requires `puuid`; `MatchIdPayload` requires `match_id` and `region`
- `attempts` defaults to `0` when not provided
- `enqueued_at` set automatically to current UTC ISO 8601 if not provided
- `attempts` provided as non-integer string → `ValidationError`
- `max_attempts` must be ≥ 1; `0` → `ValidationError`

### `rate_limiter.py`

- `acquire_token()` is non-blocking: returns `(True, 0.0)` when granted, `(False, wait_seconds)` when denied; caller sleeps and retries
- Request 1 → `(True, 0.0)`; returns immediately
- Request 20 in 1s (short limit) → all return `(True, 0.0)`
- Request 21 in 1s → `(False, wait_seconds)` where `0 < wait_seconds ≤ 1.1`
- Request 100 in 2min (long limit) → allowed
- Request 101 in 2min → denied; returned sleep time ≥ 0ms and ≤ 120050ms
- Short window full AND long window full → sleep time = max(short_wait, long_wait)
- Short window full but long window not → sleep time based on short window only
- After short window expires (freeze time + 1001ms): requests allowed again
- After long window expires (freeze time + 120001ms): requests allowed again
- **Concurrency:** 20 async tasks each calling `acquire_token()` simultaneously → exactly 20 tokens granted, 0 denied (Lua script is atomic in fakeredis)
- **Boundary:** Requests at T=0ms and T=999ms both counted; request at T=1001ms opens new window
- `req_id` collision (same UUID submitted twice) → Sorted Set member updated; `ZCARD` does not increase
- Lua SHA loaded at startup; `NOSCRIPT` error → script reloaded via `SCRIPT LOAD`; call retried automatically
- `BUFFER_MS = 50` included in sleep time calculation to avoid boundary races

### `streams.py`

- `ensure_consumer_group(stream, group)` → group created successfully
- `ensure_consumer_group` called twice on same stream+group → no error (idempotent)
- `publish(stream, envelope)` → XLEN increases by 1; message fields match envelope
- `consume(stream, group, consumer, block_ms=0)` → returns published message with correct fields
- `ack(stream, group, redis_id)` → `XPENDING` count decreases; message no longer in PEL
- Unacknowledged message idle > `STREAM_ACK_TIMEOUT` → `XAUTOCLAIM` returns it; `pending_redelivery_loop` re-delivers
- `nack_to_dlq` with `attempts=0`, `max_attempts=5` → message in `delayed:messages` with score = now + 5000ms (5s backoff); original ACK'd; `attempts` field in delayed envelope = 1
- `nack_to_dlq` with `attempts=4`, `max_attempts=5` → message in `stream:dlq`; `failure_code` and `failure_reason` present; original ACK'd
- Backoff schedule verified: attempt 1→5s, 2→15s, 3→60s, 4→300s (5min)
- `requeue_delayed(envelope, target_stream, delay_ms=3000)` → ZADD to `delayed:messages` with score = now_ms + 3000
- `pending_redelivery_loop` runs as background asyncio task; on shutdown signal, exits cleanly
- `system:halted = "1"` → `consume` returns empty list immediately (no blocking)
- `system:halted` absent → `consume` blocks for `block_ms` waiting for messages
- Consumer from different group receives same messages independently

### `raw_store.py` — `RedisRawStore`

- `set(match_id, data)` → `exists(match_id)` returns `True`
- `get(match_id)` → returns exact `data` string with no transformation
- `set(match_id, data2)` (second call, same key) → `get` still returns first `data` (NX write-once)
- `get(match_id)` on missing key → `None`
- `exists(match_id)` on missing key → `False`
- Redis key used is exactly `raw:{match_id}` (verify via `KEYS raw:*`)
- No TTL on stored key (`TTL raw:{match_id}` returns `-1`)
- Data stored is byte-for-byte identical to input (no encoding transformation)
- Very large payload (200KB string) → stored and retrieved correctly

### `riot_api.py`

- `get_account_by_riot_id("Faker", "KR1", "kr")` with mock 200 → returns dict containing `puuid`
- `get_account_by_riot_id` with mock 404 → raises `NotFoundError`
- `get_account_by_riot_id` with mock 403 → raises `AuthError`
- `get_account_by_riot_id` with mock 429, `Retry-After: 5` → raises `RateLimitError(retry_after=5)`
- `get_account_by_riot_id` with mock 429, no `Retry-After` header → raises `RateLimitError(retry_after=1)` (default)
- `get_account_by_riot_id` with mock 500 → raises `ServerError`
- `get_account_by_riot_id` with connection timeout → raises `ServerError` (not unhandled exception)
- `get_match_ids(puuid, region, start=0, count=100)` → correct query params sent to correct regional host
- `get_match_ids` with empty JSON array response `[]` → returns `[]`
- `get_match(match_id, "na1")` → request goes to `americas.api.riotgames.com`
- `get_match(match_id, "kr")` → request goes to `asia.api.riotgames.com`
- `get_match(match_id, "euw1")` → request goes to `europe.api.riotgames.com`
- Invalid region string → raises `ValueError` with clear message
- Every request: `acquire_token()` called exactly once (assert mock call count)
- Every request: `X-Riot-Token` header = `RIOT_API_KEY` env value
- Every request: `User-Agent` header present and non-empty
- `get_account_by_riot_id` with Riot ID containing Unicode (`한국#KR1`) → URL-encoded correctly

---

## Unit Tests: Per Service

### Seed Service

- Valid `Faker#KR1` → PUUID resolved → `player:{puuid}` HSET → message in `stream:puuid`; correct fields
- Input without `#` → `ValueError` before any API call; no Redis writes
- Input with multiple `#` (`Game#Name#TAG`) → first `#` splits correctly: game_name=`Game`, tag=`Name#TAG`
- `seeded_at` within cooldown (24h), `last_crawled_at` absent → skip; no publish
- `last_crawled_at` within cooldown, `seeded_at` absent → skip; no publish
- Both set; `last_crawled_at` is newer and within cooldown → skip
- Both set; `seeded_at` is newer and within cooldown → skip
- Both set; both older than cooldown → proceed
- Neither set (new player) → proceed
- Cooldown boundary: exactly `SEED_COOLDOWN_HOURS * 3600` seconds ago → proceed (exclusive check)
- Cooldown boundary: 1 second inside cooldown → skip
- `seeded_at` written with correct UTC ISO 8601 timestamp
- Riot 404 → exits 1; `player:{puuid}` not written; no stream message
- Riot 403 → `system:halted = "1"`; exits 1; no stream message
- Riot 429 → exits 1 (one-shot process; caller retries)
- `system:halted` already set at startup → exits immediately; no API call; log contains "halted"

### Crawler Service

- 0 matches → 0 messages in `stream:match_id`; `last_crawled_at` updated
- 1 match → 1 message published
- 100 matches (1 page, page is full) → fetches second page; second page empty → stops; 100 messages
- 250 matches (3 pages: 100+100+50) → 250 messages; correct pagination `start` params sent
- All matches on page 1 already known → stops after page 1; 0 messages; no further API call
- Page 1: 50 known + 50 new; page 2: 100 new; page 3: empty → 150 messages; 3 API calls
- `player:matches:{puuid}` fetched exactly ONCE (single ZRANGE call before pagination loop)
- `last_crawled_at` NOT updated if Riot API error occurs mid-crawl
- `last_crawled_at` IS updated after zero new matches found (successful crawl with nothing new)
- Riot 403 during page 2 → `system:halted = "1"`; does NOT ACK; `last_crawled_at` not updated
- `system:halted` at top of loop → does NOT ACK; exits immediately

### Fetcher Service

- `RawStore.exists = True` → zero API calls; publishes to `stream:parse`; ACKs; `match.status` not overwritten
- Successful fetch → raw blob in RawStore; `match:{id}.status = "fetched"`; message in `stream:parse`; ACKs
- HTTP 404 → `match:{id}.status = "not_found"`; ACKs; zero messages in `stream:parse`
- HTTP 429 with `Retry-After: 10` → `delayed:messages` entry score ≈ now_ms + 11000; original ACK'd
- HTTP 429 without `Retry-After` → uses default delay of 2s
- HTTP 500 → `nack_to_dlq` with `failure_code = "http_5xx"`; backoff applied
- `RawStore.set` raises exception → no message published to `stream:parse`; `nack_to_dlq`
- HTTP 403 → `system:halted = "1"`; does NOT ACK; exits
- After `max_attempts` (5) → message in `stream:dlq`; `failure_reason` describes last error; `failed_by = "fetcher"`
- `failure_code` field present and correct in DLQ entry

### Parser Service

- Valid 5v5 match JSON (from `match_normal.json` fixture) → all Redis keys correct; 10 `stream:analyze` messages
- ARAM match (from `match_aram.json` fixture) → parsed correctly; participant count matches fixture
- Remake match (from `match_remake.json` fixture) → all stats zero/null handled gracefully; `win = 0`
- `RawStore.get` returns `None` → `nack_to_dlq` with `parse_error`; zero Redis writes
- `info.participants` field missing from JSON → `nack_to_dlq` with `parse_error`
- `info.gameStartTimestamp` field missing → `nack_to_dlq` with `parse_error`
- `info.gameStartTimestamp = 0` → `nack_to_dlq` with `parse_error` (zero timestamp is invalid; cursor would never advance)
- Participant `items` all absent (API returns no item fields) → stored as `[0,0,0,0,0,0,0]`
- Participant `items` partially present (`item0` through `item3` only) → missing slots default to `0`
- `win` field as boolean `True` → stored as `"1"`; `False` → `"0"`
- `win` field as integer `1` → stored as `"1"`; `0` → `"0"`
- `win` field absent → stored as `"0"` (default safe)
- Re-parsing same match (already in `match:status:parsed`) → idempotent; no errors; no duplicate `stream:analyze` (Sorted Set score same, SADD no-op)
- `match:status:parsed` contains `match_id` after parse
- `match:participants:{match_id}` Set cardinality = participant count in fixture
- `player:matches:{puuid}` Sorted Set score = `gameStartTimestamp` from fixture (verified per-participant)
- Number of `stream:analyze` messages = number of unique PUUIDs in match
- `system:halted` → no ACK; exits; zero Redis writes

### Analyzer Service

- Lock held by another worker (SETNX fails) → ACKs immediately; no HGETALL or ZINCRBY calls
- Lock acquired (SETNX succeeds) → processes new matches; releases lock; ACKs
- Cursor = `0` → ZRANGEBYSCORE returns all matches; all processed
- Cursor = highest game_start → ZRANGEBYSCORE returns empty; cursor unchanged; ACKs cleanly
- Single new match after cursor → `total_games += 1`; correct totals and derived values
- 5 new matches after cursor → all 5 accumulated; cursor = highest game_start of the 5
- `total_deaths = 0` across all matches → `kda = (kills + assists) / max(0, 1) = kills + assists`; no division by zero
- `total_games = 0` (guard case) → `win_rate`, `avg_kills`, etc. all `0`, not error
- `champion_name = None` in participant hash → champion Sorted Set not updated; no error
- `role = ""` (empty string) in participant hash → role Sorted Set not updated; no error
- `player:champions:{puuid}`: after 3 games on Jinx, 2 on Lux → `ZSCORE` Jinx = 3, Lux = 2
- `player:roles:{puuid}`: correct scores per role
- Lock released after successful run: `EXISTS player:stats:lock:{puuid}` = 0
- **Safe lock release:** simulate lock expiry + re-acquisition by another worker; original worker's release call returns `0` from Lua (no-op); original worker logs warning; original worker still ACKs
- `system:halted` → does NOT ACK; exits

### Recovery Service

- `http_429`, `dlq_attempts = 0` → message in `delayed:messages`; `dlq_attempts = 1` in requeued envelope; ACK'd
- `http_429`, `dlq_attempts = DLQ_MAX_ATTEMPTS (3)` → message in `stream:dlq:archive`; `match.status = "failed"` if payload has `match_id`; ACK'd from `stream:dlq`
- `http_5xx`, `dlq_attempts = 0` → requeued with correct exponential backoff delay
- `http_5xx`, `dlq_attempts = 1` → delay longer than `dlq_attempts = 0` (backoff increases)
- `http_404` → ACK'd and discarded; zero ZADD calls; `stream:dlq:archive` unchanged
- `parse_error` → ACK'd; logged; no requeue; `stream:dlq:archive` unchanged
- `http_403` → `system:halted = "1"` SET; CRITICAL log emitted; message ACK'd; Recovery continues processing
- `system:halted = "1"` set; DLQ contains `http_5xx` entry → Recovery leaves it unACKed (does not requeue, does not archive); `ZCARD delayed:messages` unchanged
- `unknown` → ACK'd; logged; no requeue
- `dlq_attempts` incremented on each pass: `0 → 1 → 2 → DLQ_MAX_ATTEMPTS → archived`
- `match.status = "failed"` written when archiving fetch/parse failure (payload has `match_id`)
- `match.status` NOT written when archiving crawl failure (payload has `puuid` not `match_id`)
- `match:status:failed` Set updated when `match.status = "failed"` is written
- Empty DLQ → blocking wait; service does not spin; CPU near zero

### Delay Scheduler Service

- `delayed:messages` empty → no XADD calls; no errors; loop continues
- Message with score = now + 5000ms → not moved; still in `delayed:messages`
- Message with score = now - 1ms (past) → XADD to `source_stream`; ZREM from `delayed:messages`
- Message with score = now exactly → moved (boundary: inclusive)
- 3 messages all past-due → all 3 moved in single iteration; `ZCARD delayed:messages` = 0
- 2 messages past-due + 1 future → only 2 moved; future message remains
- After move: deserialized envelope appears in target stream with correct fields
- ZREM failure (simulate) → message remains in `delayed:messages`; XADD not re-called in same iteration
- 1000 ready messages in single iteration → all moved; no timeout or memory error
- Shutdown signal (SIGTERM / asyncio cancellation) → current iteration completes; exits cleanly

### Admin CLI

- `admin dlq list` with empty DLQ → exits 0; output is empty or "no messages" message
- `admin dlq list` with 3 entries → 3 entries printed; each includes `failure_code`, `failed_at`, `failed_by`
- `admin dlq replay --all` → all DLQ messages re-published to respective `source_stream`; DLQ empty after
- `admin dlq replay <id>` → only that message re-published; others unchanged
- `admin dlq replay <nonexistent_id>` → exits 1; error message; no side effects
- `admin dlq clear --all` → DLQ empty; messages NOT moved to archive; ACK'd
- `admin dlq clear <id>` → only that message removed
- `admin replay-parse --all` → reads `match:status:parsed` Set; publishes one `stream:parse` message per match_id
- `admin replay-parse <match_id>` → single message published to `stream:parse`
- `admin replay-fetch <match_id>` → single message published to `stream:match_id`
- `admin reseed "Faker#KR1"` → DELs `last_crawled_at` and `seeded_at`; publishes to `stream:puuid`
- `admin stats "Faker#KR1"` → resolves PUUID; prints all fields of `player:stats:{puuid}`
- `admin stats "Unknown#NA1"` (player not in Redis) → exits 1; "player not found" message
- `admin system-resume` → DELs `system:halted`; prints confirmation message
- `admin system-resume` when `system:halted` not set → exits 0; no error

---

## Integration Tests

Integration tests use a real Redis instance via `testcontainers`. Riot API calls are mocked
with `respx` returning responses built from fixture files.

### Happy Path

```
seed("Faker#KR1", region="kr")
  → stream:puuid: 1 message with correct puuid, region
  → crawl(puuid, region)  [mock: returns 3 match IDs]
  → stream:match_id: 3 messages
  → fetch each  [mock: returns match_normal.json for each]
  → RawStore: 3 blobs; stream:parse: 3 messages; match.status = "fetched" for each
  → parse each
  → match:{id}: 3 hashes with status=parsed
  → match:participants:{id}: 3 Sets of 10 PUUIDs each
  → player:matches:{puuid}: populated for all participants
  → match:status:parsed: contains all 3 match IDs
  → stream:analyze: messages for all unique PUUIDs
  → analyze each unique PUUID
  → player:stats:{puuid}: correct totals and derived fields for all analyzed players
```

Verify final state assertions:
- `HGET match:{id} status` = `"parsed"` for all 3
- `SMEMBERS match:status:parsed` contains all 3 match IDs
- `HGET player:stats:{puuid} total_games` = correct count
- `HGET player:stats:{puuid} win_rate` matches wins/total_games

### Re-run Idempotency

```
Full pipeline for player X (3 matches)
Re-seed player X immediately (within cooldown) → skip; 0 new stream:puuid messages
Manually clear seeded_at and last_crawled_at
Re-seed player X → proceeds; 1 stream:puuid message
Re-crawl → all match IDs already in player:matches:{puuid} → 0 stream:match_id messages
No duplicate data in any Redis structure
```

### 429 Recovery End-to-End

```
Fetcher receives match_id
Mock Riot API: first call returns 429 (Retry-After: 2); second call returns 200 + fixture
  → nack_to_dlq → delayed:messages score ≈ now + 3000ms
  → Delay Scheduler (poll interval: 100ms for test) fires → XADD to stream:match_id
  → Fetcher picks up requeued message → mock returns 200
  → RawStore has blob; stream:parse has message; match.status = "fetched"
```

### DLQ Exhaustion

```
Fetcher receives match_id
Mock Riot API always returns 500
  → After MAX_ATTEMPTS (5) deliveries → message in stream:dlq
  → Recovery processes → requeues (dlq_attempts = 1) → fails again → (dlq_attempts = 2)
  → After DLQ_MAX_ATTEMPTS (3) → archived to stream:dlq:archive
  → match:{id}.status = "failed"
  → SMEMBERS match:status:failed contains match_id
```

### Crash Recovery

```
Parser consumes from stream:parse; process killed before ACK
  → After STREAM_ACK_TIMEOUT (set to 2s in test): pending_redelivery_loop claims entry
  → Parser re-processes the message; all Redis writes succeed; ACKs
  → match.status = "parsed"; no corruption
```

### system:halted Propagation

```
Fetcher receives 403
  → system:halted = "1"
  → Fetcher exits (no ACK on the 403 message)
  → Other services (crawler, parser, analyzer) exit at top of next loop
  → Delay Scheduler continues moving messages (does not check halted)
  → All stream pending entries remain; no data lost
  → admin system-resume → DEL system:halted
  → docker compose restart (simulated) → services restart; pick up pending messages; proceed normally
```

### Concurrent Workers: No Corruption

```
2 Parser workers; 20 parse messages in stream:parse
  → Consumer group ensures each message delivered to exactly one worker
  → All 20 match hashes written; SADD idempotent (no extra PUUIDs in Sets)

2 Analyzer workers; stream:analyze flooded with same PUUID
  → One acquires lock; other ACKs and discards each time
  → player:stats correct after all messages consumed
  → No double-counting in totals
```

### Admin: replay-parse End-to-End

```
Parser ran successfully on 5 matches
admin replay-parse --all
  → 5 messages published to stream:parse
  → Parser re-processes all 5 (idempotent)
  → No data changes (HSET same values; SADD/ZADD same members)
```

---

## Edge Case Categories

### Boundary Conditions

- Cooldown `seeded_at` exactly `SEED_COOLDOWN_HOURS * 3600` seconds ago → proceeds (exclusive)
- Cooldown `seeded_at` one second before boundary → skips
- Rate limiter: exactly at Nth token (last allowed) → allowed; N+1 → denied
- Sorted Set cursor at exactly the score of the last processed match → ZRANGEBYSCORE returns empty
- Empty player history (0 matches from API) → Crawler writes `last_crawled_at`; 0 messages published
- Player with exactly 1 match → full pipeline produces correct single-game stats
- `gameStartTimestamp` = 1 (minimum valid) → stored; cursor advances correctly
- `gameStartTimestamp` = 0 → rejected as invalid by Parser (`parse_error`)
- Delay Scheduler: message score = `now_ms` exactly → moved (boundary is inclusive `≤`)

### Idempotency

- Same `match_id` delivered twice to Fetcher → blob exists on second delivery; no API call; idempotent
- Same match parsed twice → HSET same values; SADD/ZADD idempotent; extra `stream:analyze` messages harmless (cursor handles them)
- `ensure_consumer_group` called 10× → no errors; group exists once
- Delay Scheduler processes same message twice (ZREM failed on first) → target stream has 2 entries; service handles second delivery idempotently

### Concurrency & Race Conditions

- Rapid double-seed of same PUUID → at most 2 messages in `stream:puuid`; Crawler handles both idempotently
- Two Fetcher workers receive same `match_id` → one writes blob; other finds it exists; both succeed
- Two Analyzer workers for same PUUID → lock ensures serial processing; stats correct
- Rate limiter: 20 concurrent workers → total req/s ≤ 20 (verified against Redis counter)
- Analyzer lock stolen (TTL expiry before release) → Lua ownership check returns 0; original worker logs warning; stats still consistent (second worker processed remaining matches)

### Error Propagation

- `http_403` mid-batch → `system:halted` set; in-flight messages remain in pending; redelivered after restart
- Multiple consecutive `http_429` responses → backoff increases each time through Recovery
- `parse_error` never auto-retried → stays in DLQ; raw blob preserved; `admin dlq replay` triggers re-parse
- DLQ exhaustion → archived; `match.status = "failed"`; secondary index updated

### Data Integrity

- `player:stats:{puuid}` raw totals sum correctly: manually count wins/kills/etc. from participant hashes
- `player:matches:{puuid}` Sorted Set score for each member = `game_start` field of corresponding `match:{id}` hash
- `match:participants:{match_id}` Set cardinality = participant count in `match:{id}` hash
- `match:status:parsed` contains exactly the match IDs where `HGET match:{id} status` = `"parsed"`
- `match:status:failed` contains exactly the match IDs where `HGET match:{id} status` = `"failed"`
- `raw:{match_id}` JSON is structurally valid and round-trips through the parser without data loss

### Input Validation

- Riot ID with Unicode game_name (`한국어#KR1`) → Seed encodes correctly; Account-v1 API called with URL-safe encoding
- Riot ID with spaces in game_name (`Space Name#NA1`) → handled correctly (spaces URL-encoded)
- Riot ID with empty tag (`GameName#`) → `ValueError` before API call
- Riot ID with empty game_name (`#TAG`) → `ValueError` before API call
- Invalid region string (`"xx1"`) → `ValueError` from Riot API client before HTTP call
- Very long game_name (> 100 chars) → `ValueError` before API call (Riot max is ~16 chars)

### Clock & Time

- Matches played at same second → ZADD adds both; Sorted Set contains both (different match_ids, same score)
- `enqueued_at` in message envelope uses UTC (not local time)
- `freezegun` confirms all `now()` calls in services use UTC
- Delay Scheduler: `DELAY_SCHEDULER_INTERVAL_MS = 100` in tests; messages move promptly

### Resource & Scale

- Player with 3000 matches → Analyzer processes all in single lock acquisition; lock not stolen (TTL 300s)
- `match_large.json` fixture (~200KB) → RawStore stores and retrieves without truncation
- 500 `stream:match_id` messages → Fetcher processes all within rate limit constraints; no DLQ entries
- `delayed:messages` with 1000 simultaneous ready entries → Scheduler moves all in one iteration without timeout

### Redis Failure

- Redis connection lost mid-HSET (simulated by closing fakeredis connection) → service raises; message not ACK'd; redelivered after timeout
- Rate limit keys absent (Redis restarted, memory cleared) → rate limiter starts fresh; does not crash; enforces new limits from zero

---

## Contract Tests

### Philosophy: Consumer-Driven Contract Testing (CDCT)

Each service is treated as a black box that knows **only its own input and output contracts**.
Contract tests enforce this isolation structurally: the consumer defines what it needs; the
provider verifies it can produce that. No shared knowledge of pipeline topology is permitted.

All inter-service contracts use **Pact v3 Message Pacts** (`pact-python>=2.0.0`). Pact files
live in the consumer's repo under `pacts/`. Canonical schemas live in
`lol-pipeline-common/contracts/schemas/` and are the DRY source of truth.

See `lol-pipeline-common/contracts/README.md` for the full workflow.

---

### Pact File Locations

| Consumer                       | Provider               | Pact File                                                         |
|--------------------------------|------------------------|-------------------------------------------------------------------|
| `lol-pipeline-crawler`         | `lol-pipeline-seed`    | `lol-pipeline-crawler/pacts/crawler-seed.json`                    |
| `lol-pipeline-fetcher`         | `lol-pipeline-crawler` | `lol-pipeline-fetcher/pacts/fetcher-crawler.json`                 |
| `lol-pipeline-parser`          | `lol-pipeline-fetcher` | `lol-pipeline-parser/pacts/parser-fetcher.json`                   |
| `lol-pipeline-analyzer`        | `lol-pipeline-parser`  | `lol-pipeline-analyzer/pacts/analyzer-parser.json`                |
| `lol-pipeline-recovery`        | `lol-pipeline-common`  | `lol-pipeline-recovery/pacts/recovery-common.json`                |
| `lol-pipeline-delay-scheduler` | `lol-pipeline-common`  | `lol-pipeline-delay-scheduler/pacts/delay-scheduler-common.json`  |

---

### Consumer Tests (per service)

Each service's `tests/contract/test_consumer.py` uses the `pact-python` `MessageConsumer`
to define what the service expects to receive. Running these tests generates/updates the
pact JSON file. Consumer tests must pass before any provider changes are merged.

**What each consumer test must assert:**
- The consumer can fully process the example message without error
- All required payload fields are present and correctly typed
- No fields outside the contract are accessed (service isolation check)

**Per-stream messages to cover:**

| Consumer   | Stream           | Messages in pact                          |
|------------|------------------|-------------------------------------------|
| Crawler    | `stream:puuid`   | valid PUUID message                       |
| Fetcher    | `stream:match_id`| valid match_id message                    |
| Parser     | `stream:parse`   | match with raw blob in RawStore           |
| Analyzer   | `stream:analyze` | PUUID with participant data in Redis      |
| Recovery   | `stream:dlq`     | one message per `failure_code` (5 total)  |
| Delay Scheduler | `delayed:messages` | match_id envelope, parse envelope    |

---

### Provider Verification Tests (per service)

Each service's `tests/contract/test_provider.py` uses `MessageProvider` to verify it can
produce messages that satisfy all consumer pact files that reference it as provider.

**Provider → Consumers it must satisfy:**

| Provider               | Consumer(s)                        | Pact files loaded from                     |
|------------------------|------------------------------------|--------------------------------------------|
| `lol-pipeline-seed`    | Crawler                            | `../lol-pipeline-crawler/pacts/`           |
| `lol-pipeline-crawler` | Fetcher                            | `../lol-pipeline-fetcher/pacts/`           |
| `lol-pipeline-fetcher` | Parser                             | `../lol-pipeline-parser/pacts/`            |
| `lol-pipeline-parser`  | Analyzer                           | `../lol-pipeline-analyzer/pacts/`          |
| `lol-pipeline-common`  | Recovery, Delay Scheduler          | `../lol-pipeline-recovery/pacts/`, `../lol-pipeline-delay-scheduler/pacts/` |

Provider verification tests must not mock the serialization layer — they call real
`to_redis_fields()` / `from_redis_fields()` to prove the contract holds end-to-end.

---

### Contract Verification Checklist (run on every service modification)

1. If output schema changes → update consumer's pact file → run provider verification
2. If input contract changes → update consumer test first (red/green TDD) → update pact file → provider must pass
3. Envelope or DLQ schema change → update `contracts/schemas/` first → update all affected pact files
4. All contract tests must be green before integration tests run

---

### Existing Contract Verifications (no Pact tooling required)

These verify data integrity across service boundaries using fixtures and real serialization,
supplementing the Pact message shape contracts:

| Contract                              | Verification                                                         |
|---------------------------------------|----------------------------------------------------------------------|
| Riot API shape → Parser               | `match_normal.json` fixture parses without `parse_error`; all expected fields present |
| Riot API shape → Parser (ARAM)        | `match_aram.json` parses correctly; participant count correct         |
| Riot API shape → Parser (remake)      | `match_remake.json` parses; zero-value stats stored, not errored     |
| Parser → Analyzer: participant hash   | Fields written by Parser's `HSET participant:...` match what Analyzer reads via `HGETALL` |
| Parser → `match:status:parsed`        | After parsing, `match_id` in `match:status:parsed` Set              |
| Recovery → `match:status:failed`      | After DLQ archive, `match_id` in `match:status:failed` Set          |
| Envelope round-trip (all types)       | `to_redis_fields → from_redis_fields` for every envelope type; no data loss |
| DLQ envelope round-trip               | All failure fields preserved across serialization                    |

---

## CI Pipeline

```yaml
# Per service repo (GitHub Actions example)
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install common library
        run: pip install "lol-pipeline-common @ git+https://github.com/org/lol-pipeline-common.git@${COMMON_VERSION:-main}"
      - name: Install service deps
        run: pip install -e ".[dev]"
      - name: Unit tests
        run: pytest tests/unit --cov --cov-report=xml -x
      - name: Contract tests
        run: pytest tests/contract -x
        # validates pact JSON files + serialization round-trips; no Redis or external services needed
      - name: Integration tests
        run: pytest tests/integration -x
        # testcontainers starts Redis automatically; no external service needed
      - name: Upload coverage
        uses: codecov/codecov-action@v4
```

**Coverage targets:** `lol-pipeline-common` ≥ 90% · Each service ≥ 80%

**Test ordering (red/green TDD):** Write all unit tests for a module first (red — they will fail),
then implement until they pass (green), then move to the next module. Contract tests must pass
before integration tests run. Do not skip ahead to integration tests until all unit and contract
tests in a phase are green.
