# CLAUDE.md — Project Instructions

## Project

LoL Match Intelligence Pipeline — monorepo, Redis Streams, Python 3.14, Podman Compose (default) / Docker Compose.
See `ARCHITECTURE.md` for doc index. See `docs/standards/01-coding-standards.md` for lint/type config.
Platform: macOS. Container runtime: Podman (default). Switch with `RUNTIME=docker just <cmd>`.

## Directives

- **TDD (Red → Green → Refactor)**: Write failing test first. Never skip. Never change contracts to match broken output. Ask if ambiguous.
- **12-factor app** methodology
- **DRY** — Don't Repeat Yourself
- **Service isolation**: Services know only their own input/output contracts. No cross-service imports.
- **PACT contracts**: Schemas in `lol-pipeline-common/contracts/schemas/` are the DRY source. When modifying a service: (1) update schemas if shape changes, (2) update consumer pacts, (3) update provider contract tests. Contract tests must pass before merge.
- **Before compound tasks**: Update CLAUDE.md with a TODO list; remove when done.
- **Replies**: Direct, fewest words.

## Gotchas
- All complexity/lint thresholds configured in each service's `pyproject.toml` (see `docs/standards/`)

## Key Locations

| Path | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Doc index |
| `docs/standards/01-coding-standards.md` | Lint, type, complexity config |
| `docs/standards/03-testing-standards.md` | Test speed limits, timeout config, parallelism, agent batch strategy |
| `lol-pipeline-common/contracts/schemas/` | Canonical Pact v3 schemas |
| `lol-pipeline-*/pacts/` | Per-service consumer contracts |
| `tests/integration/` | 7 integration tests (IT-01 through IT-07, testcontainers) |

## TODO — Review Cycle 2

### Code Quality
- [ ] R5: Crawler only clears priority when `published == 0` — if matches stall in pipeline, priority never cleared

### DevOps
- [x] R7: Dockerfiles use `python:3.12-slim` but CI uses 3.14 — runtime/CI parity gap

---

## TODO — Orchestrator Cycle 2 (Review Iteration 1)

### Critical
- [x] B1: `raw:match:*` no TTL + `noeviction` policy = pipeline OOM at ~50K matches (`raw_store.py:126`)
- [x] B2: Discovery `_resolve_names` crashes on missing `gameName`/`tagLine` → infinite crash loop (`discovery/main.py:77`)

### High
- [x] B3: `_raise_for_status` crashes on non-integer `Retry-After` header (`riot_api.py:102`)
- [x] B4: Admin + UI DLQ pages crash on any corrupt DLQ entry (`admin/main.py:37`, `ui/main.py:913`)
- [x] B5: Admin `_make_replay_envelope` loses `enqueued_at` and `dlq_attempts` (`admin/main.py:41-48`)
- [x] B6: Crawler missing `NotFoundError` handler → unnecessary DLQ cycles (`crawler/main.py:66-118`)
- [x] B7: CI `docker-build` not gated on lint/typecheck/test
- [x] B8: Worker Dockerfiles missing `--start-period 60s` on HEALTHCHECK (9 services)
- [x] B9: CI `mypy --ignore-missing-imports` overrides `strict=true`

### Medium
- [x] B10: Recovery `_consume_dlq` lacks XAUTOCLAIM → stranded DLQ messages after worker crash
- [x] B11: Redis connection pool no socket timeout → hung connections block all coroutines
- [x] B12: Priority keys have no TTL → orphaned keys block Discovery if message lost (V7)
- [x] B13: In-memory retry counter lost on service restart → poison message loops forever (V3)
- [x] B14: `match:participants:{match_id}` sets are write-only (~90MB waste at 10K players)
- [x] B15: `ratelimit:limits:short/long` no TTL → stale limits persist after API key rotation

### Complexity Refactors
- [x] C1: `_crawl_player` — extract `_fetch_match_ids_paginated()` + `_handle_crawl_error()` helpers
- [x] C2: `show_stats` — extract `_resolve_and_cache_puuid()`, `_auto_seed_player()`, `_build_stats_response()`

## TODO — Orchestrator Cycle 3 (Review Iteration 2)

