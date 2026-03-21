# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project uses semantic versioning.

---

## [Unreleased]

---

## [2.2.0] — 2026-03-21

Phase 20 INSIGHT: champion analytics, priority tiers, crawler improvements, UI redesign.

### Added

- Champion aggregate stats pipeline: per-champion win rate, KDA, gold, damage, and
  multikill tracking keyed by patch and role (`analyzer/main.py`, `parser/main.py`)
- Champion ban and matchup tracking in Parser for ranked solo queue
- `CHAMPION_STATS_TTL_SECONDS` env-configurable TTL for champion analytics keys
  (default 90 days)
- Admin `backfill-champions` command to reprocess existing parsed matches into champion stats
- Admin `reset-stats` command to wipe player stats and re-trigger analysis
- Admin `clear-priority` command to delete stalled priority keys
- Admin `delayed-list` and `delayed-flush` commands for delayed:messages visibility
- 12 new Redis key patterns for champion analytics, matchups, bans, player rank,
  and crawler state (documented in `docs/architecture/04-storage.md`)

### Fixed

- Admin `_UPDATE_CHAMPION_LUA` ZADD for `patch:list` now uses NX flag, matching the
  analyzer version (prevents overwriting patch scores)
- Admin `_UPDATE_CHAMPION_LUA` uses ZINCRBY for champion index, matching the analyzer
  version (was incorrectly using ZADD with absolute game count)
- DLQ archive list now displays raw fields (`failure_code`, `original_stream`,
  `failure_reason`) for corrupt entries instead of a bare `(corrupt entry)` message
- README test count updated to 1161 unit + 44 contract tests

### Changed

- `CHAMPION_STATS_TTL_SECONDS` in `constants.py` now reads from env var with 90-day default

---

## [2.1.0] — 2026-03-21

Phase 19 FINALIZE: replay tests, admin whitelist tests, doc fixes.

### Added

- Tests for atomic DLQ replay (`replay_from_dlq` Lua script) covering race conditions
- Admin whitelist validation tests for `_VALID_REPLAY_STREAMS`
- Documentation fixes across architecture and service docs

---

## [2.0.0] — 2026-03-21

Phase 18 INTEGRITY: atomic DLQ replay, constants DRY, tests, docs.

### Added

- `replay_from_dlq` Lua script in `streams.py` for atomic XADD + XDEL replay from DLQ
  (eliminates crash-induced duplicate replays)
- `VALID_REPLAY_STREAMS` constant in `constants.py` as the DRY source for replay whitelist
  (admin and UI both import from `constants.py`)

### Changed

- Admin and UI DLQ replay now use `replay_from_dlq()` instead of separate XADD + XDEL calls
- `_VALID_REPLAY_STREAMS` in admin and UI replaced with import from `constants.py`

---

## [1.9.0] — 2026-03-20

Phase 17 RESOLUTION: delay scheduler starvation, DLQ whitelist, docs, perf.

### Fixed

- Delay Scheduler starvation: failing members no longer monopolize the poll loop;
  per-member backoff prevents hot-loop retries on persistent failures
- DLQ replay whitelist (`_VALID_REPLAY_STREAMS`) added to admin CLI and UI to reject
  replay to arbitrary Redis streams
- UI `_build_stats_response` pipelined (3 sequential reads collapsed into 1 pipeline call)

### Added

- `docs/services/delay-scheduler.md` service documentation
- Performance improvements to UI stats rendering

---

## [1.8.0] — 2026-03-20

Phase 16 HORIZON: Discovery idle-check simplification, crawler atomicity, UI hardening.

### Fixed

- **V16-3**: Discovery `_is_idle()` Layer 1 XLEN check was always true at normal scale and
  added a false sense of backpressure; removed — idle check now uses only XINFO GROUPS
  pending/lag per stream (`discovery/main.py`)
- **S16-1**: DLQ replay in admin CLI and UI accepted arbitrary `original_stream` values from
  the DLQ envelope without validation; added `_VALID_REPLAY_STREAMS` whitelist to reject
  replay to unknown streams (`admin/main.py`, `ui/main.py`)
- **S16-2**: Admin `cmd_dlq_replay` called `r.xadd()` directly with hardcoded `maxlen=10_000`,
  bypassing the per-stream MAXLEN policy; replaced with `publish()` (`admin/main.py`)
- **P16-PERF-1**: Crawler `_fetch_match_ids_paginated` made one Redis write per match ID;
  batched into a single `r.pipeline()` call
