# TODO — Improvement Proposals (All 21 Agents)

Phase 7 "IRONCLAD" and Phase 8 "FACELIFT" are **complete**.
546 unit tests + 61 contract tests. 21 agentic coding agents.

---

## Immediate Code Fixes

These are concrete bugs and code-quality issues discovered by reading the source.

- **DLQ round-trip loses priority** — `lol-pipeline-recovery/src/lol_recovery/main.py:110-119`
  - `_requeue_delayed` constructs a new `MessageEnvelope` but does not copy `dlq.priority` to the envelope. High-priority messages lose their priority status after a DLQ retry.
  - Fix: add `priority=dlq.priority` to the `MessageEnvelope(...)` constructor call at line 110.

- **Recovery resets attempts to 0 on requeue** — `lol-pipeline-recovery/src/lol_recovery/main.py:116`
  - `attempts=0` unconditionally resets the message's attempt counter, losing the history of how many times it was tried before hitting the DLQ.
  - Fix: preserve `attempts=dlq.attempts` so the original attempt count carries through.

- **Recovery has no handler for `handler_crash` failure code** — `lol-pipeline-recovery/src/lol_recovery/main.py:185-190`
  - `service.py:54` sends messages to DLQ with `failure_code="handler_crash"`, but Recovery's `_HANDLERS` dict has no entry for it. These messages are immediately archived without any retry attempt.
  - Fix: add `"handler_crash": _handle_transient` to the `_HANDLERS` dict so they get exponential backoff retries.

- **Analyzer pipeline not awaited as context manager** — `lol-pipeline-analyzer/src/lol_analyzer/main.py:94`
  - `pipe = r.pipeline(transaction=True)` without `async with`. If `pipe.execute()` raises, the pipeline connection is leaked.
  - Fix: use `async with r.pipeline(transaction=True) as pipe:` matching the pattern used in Parser and UI.

- **`_format_stat_value` does not handle NaN/Inf** — `lol-pipeline-ui/src/lol_ui/main.py:334-349`
  - `float(value)` succeeds for `"nan"`, `"inf"`, `"-inf"` but produces meaningless display strings like `"nan%"` or `"inf%"`.
  - Fix: after `float()` conversion, check `math.isfinite()` and return a fallback like `"N/A"` for non-finite values.

- **Unreachable `return None` after `with` block** — `lol-pipeline-common/src/lol_pipeline/raw_store.py:107`
  - `_search_compressed_bundle` has a `return None` after the `with` statement that is dead code (the `with` block always returns via `_find_in_lines`).
  - Fix: remove the unreachable `return None`.

- **Delay Scheduler XADD+ZREM not atomic** — `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py:50-51`
  - If the process crashes between `XADD` and `ZREM`, the delayed message is duplicated (delivered to the stream but remains in the ZSET for re-delivery).
  - Fix: use a Lua script that performs both `XADD` and `ZREM` atomically.

- **`publish()` missing MAXLEN** — `lol-pipeline-common/src/lol_pipeline/streams.py:27`
  - `r.xadd(stream, fields)` has no `maxlen` parameter, so streams grow unbounded indefinitely.
  - Fix: add `maxlen` parameter with approximate trimming: `r.xadd(stream, fields, maxlen=10000, approximate=True)`.

- **`_badge` XSS risk with caller-supplied text** — `lol-pipeline-ui/src/lol_ui/main.py:365-373`
  - The `text` parameter is documented as "raw HTML (caller must escape user data)" but the contract is fragile. Multiple callers pass computed strings directly.
  - Fix: auto-escape `text` inside `_badge()` and use a separate `_badge_html()` for cases where raw HTML entities are intentional (e.g., `&#10003;`).

- **DRY: `_name_cache_key` duplicated in 3 places** — `seed/main.py:39`, `admin/main.py:55`, `ui/main.py:596`
  - All define `f"player:name:{game_name.lower()}#{tag_line.lower()}"`. Extract to `lol_pipeline.constants` or a shared helper.