### Critical
- [x] I2-C1: `nack_to_dlq` never passes `dlq_attempts` → DLQ exhaustion mechanism completely broken + backoff always 5s (`streams.py:150`)
- [x] I2-C2: Discovery idle check only watches `stream:puuid`, not downstream streams → promotes players into already-backlogged pipeline (`discovery/main.py:37`)
- [x] I2-C3: `match:{match_id}` + `participant:{match_id}:{puuid}` hashes have no TTL → unbounded Redis memory growth (~1.5GB+ at 10K players)

### High
- [x] I2-H1: Seed publishes message THEN sets priority → Crawler can clear_priority before set_priority, permanently blocking Discovery (`seed/main.py:107-108`)
- [x] I2-H2: REJECTED — keep XADD-then-ZREM (see REJECTED.md I2-D1)
- [x] I2-H3: Stream MAXLEN=10,000 silently trims undelivered messages under sustained load (`streams.py:39`)
- [x] I2-H4: Crawler publishes all match IDs without backpressure check → spike floods `stream:match_id` past MAXLEN, silently drops messages (`crawler/main.py:88-96`)
- [x] I2-H5: Analyzer lock PEXPIRE inside MULTI/EXEC doesn't verify ownership → expired lock extended for wrong owner, causing concurrent stat double-counting (`analyzer/main.py:118`)
- [x] I2-H6: Admin `_make_replay_envelope` drops `priority` field (residual B5 bug) — `priority` not preserved when replaying DLQ (`admin/main.py:49`)
- [x] I2-H7: UI stats route calls Riot API without `wait_for_token()` → bypasses shared rate limiter, can trigger 429s for pipeline workers (`ui/main.py:571`)
- [x] I2-H8: Admin `cmd_reseed` calls `r.xadd()` directly with `priority="normal"` instead of `priority="high"` → reseeded player gets wrong priority vs original seed (`admin/main.py:247`)
- [x] I2-H9: Admin CLI no Redis error handling → raw traceback when Redis is down (`admin/main.py:348-361`)
- [x] I2-H10: `_ensure_group` suppresses ALL ResponseError, not just BUSYGROUP → permanent consumer failure silently swallowed (`streams.py:47-52`)
- [x] I2-H11: Parser HSET + SADD for match metadata not atomic → crash between them leaves match:status:parsed inconsistent (`parser/main.py:122-136`)
- [x] I2-H12: Discovery promotion writes (publish + ZREM + HSET) not atomic → crash causes duplicate promotion (`discovery/main.py:141-152`)
- [x] I2-H13: `recalc-priority` SCAN races with active pipeline → counter can diverge, permanently blocking Discovery (`admin/main.py:214-221`)
- [x] I2-H14: Admin DLQ page shows `source_stream` (always "stream:dlq") instead of `original_stream` (`admin/main.py:131`, `ui/main.py:939`)
- [x] I2-H15: Redis key injection from unbounded user input (game_name/tag_line) — no length limit on name_cache_key (`helpers.py:13`)

### Medium
- [x] I2-M1: `discover:players` sorted set grows without bound (cubic fan-out: players × matches × participants) → cap at configurable MAX_DISCOVER (e.g. 50K) via ZREMRANGEBYRANK (`parser/main.py:164-170`)
- [x] I2-M2: `_make_replay_envelope` also omits `priority` field (I2-H6 covers admin; this reminder covers the root in streams.py nack_to_dlq not preserving dlq_attempts — same fix needed)
- [x] I2-M3: RawStore synchronous disk I/O blocks async event loop → wrap `_exists_in_bundles`, `_search_bundles` in `asyncio.to_thread()` (`raw_store.py:139`)
- [x] I2-M4: Delay Scheduler failing members retry every 500ms forever with no backoff → add per-member failure counter + circuit breaker (`delay_scheduler/main.py:91-97`)
- [x] I2-M5: Discovery service continues polling loop when system:halted → wastes CPU; other services exit on halt (`discovery/main.py`)
- [x] I2-M6: DEVEX: pip install runs on every container restart (10-15s per service) → bake editable installs into Dockerfile, drop from compose command
- [x] I2-M7: Testing standards doc requires pytest-timeout but zero services implement it → add to all pyproject.toml dev deps

### Complexity Refactors (already in Cycle 2)
- [x] C1: `_crawl_player` — extract `_fetch_match_ids_paginated()` + `_handle_crawl_error()` helpers
- [x] C2: `show_stats` — extract `_resolve_and_cache_puuid()`, `_auto_seed_player()`, `_build_stats_response()`