- **P16-PERF-2**: `_build_stats_response` made 3 sequential Redis reads with no data
  dependencies; collapsed into one `r.pipeline(transaction=False)` call (`ui/main.py`)
- Streams page halt banner now uses shared `_HALT_BANNER` constant with recovery instructions
  instead of an abbreviated inline message (`ui/main.py`)
- Discovery docs (`docs/services/discovery.md`) updated: removed stale Layer 1 / XLEN
  section, `MAX_STREAM_BACKLOG` config row, `system:priority_count` key references, and
  resolved Known Limitations (I2-C2, I2-H3)

---

## [1.7.0] — 2026-03-20

Phase 15 HORIZON: TTL constants, TimeoutError handling, UX fixes, DX hardening.

### Fixed

- Introduced shared TTL constants module to eliminate magic numbers scattered across services
- Added `asyncio.TimeoutError` handling in Crawler and Fetcher to prevent indefinite hangs
  on slow Riot API responses
- Halt banner on all UI pages now includes actionable recovery instructions
- DX: pre-push hook verifies mypy passes on changed services before push

---

## [1.6.0] — 2026-03-20

Phase 14 HORIZON: rate limiter hardening, atomicity fixes, pipeline optimizations (980 tests).

### Fixed

- Rate limiter `wait_for_token()` now checks `system:halted` flag before sleeping to avoid
  blocking workers when the pipeline is stopped
- Parser HSET + SADD for match metadata wrapped in `MULTI/EXEC` for atomic write
- `_incr_retry` batches INCR + EXPIRE into a single pipeline call to prevent partial
  retry-counter state
- UI stats route now calls `wait_for_token()` before any Riot API calls

### Changed

- `stream:analyze` upgraded to per-stream `ANALYZE_STREAM_MAXLEN` override to accommodate
  bursty analyzer workloads without silent trimming

---

## [1.5.0] — 2026-03-20

Phase 13 SUMMIT: cache invalidation, TTLs, parser pipeline, UX fixes.

### Fixed

- `ratelimit:limits:short` and `ratelimit:limits:long` now carry a TTL so stale rate limit
  state clears automatically after API key rotation
- Parser now batches participant HSET writes into a pipeline call, reducing round-trips for
  large match rosters
- `name_cache_key` length capped to prevent Redis key injection from long `game_name`/
  `tag_line` values (I2-H15 residual hardening)
- UI player list page handles missing `game_name`/`tag_line` gracefully instead of
  crashing on incomplete player hashes

---

## [1.4.0] — 2026-03-20

Phase 12 ZENITH: champion icons, Content Security Policy, async log IO, TTLs, ARIA improvements.

### Added

- Champion icon images on the stats page (fetched from CDragon CDN)
- Content Security Policy header via FastAPI middleware
- ARIA labels and roles on interactive UI elements for screen-reader accessibility

### Fixed

- Log IO moved to `asyncio.to_thread()` so synchronous file writes no longer block the
  async event loop
- `match:{match_id}` and `participant:{match_id}:{puuid}` hashes now carry a 30-day TTL
  (I2-C3 residual: applied to parser output)

---

## [1.3.0] — 2026-03-20

Phase 11 APEX: hardening, responsive design, DX improvements, and observability fixes.

### Added

- Responsive mobile layout for all UI pages using CSS grid breakpoints
- `just logs <service>` recipe for tailing a single service log file
- Per-service `pytest-timeout` configuration (10 s per test) enforced in CI

### Fixed

- Structured JSON logs now include `service` field for easier log aggregation
- Admin DLQ page no longer crashes on corrupt DLQ entries (B4 residual)
- `system:halted` check added to Delay Scheduler main loop

---

## [1.2.0] — 2026-03-20

Phase 10 "ILLUMINATE": rate limiter improvements, mobile UX, admin CLI extensions, security.

### Added

- Admin `system-resume` command to clear `system:halted` flag and restart pipeline workers
- Admin `dlq-list` pagination for large DLQ backlogs
- Mobile-friendly navigation menu with hamburger toggle

### Fixed

- Rate limiter short/long window sliding correctly without counter drift
- Admin CLI now wraps all Redis operations in try/except and prints clean errors (B11)
- UI XSS: player names and Riot IDs HTML-escaped in all rendered templates

---

## [1.1.0] — 2026-03-20

Architecture hardening, UI Phase 9 design polish, integration tests IT-08 through IT-12.

### Added

