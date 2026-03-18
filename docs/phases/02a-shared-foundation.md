# Phase 02a — Shared Foundation

**Role:** Product Manager
**Objective:** The four foundational modules of `lol-pipeline-common` — configuration, logging, Redis connection, and data models — are complete, tested, and installable. These are the bedrock that every service and Phase 02b depends on.

**Complexity: LOW-MEDIUM** — config/log/models are straightforward; redis_client singleton needs care.

**Value unlocked:** Configuration and data shape are locked in. Any service can import these immediately.

---

## Dependencies

- Phase 01 complete (all repos exist, CI green, dev env works)

---

## Deliverables

1. `config.py` — `pydantic-settings` Settings class with singleton `get_settings()`
2. `log.py` — JSON formatter applied at process startup
3. `redis_client.py` — async connection pool singleton + `health_check()`
4. `models.py` — all envelope and payload dataclasses
5. Unit tests for all four modules
6. One integration test for `redis_client.py` (real Redis via testcontainers)
7. `lol-pipeline-common` version tagged `1.0.0-dev` and installable

---

## Acceptance Criteria

### `config.py`

- AC-2a-01: `Settings()` with no `RIOT_API_KEY` in environment raises `pydantic_settings.ValidationError` (not `KeyError` or any other exception).
- AC-2a-02: `Settings()` with all required vars present: `SEED_COOLDOWN_MINUTES` defaults to `30`; `MAX_ATTEMPTS` defaults to `5`; `DLQ_MAX_ATTEMPTS` defaults to `3`; `STREAM_ACK_TIMEOUT` defaults to `60`; `DELAY_SCHEDULER_INTERVAL_MS` defaults to `500`; `ANALYZER_LOCK_TTL_SECONDS` defaults to `300`.
- AC-2a-03: `SEED_COOLDOWN_HOURS="abc"` raises `ValidationError`.
- AC-2a-04: `RAW_STORE_BACKEND="ftp"` raises `ValidationError`; `RAW_STORE_BACKEND="redis"` and `RAW_STORE_BACKEND="s3"` both pass.
- AC-2a-05: `get_settings()` called 3 times with same env returns the same object instance (identity, not just equality).
- **Unit test count: 5 tests passing.**

### `log.py`

- AC-2a-06: A single log call at any level produces exactly one line on stdout; `json.loads(line)` succeeds.
- AC-2a-07: That JSON object contains keys `timestamp` (ISO-8601 string), `level` (uppercase string), `service` (string), `message` (string).
- AC-2a-08: Extra keyword arguments passed to the logger appear as top-level keys in the JSON object (e.g., `log.info("msg", match_id="ABC")` → `{"match_id": "ABC", ...}`).
- AC-2a-09: No output on stderr for a normal log call (capture stderr, assert empty).
- AC-2a-10: Calling `setup_logging(service="seed")` configures all subsequent log calls to include `"service": "seed"` in output.
- **Unit test count: 5 tests passing.**

### `redis_client.py`

- AC-2a-11: `get_redis()` with a running Redis returns an object that responds to `await redis.ping()` with `True`.
- AC-2a-12: `get_redis()` called twice returns the same pool object (`is` identity).
- AC-2a-13: `health_check()` returns `True` when Redis is reachable.
- AC-2a-14: `get_redis()` when Redis is unreachable (invalid URL) raises `redis.exceptions.ConnectionError` (not hangs indefinitely; completes within 5 seconds).
- **Unit test count: 3 (fakeredis); integration test count: 1 (testcontainers real Redis). All passing.**

### `models.py`

- AC-2a-15: `MessageEnvelope(stream="stream:match_id", payload={"match_id": "NA1_123"})` → `to_redis_fields()` returns a `dict[str, str]` (all values are strings); `MessageEnvelope.from_redis_fields(fields)` round-trips with field-by-field equality.
- AC-2a-16: `DLQEnvelope` with no `failure_code` argument raises `TypeError` or `ValidationError`.
- AC-2a-17: `MessageEnvelope.attempts` defaults to `0` when not specified.
- AC-2a-18: `MessageEnvelope.enqueued_at` is set to ISO 8601 string of current time when not specified.
- AC-2a-19: `PUUIDPayload(puuid="x", region="na1")` passes; `PUUIDPayload(region="na1")` (missing `puuid`) raises `ValidationError` or `TypeError`.
- AC-2a-20: `MatchIdPayload`, `ParsePayload`, `AnalyzePayload` each validate their required fields identically.
- AC-2a-21: `DLQEnvelope.to_redis_fields()` includes all DLQ-specific fields: `failure_code`, `dlq_attempts`, `original_stream`, `source_stream`, `original_message_id`, `retry_after_ms` (serialized as string `"null"` when `None`).
- **Unit test count: 7 tests passing.**

### Coverage

- AC-2a-22: `pytest tests/unit --cov=lol_pipeline.config --cov=lol_pipeline.log --cov=lol_pipeline.redis_client --cov=lol_pipeline.models --cov-fail-under=90` exits 0.

### Total

- **21 unit tests + 1 integration test, all passing.**
- **Branch coverage ≥ 90% on all four modules.**

---

## Notes

- `log.py` must NOT be named `logging.py` (shadows stdlib `logging` module).
- `redis_client.py` uses `redis.asyncio` connection pool. The pool is module-level singleton (initialized once on first call to `get_redis()`).
- `get_settings()` singleton is reset between tests using a fixture that monkeypatches the module-level cache.
- `models.py` uses Python dataclasses (not Pydantic models) for envelope types to keep serialization explicit. Payload types may use Pydantic for field validation.
