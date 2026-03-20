# TODO — Improvement Proposals (All 21 Agents)

Phase 7 "IRONCLAD" and Phase 8 "FACELIFT" are **complete**.
546 unit tests + 61 contract tests. 21 agentic coding agents.

---

## Critical Bugs (Orchestrator Cycle 2)

### Critical
- [ ] B1: `raw:match:*` no TTL + `maxmemory-policy noeviction` = pipeline OOM at ~50K matches (`raw_store.py:126`)
- [ ] B2: Discovery `_resolve_names` crashes on missing `gameName`/`tagLine` for deleted/banned accounts → infinite crash loop (`discovery/main.py`)

### High
- [ ] B3: `_raise_for_status` crashes on non-integer Retry-After header (HTTP-date format) (`riot_api.py:102`)
- [ ] B4: Admin + UI DLQ pages crash on any corrupt DLQ entry (`admin/main.py`, `ui/main.py`)
- [ ] B5: Admin `_make_replay_envelope` resets `enqueued_at` and `dlq_attempts=0` — loses metadata (`admin/main.py`)
- [ ] B6: Crawler missing `NotFoundError` (404) handler → unnecessary DLQ cycles for non-existent accounts
- [ ] B7: CI `docker-build` not gated on lint/typecheck/test — broken builds can publish images
- [ ] B8: Worker Dockerfiles missing `--start-period=60s` on HEALTHCHECK — 9 services restart prematurely
- [ ] B9: CI `mypy --ignore-missing-imports` overrides `strict=true` — hides real type errors

### Medium
- [ ] B10: Redis connection pool has no socket timeout → hung connections block all coroutines
- [ ] B11: Priority keys have no TTL → orphaned keys permanently block Discovery if message lost in transit
- [ ] B12: In-memory retry counter lost on service restart → poison messages loop forever (formal invariant V3)
- [ ] B13: `match:participants:{match_id}` sets are write-only, never read (~90MB waste at 10K players)
- [ ] B14: `ratelimit:limits:short/long` keys have no TTL → stale limits persist after API key rotation
- [ ] B15: Recovery `_consume_dlq` lacks XAUTOCLAIM → stranded DLQ messages after worker crash

### Complexity Refactors
- [ ] C1: `_crawl_player` — extract `_fetch_match_ids_paginated()` + `_handle_crawl_error()` helpers
- [ ] C2: `show_stats` — extract `_resolve_and_cache_puuid()`, `_auto_seed_player()`, `_build_stats_response()`

## New Findings (Orchestrator Cycle 3 — Review Iteration 2)

### Critical (NEW)
- [ ] I2-C1: `nack_to_dlq` never passes `dlq_attempts` → DLQ exhaustion mechanism completely broken + backoff always 5s
- [ ] I2-C2: Discovery idle check only watches `stream:puuid` → promotes players into backlogged pipeline
- [ ] I2-C3: `match:{match_id}` + `participant:` hashes no TTL → unbounded Redis growth (~1.5GB+ at 10K players)

### High (NEW)
- [ ] I2-H1 through I2-H15 [see CLAUDE.md for details]

---

## Fuzzing Targets

Fuzz-worthy functions with high input-surface risk. Each should get a Hypothesis property-based test.

- **`MessageEnvelope.from_redis_fields`** — `lol-pipeline-common/src/lol_pipeline/models.py:46-57`
  - Generate dicts with random subsets of required keys, random value types (int, bytes, None, empty string)
  - Verify: either returns valid `MessageEnvelope` or raises one of `(KeyError, json.JSONDecodeError, ValueError, TypeError)`
  - Verify: never raises unexpected exception types
  - Verify: round-trip (`to_redis_fields` -> `from_redis_fields`) is identity

- **`DLQEnvelope.from_redis_fields`** — `lol-pipeline-common/src/lol_pipeline/models.py:102-121`
  - Generate dicts with random subsets of required keys, extra unknown keys, null values
  - Verify: either returns valid `DLQEnvelope` or raises one of `(KeyError, json.JSONDecodeError, ValueError, TypeError)`
  - Verify: `retry_after_ms` parsing handles non-numeric, negative, overflow
  - Verify: round-trip (`to_redis_fields` -> `from_redis_fields`) is identity