- Integration tests IT-08 through IT-12: Discovery fan-out, DLQ circuit-breaker, rate-limit
  window sliding, parser atomicity, and priority gate scenarios (testcontainers)
- UI Phase 9: consistent card layout, colour-coded status badges, and stream depth sparklines
- `just admin` recipe family wrapping all `lol-admin` CLI commands

### Fixed

- Crawler backpressure: checks `stream:match_id` depth before publishing a new batch to
  prevent flooding the stream past MAXLEN (I2-H4)
- Seed service sets priority before publishing to `stream:puuid` to eliminate the
  clear-before-set race (I2-H1)
- `_ensure_group` now re-raises non-BUSYGROUP `ResponseError` (I2-H10)

---

## [1.0.0] — 2026-03-19

Orchestrator cycles 2 and 3: 30 bug fixes spanning critical correctness gaps,
security hardening, DLQ integrity, and CI/CD gating.

### Fixed — Critical

- **B1**: `raw:match:*` keys had no TTL under `noeviction` policy, causing pipeline OOM
  at ~50K matches (`raw_store.py`)
- **B2**: Discovery `_resolve_names` crashed on missing `gameName`/`tagLine` for deleted
  or banned accounts, creating an infinite restart loop (`discovery/main.py`)
- **I2-C1**: `nack_to_dlq` never forwarded `dlq_attempts` to the DLQ envelope, completely
  breaking the DLQ exhaustion mechanism and pinning retry backoff to 5 s (`streams.py`)
- **I2-C2**: Discovery idle check only watched `stream:puuid`; now checks all four pipeline
  streams (`stream:puuid`, `stream:match_id`, `stream:parse`, `stream:analyze`) to avoid
  promoting players into an already-backlogged pipeline (`discovery/main.py`)
- **I2-C3**: `match:{match_id}` and `participant:{match_id}:{puuid}` hashes had no TTL,
  causing unbounded Redis memory growth (~1.5 GB+ at 10K players) (`parser/main.py`)

### Fixed — High

- **B3**: `_raise_for_status` crashed on non-integer `Retry-After` headers (HTTP-date
  format); now parses both int-seconds and HTTP-date forms (`riot_api.py`)
- **B4**: Admin and UI DLQ pages crashed on any corrupt DLQ entry; both now handle
  deserialization errors gracefully (`admin/main.py`, `ui/main.py`)
- **B5 / I2-H6 / I2-M2**: `_make_replay_envelope` lost `enqueued_at`, `dlq_attempts`,
  and `priority` fields when replaying from the DLQ; all three fields are now preserved
  (`admin/main.py`, `streams.py`)
- **B6**: Crawler was missing a `NotFoundError` (404) handler, sending non-existent
  accounts through unnecessary DLQ cycles (`crawler/main.py`)
- **B7**: CI `docker-build` job was not gated on lint, typecheck, or test; it now
  `needs: [lint, typecheck, test, contract]` (`.github/workflows/ci.yml`)
- **B8**: Nine worker Dockerfiles were missing `--start-period 60s` on HEALTHCHECK,
  causing premature container restarts during startup
- **B9**: CI mypy was called with `--ignore-missing-imports`, silently overriding the
  `strict = true` setting in each service's `pyproject.toml` (`.github/workflows/ci.yml`)
- **I2-H1**: Seed published a message then set priority; Crawler could clear priority
  before `set_priority` completed, permanently blocking Discovery. Fixed to set priority
  first (`seed/main.py`)
- **I2-H5**: Analyzer lock `PEXPIRE` inside `MULTI/EXEC` did not verify lock ownership,
  allowing an expired lock to be extended for the wrong owner and causing concurrent stat
  double-counting (`analyzer/main.py`)
- **I2-H7**: UI stats route called the Riot API without `wait_for_token()`, bypassing
  the shared rate limiter and causing 429s for pipeline workers (`ui/main.py`)
- **I2-H8**: Admin `cmd_reseed` used `priority="normal"` instead of `priority="high"`,
  giving reseeded players the wrong queue priority (`admin/main.py`)
- **I2-H9**: Admin CLI emitted raw tracebacks when Redis was unavailable; now catches
  `RedisError` and prints a clean error message (`admin/main.py`)
- **I2-H10**: `_ensure_group` suppressed all `ResponseError`, not just `BUSYGROUP`,
  silently swallowing permanent consumer group failures (`streams.py`)
- **I2-H11**: Parser `HSET` + `SADD` for match metadata were not atomic; a crash between
  them left `match:status:parsed` inconsistent. Now wrapped in `MULTI/EXEC` (`parser/main.py`)