- **DRY: `_resolve_puuid` duplicated in 3 places** — `seed/main.py:43`, `admin/main.py:59`, `ui/main.py:596-636`
  - All implement PUUID resolution with cache check + Riot API fallback. Extract to `lol_pipeline.resolve`.
  - Subtask 1: Create `lol_pipeline/resolve.py` with unified `resolve_puuid()`
  - Subtask 2: Refactor seed, admin, UI to use it; update tests

- **DRY: `system:halted` check duplicated in every handler** — `crawler/main.py:34`, `fetcher/main.py:35`, `parser/main.py:84`, `analyzer/main.py:57`
  - Move halt-check into `service.py:run_consumer` before dispatching handler.

- **DRY: seeded_at hset pattern duplicated** — `seed/main.py:161-169`, `ui/main.py:678-686`, `discovery/main.py:137-145`
  - All write identical `player:{puuid}` mappings. Extract to shared function.

- **No TTL on `player:name:` cache keys** — `seed/main.py:64`, `admin/main.py:77`, `ui/main.py:636`
  - `r.set(cache_key, puuid)` with no expiry. Name changes cached forever. Add `ex=86400` (24h).

- **Discovery uses two separate HGET calls** — `discovery/main.py:72-73`
  - Two round-trips (`hget game_name`, `hget tag_line`). Use `hmget(f"player:{puuid}", ["game_name", "tag_line"])` for one round-trip.

- **Recovery has no SIGTERM via asyncio** — `lol-pipeline-recovery/src/lol_recovery/main.py:230-247`
  - No graceful shutdown handler (unlike discovery/delay-scheduler). Add `asyncio.Event` shutdown flag with `loop.add_signal_handler`.

- **`_crawl_player` complexity** — `crawler/main.py:26` has `noqa: C901, PLR0915`
  - Extract API pagination into `_fetch_match_ids_paginated()` helper.
  - Subtask 1: Extract pagination loop to helper function
  - Subtask 2: Extract error handling to `_handle_crawl_error()`

- **`show_stats` complexity** — `ui/main.py:577` has `noqa: PLR0911, C901` (11 return paths)
  - Extract `_resolve_and_cache_puuid()`, `_auto_seed_player()`, `_build_stats_response()`.
  - Subtask 1: Extract resolve+cache into helper
  - Subtask 2: Extract auto-seed logic into helper
  - Subtask 3: Extract stats rendering into helper

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

- **`_load_lcu_data`** — `lol-pipeline-ui/src/lol_ui/main.py:46-65`
  - Fuzz with malformed JSONL, binary files, symlinks, very large files
  - Verify: returns a dict, skips malformed lines, never crashes

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

## Testing Gaps Found

- **No unit tests for `_streams_fragment_html`** — `lol-pipeline-ui/src/lol_ui/main.py:836-865`. No test verifies the halt banner or priority count display.
- **No unit tests for `show_dlq` route** — `lol-pipeline-ui/src/lol_ui/main.py:920-961`. DLQ page rendering is untested.
- **No unit tests for `/stats/matches` route** — `lol-pipeline-ui/src/lol_ui/main.py:1018-1058`. Match history pagination, PUUID validation, pipeline batching all untested.
- **No unit tests for LCU `_collect_with_auth_retry`** — `lcu/main.py:150-165`. Retry logic for stale lockfile credentials is untested.
- **No unit tests for LCU `_build_participants`** — `lcu/main.py:68-85`. Edge cases like missing participantIdentities untested.
- **Analyzer `_derived` division edge cases** — `lol-pipeline-analyzer/src/lol_analyzer/main.py:32-46`. No test for extremely large stat values or negative values.
- **No test for `_tail_file` with very large files** — `lol-pipeline-ui/src/lol_ui/main.py:1132-1149`. Byte-seek logic untested with files larger than `n * _EST_BYTES_PER_LOG_LINE`.
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

- (Phase 9) Discovery + LCU service READMEs
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