- **`riot_api._raise_for_status`** — `lol-pipeline-common/src/lol_pipeline/riot_api.py:92-104`
  - Generate mock `httpx.Response` objects with status codes 100-599
  - Verify: 200 returns data, 404 raises NotFoundError, 401/403 raises AuthError, 429 raises RateLimitError, 5xx raises ServerError
  - Verify: any other status code (1xx, 3xx, 4xx except 401/403/404/429) raises ServerError
  - Verify: 429 with malformed/missing `Retry-After` header does not crash

- **`_derived` (extract player stats)** — `lol-pipeline-analyzer/src/lol_analyzer/main.py:32-46`
  - Generate dicts with missing keys, zero values, negative values, non-numeric strings
  - Verify: never raises ZeroDivisionError (deaths=0 path)
  - Verify: returns empty dict when total_games=0
  - Verify: handles extremely large stat values without overflow

- **`_parse_match`** — `lol-pipeline-parser/src/lol_parser/main.py:76-181`
  - Generate random bytes, truncated JSON, valid JSON missing required fields
  - Verify: always results in either successful parse + ack, or nack_to_dlq + ack
  - Verify: never leaves message unacknowledged (except system:halted)
  - Verify: corrupt participant data is skipped without crashing the whole match

- **`_format_stat_value`** — `lol-pipeline-ui/src/lol_ui/main.py:334-349`
  - Inputs: `"nan"`, `"inf"`, `"-inf"`, `""`, `"999999999999"`, `"-0.0"`, very long strings, unicode
  - Verify: never raises an unhandled exception
  - Verify: returns a non-empty string for all valid stat keys
  - Verify: win_rate, avg_*, and kda keys always produce human-readable output

- **`_badge`** — `lol-pipeline-ui/src/lol_ui/main.py:365-373`
  - Generate random strings for variant, empty string, unicode, strings containing HTML/JS
  - Verify: raises `ValueError` for invalid variants
  - Verify: valid variants produce well-formed HTML
  - Verify: text containing `<script>` or HTML entities does not produce injectable output

- **Redis key construction with unicode/special chars**
  - Generate PUUIDs and match_ids containing unicode, colons, newlines, null bytes, spaces
  - Verify: `f"player:{puuid}"` and `f"match:{match_id}"` keys are safe for Redis
  - Verify: keys with special chars don't cause Redis protocol errors or key collisions
  - Verify: round-trip through HSET/HGET preserves the key correctly

- **`_parse_log_line`** — `lol-pipeline-ui/src/lol_ui/main.py:1152-1163`
  - Fuzz with arbitrary strings, nested JSON, extremely long lines, binary data mixed with UTF-8
  - Verify: always returns a 5-tuple of strings, never crashes

- **`_validate` in parser** — `lol-pipeline-parser/src/lol_parser/main.py:27-34`
  - Fuzz with deeply nested dicts, missing `info`/`metadata`, empty participant lists, non-dict types
  - Verify: raises `KeyError` for missing required fields, never panics on unexpected input shapes

- **`RawStore._search_bundle_file`** — `lol-pipeline-common/src/lol_pipeline/raw_store.py:95-98`
  - Fuzz with corrupted JSONL bundles, lines with no tab separator, binary content
  - Verify: returns None for non-matching lines, never crashes

---

## Integration Test Scenarios

- **IT-08: Seed with priority → verify Discovery paused until complete**
  - Seed a player with `priority="high"` via `stream:puuid`
  - Verify `system:priority_count` is set to 1
  - Verify Discovery service's polling loop skips `discover:players` while priority_count > 0
  - Wait for full pipeline to complete (analyze), verify priority_count returns to 0
  - Verify Discovery resumes after priority clears

- **IT-09: Two manual seeds → verify both process before any discovery**
  - Seed player A and player B in rapid succession with `priority="high"`
  - Verify `system:priority_count` reaches 2
  - Verify neither Discovery poll occurs until both A and B complete the full pipeline
  - Verify final priority_count is 0

- **IT-10: Priority TTL expiry → verify Discovery resumes after 24h (mock time)**
  - Seed a player with priority, then simulate pipeline stall (never analyze)
  - Use `freezegun` or manual Redis key manipulation to advance `player:priority:{puuid}` past TTL
  - Verify `system:priority_count` naturally decrements when TTL expires
  - Verify Discovery resumes polling after the priority key expires

