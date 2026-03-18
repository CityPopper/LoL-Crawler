# Phase 02b — Shared Protocols

**Role:** Product Manager
**Objective:** The four protocol modules of `lol-pipeline-common` — stream operations, rate limiting, raw storage, and Riot API client — are complete and tested. These are the communication primitives every service uses.

**Complexity: HIGH** — streams.py and rate_limiter.py are the most complex shared code; dual-window Lua, XAUTOCLAIM, backoff logic; require minimum 2 review iterations.

**Value unlocked:** Services can be built. Phase 02b completion is the hard gate before any service work starts. `lol-pipeline-common` is tagged `1.0.0`.

---

## Dependencies

- Phase 02a complete (config, log, redis_client, models all passing at ≥ 90% coverage)

---

## Deliverables

1. `streams.py` — publish, consume, ack, nack_to_dlq, requeue_delayed, pending_redelivery_loop
2. `rate_limiter.py` — dual-window Lua sliding window, `acquire_token()`
3. `raw_store.py` — `RawStore` Protocol, `RedisRawStore`, `S3RawStore` stub, `get_raw_store()` factory
4. `riot_api.py` — async Riot HTTP client with typed exceptions
5. Unit tests for all four modules
6. Integration tests for `streams.py` and `rate_limiter.py` (real Redis via testcontainers)
7. `lol-pipeline-common` tagged `1.0.0`, installable via `pip install git+https://...@1.0.0`

---

## Acceptance Criteria

### `streams.py`

- AC-2b-01: `ensure_consumer_group("stream:test", "group-a")` called twice: no exception on second call (BUSYGROUP error caught and silently ignored); stream created with MKSTREAM if it doesn't exist.
- AC-2b-02: `publish("stream:test", {"key": "val"})` then `consume("stream:test", "group-a", "worker-1")`: returned list contains exactly 1 message with matching payload.
- AC-2b-03: After `ack("stream:test", "group-a", message_id)`: `XPENDING stream:test group-a - + 10` returns 0 entries.
- AC-2b-04: Unacked message + advance time past `STREAM_ACK_TIMEOUT` (freezegun or fakeredis time manipulation): `pending_redelivery_loop` iteration reclaims message; `consume()` returns it again.
- AC-2b-05: `nack_to_dlq(msg, failure_code="http_5xx", failure_reason="...", failed_by="fetcher")` where `msg.attempts=1, msg.max_attempts=5`: `ZCARD delayed:messages` = 1; score of entry = `now_ms + 5000` (±100ms); `XLEN stream:dlq` = 0.
- AC-2b-06: same call with `msg.attempts=2`: score = `now_ms + 15000` (±100ms).
- AC-2b-07: same call with `msg.attempts=3`: score = `now_ms + 60000` (±100ms).
- AC-2b-08: same call with `msg.attempts=4`: score = `now_ms + 300000` (±100ms).
- AC-2b-09: same call with `msg.attempts=5, msg.max_attempts=5`: `XLEN stream:dlq` = 1; `ZCARD delayed:messages` = 0. DLQ entry contains `failure_code`, `failure_reason`, `failed_at`, `failed_by`, `original_stream`, `source_stream`, `dlq_attempts=0`, `original_message_id`.
- AC-2b-09b: `nack_to_dlq(msg, failure_code="http_429", failure_reason="...", failed_by="fetcher", retry_after_ms=31000)` where `msg.attempts=1, msg.max_attempts=5`: `ZCARD delayed:messages` = 1; score = `now_ms + 31000` (±100ms); exponential backoff is NOT used for `http_429` — `retry_after_ms` is used as the delay directly.
- AC-2b-10: `SET system:halted "1"` then `consume(stream, group, consumer)`: returns empty list immediately (does not block or poll).
- AC-2b-10b: `consume(stream, group, consumer, check_halt=False)` with `system:halted` set → does NOT return empty; returns messages normally. Used exclusively by the Recovery service so it can process `http_403` DLQ entries while the system is halted.
- AC-2b-11: `pending_redelivery_loop` claims entries idle > `STREAM_ACK_TIMEOUT` seconds; claimed count matches number of artificially staled entries.
- **Unit test count: 9 (fakeredis); integration test count: 2 (testcontainers). All passing.**

### `rate_limiter.py`

- AC-2b-12: `acquire_token()` is **non-blocking**: calls Lua script once, returns `(True, 0.0)` if token granted or `(False, wait_seconds)` if denied; `wait_seconds` includes a 50ms buffer. Caller is responsible for sleeping and retrying.
- AC-2b-13: 20 sequential `acquire_token()` calls within 1 second: all 20 return `(True, 0.0)`; 21st returns `(False, wait_seconds)` where `wait_seconds > 0` and `wait_seconds ≤ 1.1` (≤1s window + 50ms buffer + rounding).
- AC-2b-14: 100 sequential `acquire_token()` calls within 2 minutes: all 100 return `(True, 0.0)`; 101st returns `(False, wait_seconds)` where `wait_seconds > 0` and `wait_seconds ≤ 120.1`.
- AC-2b-15: At both window limits simultaneously: `wait_seconds = max(short_window_wait, long_window_wait)` (not the minimum; not either one arbitrarily).
- AC-2b-16: `asyncio.gather(*[acquire_token() for _ in range(20)])` with fresh counters: exactly 20 `True` results and 0 `False` results (Lua atomicity).
- AC-2b-17: Same `req_id` passed to `acquire_token()` twice: `ZCARD ratelimit:short` = 1 (not 2); score updated, not duplicated.
- AC-2b-18: Simulated NOSCRIPT error (monkeypatch first call to raise `redis.exceptions.ResponseError("NOSCRIPT")`): Lua script reloaded; second call succeeds; `acquire_token()` returns `(True, 0.0)`.
- **Unit test count: 7 (requires `lupa` for Lua support; skip with clear message if `lupa` unavailable). Integration test count: 1 (testcontainers). All passing.**