- **I2-H12**: Discovery promotion (publish + ZREM + HSET) was not atomic; fixed using
  `XADD`-first ordering plus `MULTI/EXEC` pipeline for the Redis state writes, making
  the operation at-least-once safe (`discovery/main.py`)
- **I2-H13**: Admin `recalc-priority` used `SCAN` that raced with the active pipeline,
  causing the counter to diverge and permanently blocking Discovery (`admin/main.py`)
- **I2-H14**: Admin and UI DLQ views showed `source_stream` (always `"stream:dlq"`)
  instead of `original_stream` (`admin/main.py`, `ui/main.py`)
- **I2-H15**: Redis key injection was possible from unbounded user-supplied `game_name` /
  `tag_line` input; input length is now capped (`helpers.py`)

### Fixed — Medium

- **B10 / B15 (renamed)**: Recovery `_consume_dlq` now uses `XAUTOCLAIM` to reclaim
  stranded DLQ messages after worker crashes
- **B11**: Redis connection pool had no socket timeout; connections can no longer hang
  indefinitely and block all coroutines
- **B12**: Priority keys now have TTL (default 86400 s), preventing orphaned keys from
  blocking Discovery when a message is lost in transit
- **B13**: In-memory retry counter was lost on service restart, allowing poison messages
  to loop forever; counter is now tracked in Redis
- **B14**: `match:participants:{match_id}` write-only sets (~90 MB waste at 10K players)
  removed; downstream consumers use `player:matches:{puuid}` instead
- **I2-M1**: `discover:players` sorted set is now capped via `ZREMRANGEBYRANK` to prevent
  cubic fan-out growth (players × matches × participants) (`parser/main.py`)
- **I2-M3**: RawStore synchronous disk I/O now runs in `asyncio.to_thread()` to avoid
  blocking the async event loop (`raw_store.py`)
- **I2-M4**: Delay Scheduler failing members now have a per-member failure counter with
  circuit breaker instead of retrying every 500 ms forever (`delay_scheduler/main.py`)
- **I2-M5**: Discovery polling loop now exits immediately when `system:halted` is set,
  consistent with all other services (`discovery/main.py`)
- **I2-M6**: Editable package installs are now baked into service Dockerfiles; startup
  no longer runs `pip install` (10–15 s per service eliminated)
- **I2-M7**: `pytest-timeout` added to all service `pyproject.toml` dev dependencies,
  enforcing the 10 s per-test limit from the testing standards doc

### Added

- CI security audit job using `pip-audit` scans all service packages for known CVEs
- XAUTOCLAIM support in `streams.consume()` for dead-worker message recovery
- `MATCH_ID_STREAM_MAXLEN` and `ANALYZE_STREAM_MAXLEN` per-stream overrides to allow
  bursty streams to grow without silent trimming
- `players:all` sorted set populated by Seed, UI auto-seed, and Discovery promote,
  replacing slow O(N) SCAN on the `/players` endpoint
- Pre-push git hook installs mypy to ensure typecheck passes before push

### Changed

- `_ensure_group` now re-raises any `ResponseError` that is not `BUSYGROUP`
- `nack_to_dlq` signature unchanged; internal DLQEnvelope construction now correctly
  forwards `enqueued_at`, `dlq_attempts`, and `priority` from the source envelope
- Stream `MAXLEN` policy: `stream:match_id` is now unbounded (`maxlen=None`) to prevent
  silent message loss under crawler bursts

---

## [0.9.0] — 2026-02-28

Phase 7 "IRONCLAD" hardening and Phase 8 "FACELIFT" UI overhaul, plus the full
initial test coverage expansion to 560 unit tests + 61 contract tests.

### Added

- Phase 8 FACELIFT: complete UI overhaul with dark theme, design system, responsive
  layout, DLQ browser, player search, wide layout, and favicon
- Phase 7 IRONCLAD: security hardening, code quality enforcement, and weighted priority
  queue for seeded players
- 7 integration tests (IT-01 through IT-07) using testcontainers and fakeredis covering
  happy path, idempotency, 429 recovery, crash redelivery, system halt propagation,
  concurrent worker safety, and rate-limit enforcement
- `discover:players` sorted set and Discovery service for automatic co-player fan-out
- Priority queue (`player:priority:{puuid}` + `system:priority_count`) to pause Discovery
  while seeded players are in-flight