- **IT-11: DLQ round-trip preserves priority field**
  - Publish a `priority="high"` message that will fail (e.g., mock 429 from Riot API)
  - Verify the DLQEnvelope in `stream:dlq` has `priority="high"`
  - Let Recovery requeue to `delayed:messages`
  - Let Delay Scheduler dispatch back to source stream
  - Verify the redelivered MessageEnvelope still has `priority="high"`
  - (Note: currently broken — see "DLQ round-trip loses priority" in Immediate Code Fixes)

- **IT-12: Concurrent fetchers respect rate limit under load**
  - Start 3 fetcher instances with `api_rate_limit_per_second=5`
  - Enqueue 50 match_id messages simultaneously
  - Mock Riot API to record request timestamps
  - Verify no more than 5 requests per second across all fetchers
  - Verify no more than ~100 requests per 120-second window

- **IT-13: Parser handles Riot API schema change gracefully (missing fields)**
  - Store a raw match JSON blob with missing `participants[].championName` field
  - Store another with missing `info.gameStartTimestamp`
  - Verify parser sends missing-timestamp match to DLQ with `parse_error`
  - Verify parser processes missing-champion match successfully (falls back to empty string)
  - Verify `match:status:parsed` only contains the successful match

- **IT-14: Full pipeline E2E: seed → crawl → fetch → parse → analyze → UI displays stats**
  - Seed one player via `just seed` or direct stream publish
  - Mock Riot API for account lookup, match IDs (2 matches), and match data
  - Wait for all pipeline stages to complete (poll `player:stats:{puuid}`)
  - Query UI `/stats?riot_id=...` endpoint
  - Verify response contains correct win_rate, KDA, champion data
  - Verify `player:matches:{puuid}` has 2 entries
  - Verify `match:status:parsed` contains both match IDs

---

## Performance Issues Found

- **`/players` uses SCAN to enumerate all players** — `lol-pipeline-ui/src/lol_ui/main.py:732`
  - `r.scan_iter(match="player:*")` then filters by colon count. At scale this is O(N) over all keys.
  - Fix: replace with a `players:all` sorted set (score = seeded_at epoch).
  - Subtask 1: Add ZADD to `players:all` in seed, UI auto-seed, and discovery promote
  - Subtask 2: Replace SCAN with ZREVRANGE on `/players` endpoint
  - Subtask 3: Add `admin recalc-players` command to rebuild from existing keys

- **Analyzer creates a new pipeline per match in a loop** — `lol-pipeline-analyzer/src/lol_analyzer/main.py:94-104`
  - When processing N new matches, it creates N separate MULTI/EXEC pipelines. Batch all HINCRBY/ZINCRBY commands into one pipeline, then execute once.

- **RawStore `_exists_in_bundles` scans all JSONL files twice** — `lol-pipeline-common/src/lol_pipeline/raw_store.py:109-111`
  - `_search_bundles()` does a full scan. In `set()` at line 139, `_exists_in_bundles` is called after Redis NX succeeds, causing a redundant full-file scan.

- (Phase 9) Cap `discover:players` sorted set — ZREMRANGEBYRANK to bound growth
- (Phase 9) Raw blob TTL/eviction — `raw:match:*` dominates memory at scale. Add configurable TTL to `RawStore.set()`.
- (Phase 9) Parser `_write_participant` calls pipeline per participant — `lol-pipeline-parser/src/lol_parser/main.py:45-72`
  - With 10 participants per match, that is 10 pipeline round-trips. Accumulate all commands into one pipeline.
- (Phase 9) Parser discovery check is N+1 — `lol-pipeline-parser/src/lol_parser/main.py:164-165`
  - Calls `HEXISTS` per puuid in loop. Batch with pipeline.
- (future) RawStore: sorted JSONL bundles + binary search
- (future) Discovery batch pipelining when batch_size > 10
- (future) Redis connection pool tuning docs

## Security Issues Found