### `raw_store.py`

- AC-2b-19: `await store.set("match_1", '{"data": 1}')` then `await store.exists("match_1")` returns `True`.
- AC-2b-20: `await store.get("match_1")` returns `'{"data": 1}'`.
- AC-2b-21: Second `await store.set("match_1", '{"data": 2}')` (NX semantics): `await store.get("match_1")` still returns `'{"data": 1}'`.
- AC-2b-22: `await store.get("nonexistent")` returns `None`; `await store.exists("nonexistent")` returns `False`.
- AC-2b-23: `await redis.keys("raw:match_1")` returns exactly 1 result (key format is `raw:{match_id}`).
- AC-2b-24: `await redis.ttl("raw:match_1")` returns `-1` (no TTL).
- AC-2b-25: `get_raw_store(settings)` with `RAW_STORE_BACKEND="redis"` returns an instance of `RedisRawStore`.
- AC-2b-26: `get_raw_store(settings)` with `RAW_STORE_BACKEND="s3"` returns an instance of `S3RawStore` (stub — calling any method raises `NotImplementedError`).
- **Unit test count: 8 (fakeredis). All passing.**

### `riot_api.py`

- AC-2b-27: Mock 200 from `GET /riot/account/v1/accounts/by-riot-id/{gameName}/{tagLine}`: `client.get_puuid("Faker", "KR1", "kr")` returns dict with `puuid` key.
- AC-2b-28: Mock 200 from `GET /lol/match/v5/matches/by-puuid/{puuid}/ids`: `client.get_match_ids(puuid, "kr")` returns list of strings.
- AC-2b-29: Mock 200 from `GET /lol/match/v5/matches/{matchId}`: `client.get_match("KR_1234", "kr")` returns dict with `metadata` and `info` keys.
- AC-2b-30: Mock 404 → raises `riot_api.NotFoundError`.
- AC-2b-31: Mock 403 → raises `riot_api.AuthError`.
- AC-2b-32: Mock 429 with header `Retry-After: 30` → raises `riot_api.RateLimitError` with attribute `retry_after == 30` (seconds). Fetcher converts this to `retry_after_ms = (retry_after + 1) * 1000 = 31000` (includes 1s buffer). This value is used as the `delayed:messages` score offset for direct retries (attempts < max_attempts) AND stored in `DLQEnvelope.retry_after_ms` when the message is eventually archived to `stream:dlq` (at max_attempts).
- AC-2b-33: Mock 500 → raises `riot_api.ServerError`.
- AC-2b-34: Every client call invokes `acquire_token()` exactly once; verified by `mock_acquire_token.call_count == N` where N is the number of API calls made.
- AC-2b-35: Every request has `User-Agent: lol-pipeline/1.0` in request headers (asserted via respx request history).
- AC-2b-36: Regional routing correct: `na1` → `americas.api.riotgames.com`; `kr` → `asia.api.riotgames.com`; `euw1` → `europe.api.riotgames.com`; `br1` → `americas.api.riotgames.com`.
- AC-2b-37: Empty match ID list from API → `get_match_ids()` returns `[]` (not raises).
- **Unit test count: 11 (respx mocks, no real HTTP). All passing.**

### Coverage

- AC-2b-38: `pytest tests/unit --cov=lol_pipeline.streams --cov=lol_pipeline.rate_limiter --cov=lol_pipeline.raw_store --cov=lol_pipeline.riot_api --cov-fail-under=90` exits 0.

### Total

- **35 unit tests + 3 integration tests, all passing.**
- **Branch coverage ≥ 90% on all four modules.**
- **`lol-pipeline-common` tagged `1.0.0` on main branch.**

---

## Notes

- Rate limiter unit tests require `lupa` (Python Lua binding). Add `lupa` to dev dependencies. Tests marked `@pytest.mark.requires_lua` skip gracefully with `pytest.skip("lupa not installed")` if unavailable, but CI must have `lupa` installed — CI failure if these tests are skipped in CI.
- `fakeredis>=2.2.0` is required for XAUTOCLAIM support (added in that version). Pin this version explicitly.
- Consumer groups are created with `XGROUP CREATE ... $ MKSTREAM` by default — services only see messages published after group creation. This is a known limitation: if a service is added to an existing stream mid-flight, historical messages (before group creation) are not delivered. This must not be "fixed" by using `0` position without explicit decision, as it would cause replaying all historical messages on every service restart.
- `riot_api.py` uses `httpx.AsyncClient` with `base_url` set per-region. The routing map from platform to regional endpoint is a module-level constant, not config. Adding a new platform code requires a code change.