- Admin `recalc-priority` command to rebuild `system:priority_count` from live Redis state
- Admin `reseed` command to re-publish a player directly to `stream:puuid`
- `just status` recipe: dashboard of container health, stream depths, DLQ depth, and
  recent log lines
- `just coverage` recipe for per-service coverage reports
- `just test-svc <name>` recipe for targeting a single service
- `just fix` recipe for auto-fixing ruff lint issues
- Delay Scheduler service for timed retry of DLQ messages via `delayed:messages` sorted set
- RawStore JSONL+zstd bundle format replacing per-match individual JSON files
- `just consolidate` recipe to migrate old individual JSON files to JSONL bundles
- `ratelimit:limits:short/long` TTL to prevent stale rate limit state after API key rotation
- XSS protection in all UI-rendered player name/Riot ID output

### Changed

- Container runtime defaults to Podman; Docker still supported via `RUNTIME=docker`
- All worker Dockerfiles now use multi-stage builds with editable package installs
- mypy `strict = true` enforced across all 11 services
- ruff configured with bandit (`S`), annotations (`ANN`), and pylint (`PLR`) rulesets
- `STREAM_ACK_TIMEOUT` replaces hard-coded 60 s PEL claim timeout
- Fetcher and Parser both mount `MATCH_DATA_DIR` for write-through disk persistence

### Fixed

- Pre-commit hooks install correctly on both macOS (Podman) and Linux (Docker)
- CI typecheck now installs all service dependencies before running mypy
- Dev compose `user: root` added for pip install at startup
- Flaky delay-scheduler test fixed (timing edge + test isolation)

---

## [0.8.x] — Phase 8 Sprints (UI Overhaul)

- Sprint 1: Dark theme, design system, responsive layout, XSS fix (465 → 497 tests)
- Sprint 2+3: Page redesign and interactive features (497 → 540 tests)
- Sprint 4: Polish — favicon, DLQ browser, search, wide layout (540 → 546 tests)

---

## [0.7.x] — Phase 7 (IRONCLAD Security & Quality)

- P0 priority fixes: security hardening, code complexity reduction, weighted queue
- Full pre-commit hook integration

---

## [0.6.x] — Phase 6 (Operations)

- Admin CLI with stream management, DLQ replay, stats, and player management
- Justfile with full lifecycle recipes (setup, build, run, seed, admin, streams, status)
- Integration test scaffolding (IT-01 through IT-07)

---

## [0.5.x] — Phase 5 (Resilience Layer)

- Recovery service consuming `stream:dlq` and routing to `delayed:messages`
- Delay Scheduler polling `delayed:messages` and dispatching to source streams
- `system:halted` propagation: all services exit cleanly on 403 auth failure

---

## [0.4.x] — Phase 4 (Processing Pipeline)

- Parser service: raw match JSON → structured Redis hashes + participant sets
- Analyzer service: per-player stat aggregation with distributed lock

---

## [0.3.x] — Phase 3 (Ingestion Pipeline)

- Seed service: PUUID lookup → `stream:puuid`
- Crawler service: match ID discovery → `stream:match_id`
- Fetcher service: Riot API fetch → RawStore + `stream:parse`

---

## [0.2.x] — Phase 2 (Shared Foundation)

- `lol-pipeline-common`: config, logging, redis_client, models, streams, rate_limiter,
  raw_store, riot_api
- Pact v3 contract schemas in `lol-pipeline-common/contracts/schemas/`

---

## [0.1.x] — Phase 1 (Foundation)

- Monorepo structure with 11 service packages
- CI pipeline: lint, typecheck, unit tests, contract tests, docker-build
- Pre-commit hooks: ruff, mypy
- `Justfile` with setup/build/run/test/lint/typecheck recipes
- `docker-compose.yml` with Redis + all worker services

[Unreleased]: https://github.com/abhiregmi/LoL-Crawler/compare/v2.1.0...HEAD
[2.1.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v2.0.0...v2.1.0
[2.0.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.9.0...v2.0.0
[1.9.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.8.0...v1.9.0
[1.8.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.7.0...v1.8.0
[1.7.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.6.0...v1.7.0
[1.6.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.5.0...v1.6.0
[1.5.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.4.0...v1.5.0
[1.4.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.3.0...v1.4.0
[1.3.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.2.0...v1.3.0
[1.2.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.1.0...v1.2.0
[1.1.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v0.9.0...v1.0.0
[0.9.0]: https://github.com/abhiregmi/LoL-Crawler/compare/v0.8.0...v0.9.0