- **UI `player:name:` cache has no TTL** — allows unbounded memory growth if attacker queries many unique Riot IDs. Add `ex=86400` and cap total cache size.
- **UI auto-seed has no rate limiting** — `lol-pipeline-ui/src/lol_ui/main.py:663-694`. Any anonymous user can trigger unlimited `publish()` calls by querying new player names. Add per-IP rate limit or per-session cooldown.
- **No input validation on `region` parameter** — `lol-pipeline-ui/src/lol_ui/main.py:579`. Accepts any string for `region` from query params. Validate against `_REGIONS` list before use.
- **Admin CLI `_resolve_puuid` prints unsanitized input to stderr** — `admin/main.py:66`. If riot_id contains terminal escape sequences, this could be a terminal injection vector. Sanitize output.
- (Phase 9) Content-Security-Policy header with nonce-based `script-src` — `ui/main.py` has inline `<script>` blocks (lines 497-518, 792-807, 880-903, 1251-1275) that would be blocked by strict CSP
- (Phase 9) Redis ACLs — per-service users with minimal permissions
- (Phase 9) TLS reverse proxy docs (Caddy/nginx config)
- (Phase 9) Redis TLS (`rediss://`) for production
- (Phase 9) Redis `requirepass` in dev compose
- (future) Authentication / API gateway for Web UI
- (future) Audit log for admin operations

## Architecture Issues Found

- **Discovery module-level `_shutdown` global** — `discovery/main.py:24` and `delay-scheduler/main.py:22`
  - Use `global _shutdown`. This breaks if two event loops or tests run in the same process. Use `asyncio.Event` instead.
- **Recovery duplicates consume logic from streams.py** — `lol-pipeline-recovery/src/lol_recovery/main.py:56-83`
  - Re-implements `_ensure_group`, deserialization, PEL drain — same pattern as `streams.consume()` but for `DLQEnvelope`. Extract a generic `consume_typed()` or pass a deserializer.
- **Envelope schema mismatch** — `contracts/schemas/envelope.json:47`
  - Defines `dlq_attempts` as `type: "string"` but `models.py:29` stores it as `int` and `to_redis_fields()` converts to `str(self.dlq_attempts)`. Schema should say `type: "integer"` to match Python model.
- (Phase 9) Extract stream name constants to `lol_pipeline.constants` — `"stream:puuid"`, `"stream:match_id"`, etc. are string literals in every service.
- (Phase 9) Configurable priority TTL (`PRIORITY_TTL_SECONDS`) — `priority.py:9` hardcodes 86400
- (Phase 9) Document Delay Scheduler single-instance assumption
- (future) Correlation/trace ID through pipeline messages
- (future) Event sourcing for replay/audit
- (future) Circuit breaker for Riot API
- (future) S3 backend for RawStore

## Test Performance

- **Each unit test must complete in ≤0.5s** — find and fix any slow tests across all `lol-pipeline-*/tests/unit/` suites
  - Profile with `pytest --durations=20` to identify the slowest tests
  - Common causes: real `asyncio.sleep()` calls, large fixture setup, unpatched I/O, `time.sleep()` in tested code paths
  - Fix: mock all sleeps (`unittest.mock.patch`), use `AsyncMock` for async Redis calls, minimize fixture data sizes
  - Target: `pytest tests/unit -q` completes in <5s total per service
- **Maximize pytest parallelism** — enable `pytest-xdist` across all services
  - Add `pytest-xdist` to each `pyproject.toml` dev deps
  - Configure `addopts = -n auto` in `[tool.pytest.ini_options]` per service
  - Ensure tests are stateless (no shared module-level state that breaks under `-n auto`)

## Testing Gaps Found

- **No unit tests for `_streams_fragment_html`** — `lol-pipeline-ui/src/lol_ui/main.py:836-865`. No test verifies the halt banner or priority count display.
- **No unit tests for `show_dlq` route** — `lol-pipeline-ui/src/lol_ui/main.py:920-961`. DLQ page rendering is untested.
- **No unit tests for `/stats/matches` route** — `lol-pipeline-ui/src/lol_ui/main.py:1018-1058`. Match history pagination, PUUID validation, pipeline batching all untested.
- **Analyzer `_derived` division edge cases** — `lol-pipeline-analyzer/src/lol_analyzer/main.py:32-46`. No test for extremely large stat values or negative values.
- **No test for `_tail_file` with very large files** — `lol-pipeline-ui/src/lol_ui/main.py:1132-1149`. Byte-seek logic untested with files larger than `n * _EST_BYTES_PER_LOG_LINE`.
- **`consume()` XAUTOCLAIM corrupt message path untested** — `lol-pipeline-common/src/lol_pipeline/streams.py:109-128`
  - No test for corrupt messages during XAUTOCLAIM (only PEL path tested).
- **Recovery `_consume_dlq` corrupt entry handling untested** — `lol-pipeline-recovery/src/lol_recovery/main.py:36-84`
- **Admin helper functions missing tests** — `_region_from_match_id`, `_resolve_puuid` error paths, `cmd_dlq_clear` with `all=False`
- **Crawler priority preservation not tested** — no test that priority is NOT cleared when `published > 0`
- **Delay-scheduler `_tick` OSError path untested** — only RedisError tested, not OSError
- (Phase 9) Shared test fixtures in conftest.py (FakeRedis, Config, envelope factory)
- (Phase 9) UI route integration tests with TestClient + fakeredis
- (Phase 9) E2E smoke test: seed -> crawl -> fetch -> parse -> analyze
- (Phase 9) Coverage enforcement `--cov-fail-under=80` in CI
- (Phase 9) Parallel contract test runner
- (Phase 9) Hypothesis property-based tests for fuzzing targets (see Fuzzing Targets section)
  - MessageEnvelope / DLQEnvelope deserialization
  - _raise_for_status HTTP edge cases
  - _format_stat_value / _badge UI helpers
  - Redis key construction safety
- (future) Playwright browser tests for UI regression
- (future) Load testing with locust

## UI/UX Improvements

- (Phase 9) CSS spinner/loading animation for match history lazy-load and streams auto-refresh
- (Phase 9) Global halt banner on ALL pages — currently only /stats and /streams check `system:halted`
- (Phase 9) Render skip-to-content `<a>` — `.skip-link` CSS exists but no element uses it
- (Phase 9) Wire up gauge/progressbar for stream depths — CSS defined but never rendered
- (Phase 9) DLQ page: inline replay button per entry (POST /dlq/replay/{id})
- (Phase 9) DLQ page: pagination — currently hard-capped at 50 entries
- (Phase 9) Home dashboard at `/` — system status cards, recent seeds, stream overview
- (Phase 9) Match detail page — click a match row for full participant data
- (Phase 9) Player comparison view — side-by-side stats
- (Phase 9) /players: server-side sort controls (name, region, date)
- (Phase 9) /stats: sparkline for win rate trend
- (Phase 9) Toast notifications for seed instead of page reload
- (future) Static CSS file with browser caching
- (future) WebSocket for /logs and /streams (replace polling)
- (future) Dark/light theme toggle
- (future) Export stats as CSV/JSON
- (future) Keyboard shortcuts (/ for search, r for refresh)

## Infrastructure

- (Phase 9) `docker-compose.prod.yml` — baked images, `--requirepass`, resource limits, log rotation
- (Phase 9) Redis `maxmemory 4gb` + `noeviction` policy in compose
- (Phase 9) Integration test CI job for IT-01 through IT-07 (testcontainers)
- (Phase 9) Trivy image scanning in CI
- (Phase 9) Docker build layer caching (`actions/cache`)
- (Phase 9) Prometheus + Redis Exporter + Grafana monitoring stack
- (Phase 9) `pip-audit` in CI for dependency scanning
- (future) Kubernetes Helm chart
- (future) GitHub Actions deploy workflow
- (future) Container registry with tagged releases

## Documentation

- (Phase 9) Discovery service README
- (Phase 9) CONTRIBUTING.md
- (Phase 9) CI workflow guide
- (Phase 9) Discovery architecture doc
- (Phase 9) Update design comparison doc (stale claims)
- (Phase 9) CHANGELOG.md
- (future) OpenAPI/Swagger for UI routes
- (future) Architecture Decision Records (ADRs)

## Developer Experience

- (Phase 9) Admin CLI `--json` flag
- (Phase 9) `just dev-ui` recipe with `--reload`
- (Phase 9) `just status` recipe (health + streams + DLQ + halted)
- (Phase 9) Shared `requirements-dev.txt` across services
- (future) VS Code devcontainer
- (future) Hot-module reload for all services
