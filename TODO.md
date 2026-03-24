# TODO — Open Work Items

---

## Critical

### CR-2: Fetcher drops priority on outbound parse envelopes

**File:** `lol-pipeline-fetcher/src/lol_fetcher/main.py` lines 49-54, 131-136

Fetcher creates outbound `MessageEnvelope` for `stream:parse` without propagating
`envelope.priority` from the input message. Same class of bug as F1 (parser) which was
already fixed. The fetcher was missed.

**Fix:** Add `priority=envelope.priority` to both `MessageEnvelope(...)` calls in `_fetch_match`.

---

### CR-3: Crawler XADD pipeline omits maxlen trimming

**File:** `lol-pipeline-crawler/src/lol_crawler/main.py` line 136

Crawler publishes match IDs via pipelined `pipe.xadd()` that omits `maxlen`. The standard
`publish()` applies `MATCH_ID_STREAM_MAXLEN = 500_000` but the crawler bypasses `publish()`
for pipeline efficiency. Stream grows unbounded if fetcher falls behind.

**Fix:** Add `maxlen=MATCH_ID_STREAM_MAXLEN` and `approximate=True` to the `pipe.xadd()` call.

---

### CR-5: Parser ban/matchup idempotency guard has TOCTOU race

**File:** `lol-pipeline-parser/src/lol_parser/main.py` lines 373, 392, 401-404

SISMEMBER check and SADD are not atomic. Two parser instances processing the same match_id
concurrently can both see `already_parsed = False` and double-count bans/matchups via HINCRBY.

**Fix:** Move SISMEMBER into the same MULTI/EXEC or use a Lua script for atomic check-and-set.

---

### TTL-1: `priority:active` SET has no TTL and no cap

**File:** `lol-pipeline-common/src/lol_pipeline/priority.py` lines 17, 60, 66

When `player:priority:{puuid}` TTL expires naturally (without explicit `clear_priority()`),
the PUUID remains orphaned in `priority:active` permanently. False-positive blocks Discovery
entirely.

**Fix:** Periodic cleanup in Discovery's `_promote_batch` — scan `priority:active` members
and SREM any whose `player:priority:{puuid}` key has expired.

---

### DRY-1: `_make_replay_envelope` duplicated in 3 locations

**Files:** `admin/main.py`, `ui/main.py`, `recovery/main.py`

Identical 13-line function in admin and UI; same logic inlined in recovery.

**Fix:** Move `make_replay_envelope` to `lol_pipeline.models` or `lol_pipeline.streams`.

---

### E2: Config validation crash gives pydantic traceback, not actionable hint

**Surface:** All 9 services on first run

Missing `RIOT_API_KEY` or `REDIS_URL` produces a raw pydantic `ValidationError` traceback.
None of the 9 entry points catch it.

**Fix:** Catch `pydantic.ValidationError` in each entry point, print actionable message
referencing `.env.example`, exit with code 1.

---

## High

### CR-4: Analyzer champion stats lost on lock expiry mid-processing

**File:** `lol-pipeline-analyzer/src/lol_analyzer/main.py` lines 304-318

If lock expires between `_process_matches` and `_update_champion_stats`, cursor advances
past matches whose champion stats are never written. Permanent data loss for aggregate stats.

**Fix:** Increase `analyzer_lock_ttl_seconds` and add lock refresh between the two phases.

---

### CR-1 (Complexity Review): Analyzer `_update_champion_stats` sequential EVAL per match

**File:** `lol-pipeline-analyzer/src/lol_analyzer/main.py` lines 208-253

O(M) Redis round-trips where M = new matches. Each `r.eval(_UPDATE_CHAMPION_LUA, ...)` is
independent and could be batched.

**Fix:** Use `r.pipeline(transaction=False)` to batch all EVAL calls into a single round-trip.

---

### CR-2 (Complexity Review): RawStore.set calls `_exists_in_bundles` on every new write

**File:** `lol-pipeline-common/src/lol_pipeline/raw_store.py` line 144

After Redis SET NX succeeds, scans ALL JSONL bundle files for the match ID. O(B * L) per
write, worsens with data age.

**Fix:** Check only the current month's bundle, or maintain an in-memory bloom filter.

---

### CR-5 (Complexity Review): `has_priority_players` uses SCAN on every streams page refresh

**File:** `lol-pipeline-common/src/lol_pipeline/priority.py` lines 66-78
**Called from:** `lol-pipeline-ui/src/lol_ui/main.py` (every 5s per browser tab)

O(N) SCAN over entire keyspace. Worsens as keyspace grows.

**Fix:** Replace SCAN with `SCARD("priority:active") > 0` — O(1). Reconcile the set
periodically against actual keys.

---

### CR-9 (Complexity Review): Admin `_dlq_entries` loads entire DLQ into memory

**File:** `lol-pipeline-admin/src/lol_admin/main.py` line 60

`r.xrange(_STREAM_DLQ, "-", "+")` with no count limit. DLQ max is 50K entries.

**Fix:** Paginate with cursor-based XRANGE for `cmd_dlq_list`; use `XTRIM MAXLEN 0` for clear.

---

### ASYNC-2: Blocking disk write on the event loop in `RawStore.set()`

**File:** `lol-pipeline-common/src/lol_pipeline/raw_store.py` lines 161-164

`mkdir()`, `open()`, and `write()` are synchronous OS operations blocking the event loop.

**Fix:** Extract disk write into a sync helper and delegate to `asyncio.to_thread`.

---

### DRY-2: `_maxlen_for_stream` duplicated across 3 modules

**Files:** `streams.py`, `delay_scheduler/main.py`, `admin/main.py`

Three implementations of the same per-stream MAXLEN policy lookup.

**Fix:** Export a single `maxlen_for_stream(stream: str) -> int` from `lol_pipeline.streams`.

---

### DRY-3: Player registration pattern duplicated across seed, discovery, UI

**Files:** `seed/main.py`, `discovery/main.py`, `ui/main.py`

Same 4-step HSET + EXPIRE + ZADD + ZREMRANGEBYRANK sequence in 3 services.

**Fix:** Extract `register_player()` into `lol_pipeline.helpers`.

---

### DRY-5: `is_system_halted()` helper exists but raw `r.get("system:halted")` used in ~10 locations

**Files:** `service.py`, `recovery/main.py`, `discovery/main.py`, `seed/main.py`, `ui/main.py`

**Fix:** Replace all raw `r.get("system:halted")` calls with `is_system_halted(r)`.

---

### DRY-6: Riot API error handling pattern duplicated in crawler and fetcher

**Files:** `crawler/main.py` (~25 lines), `fetcher/main.py` (~25 lines)

Same 4-branch routing (404, 403, 429, 5xx) with only `failed_by` label differing.

**Fix:** Extract `handle_riot_api_error()` into `lol_pipeline.helpers`.

---

### Contract Drift

| ID | Issue | Fix | Status |
|----|-------|-----|--------|
| D1 | `correlation_id` missing from all 6 pact files | Add `"correlation_id": ""` + type matcher to every pact | Done |
| D2 | `dlq_attempts` missing from all 6 MessageEnvelope pacts | Add `"dlq_attempts": 0` + integer matcher to every pact | Done |
| D3 | Provider contract tests validate partial documents (7/10 fields) | Use full `to_redis_fields()` round-trip in provider tests | Done |

---

## Medium

### TTL-2: `match:status:parsed` SET grows unbounded (TTL resets on every write)

**File:** `lol-pipeline-parser/src/lol_parser/main.py` lines 433-434

EXPIRE resets on every SADD. Under continuous operation the key never expires. ~1.7M members/day
at 20 matches/s.

**Fix:** Only set EXPIRE when no TTL exists (same pattern as `seen:matches` F5 fix).

---

### TTL-3: `match:status:failed` SET has the same TTL-reset problem

**File:** `lol-pipeline-recovery/src/lol_recovery/main.py` lines 63-64

Same pattern as TTL-2. Low volume (~100/day) but should be consistent.

---

### TTL-4: `player:rank:history:{puuid}` ZSET has no member cap

**File:** `lol-pipeline-crawler/src/lol_crawler/main.py` lines 304-306

Active players accumulate unbounded rank snapshots. High-activity player: ~1440 entries/month.

**Fix:** Add `ZREMRANGEBYRANK` after ZADD to cap at ~500 entries.

---

### Doc Accuracy (Think Round 4)

| # | Doc | Issue |
|---|-----|-------|
| 1 | `03-streams.md` | `correlation_id` and `dlq_attempts` missing from envelope table |
| 2 | `03-streams.md` | No maxlen values in stream registry table |
| 3 | `04-storage.md` | `priority:active` key absent from schema table |
| 4 | `04-storage.md` | `player:rank:history:{puuid}` key absent |
| 5 | `05-rate-limiting.md` | `acquire_token()` return type documented as bool, actually int |
| 6 | `05-rate-limiting.md` | `wait_for_token()` described as fixed 50ms polling; actually adaptive |
| 7 | `05-rate-limiting.md` | Lua script section outdated (2 KEYS vs actual 4 KEYS) |
| 8 | `06-failure-resilience.md` | XADD+ZREM row outdated — now atomic via `_DISPATCH_LUA` |
| 9 | `07-containers.md` | References nonexistent `base.Dockerfile` |
| 10 | `07-containers.md` | `docker-compose.yml` section shows old per-service pattern |
| 11 | `ARCHITECTURE.md` | Phase 20 missing from implementation phases table |

---

### Error Messages

| ID | Surface | Severity | Issue |
|----|---------|----------|-------|
| E1 | Web UI | minor | Data Dragon fetch errors silently swallowed (no logging) |
| E3 | Web UI | minor | DLQ corrupt entry message suggests nonexistent `dlq clear {id}` subcommand |
| E4 | Logs | nit | Fetcher "server error" log lacks "will retry via DLQ" context |
| E5 | Admin CLI | nit | `--json` missing from `delayed-list`, `recalc-priority`, `recalc-players` |

---

### Architecture

- Discovery / delay-scheduler use module-level `global _shutdown` — breaks multi-loop/test scenarios. Use `asyncio.Event` instead.
- ~~Envelope schema mismatch~~: Verified — `contracts/schemas/envelope.json` correctly defines `dlq_attempts` as `type: "integer"`, matching the model.

---

## Low

### Adaptive rate limiter backoff (P10-ARC-4/OPT-2)

Return remaining `wait_ms` from Lua script on denial; sleep until next slot instead of
fixed 50ms polling in `wait_for_token()`.

---

### CLI symbols debate (P11-DD-8)

CLI uses `[OK]`/`[ERROR]` text. Design director prefers checkmark/x-mark symbols.
Deferred: ASCII-safe vs Unicode.

---

### DRY-4: `_DISCOVER_KEY` redefined instead of importing constant

**Files:** `parser/main.py`, `discovery/main.py`

**Fix:** Import `DISCOVER_PLAYERS_KEY` from `lol_pipeline.constants`.

---

### DRY-7: Consumer `main()` boilerplate repeated across 4 services

~15-line template repeated in crawler, fetcher, parser, analyzer. Acceptable inline but
could extract `consumer_id()` and `autoclaim_from_config(cfg)` helpers.

---

### CR-3 (Complexity Review): RiotClient._get writes 2 Redis SETs on every successful API call

40 extra Redis writes/second at full throughput, writing the same rate limit values.

**Fix:** Cache last-written limits in-process; only SET when value changes.

---

### CR-4 (Complexity Review): Fetcher 4 sequential Redis calls could be pipelined

4-5 Redis round-trips per match fetch (HSET, EXPIRE, SADD, TTL check).

**Fix:** Pipeline into single round-trip.

---

### CR-6 (Complexity Review): Crawler `_compute_activity_rate` 3 sequential Redis calls

ZRANGE + ZCARD + HSET could be pipelined.

---

### CR-7 (Complexity Review): Crawler rank storage 2 sequential Redis calls

HSET + EXPIRE could be pipelined.

---

### CR-8 (Complexity Review): DLQ summary page redundant XLEN call

`_dlq_summary_html` reads XLEN, then caller reads XLEN again.

---

---

## Deferred (Phase 14+)

- P14-SEC-2: CSRF protection for `/dlq/replay/{id}` (needs token infrastructure)
- P14-ARC-4: Migrate 5 config values to pydantic `Config`
- P14-FV-1: Analyzer cursor stalls on expired participant data
- P14-FV-4: Analyzer premature priority clear on partial match data
- P14-FV-5: Parser analyze pipeline partial-XADD + raw-blob-expiry compound failure
- P14-FV-8: Recovery 404 discards with no audit trail
- P14-PM-4/PM-6: `cmd_dlq_list` table mode + `dlq clear` preflight scope line
- P14-UX-4/12: DLQ pagination total count + cursor-based pagination
- P14-UX-6: Dashboard double-queries `stream:dlq`
- P14-WD/UX ARIA: nav aria-label, aria-current, role="alert", form label pairing
- P14-RD-*: Responsive CSS improvements
- P14-DD-*: Design system cleanup (rgba tokens, h2/h3 rules, spacing scale)
- P14-GD-*: CLI output formatting (DLQ table borders, stats JSON, progress signals)
- P14-DX-4-13: DevEx improvements (conftest.py, pre-commit mypy, parallel check)
- P14-DOC-4/5/7/8/12-18: Large env var table updates, storage schema, deployment docs
- P14-DBG-6: rate_limiter stored-limit keys not scoped to key_prefix

---

## Test Coverage Gaps

| # | File | Gap | Tests to write |
|---|------|-----|----------------|
| 1 | `service.py` | `_dispatch_batch` shutdown mid-batch | 1 |
| 2 | `streams.py` | `_archive_corrupt` audit trail verification | 1 |
| 3 | `fetcher/main.py` | `seen:matches` conditional TTL (F5 false-branch) | 1 |
| 4 | `crawler/main.py` | Activity rate low/medium tiers | 2 |
| 5 | `streams.py` | Autoclaim corrupt entry in `consume()` | 1 |

### Additional testing gaps

- No tests for `_streams_fragment_html`, `show_dlq` route, `/stats/matches` route
- Analyzer `_derived` division edge cases (extreme values)
- No test for `_tail_file` with large files
- Admin helpers: `_region_from_match_id`, `_resolve_puuid` error paths, `cmd_dlq_clear` with `all=False`
- Crawler priority preservation not tested
- Delay-scheduler `_tick` OSError path untested

---

## Fuzzing Targets (Hypothesis property-based tests)

- `MessageEnvelope.from_redis_fields` — random subsets of keys, random value types, round-trip identity
- `DLQEnvelope.from_redis_fields` — random subsets, extra keys, null values, `retry_after_ms` parsing
- `riot_api._raise_for_status` — status codes 100-599, malformed `Retry-After` header
- `_derived` (analyzer) — missing keys, zero/negative values, ZeroDivisionError guard
- `_parse_match` (parser) — random bytes, truncated JSON, missing required fields
- `_format_stat_value` (UI) — `"nan"`, `"inf"`, `""`, very long strings, unicode
- `_badge` (UI) — invalid variants, HTML/JS injection in text
- Redis key construction — unicode, colons, newlines, null bytes in PUUIDs/match_ids
- `_parse_log_line` (UI) — arbitrary strings, nested JSON, binary data
- `_validate` (parser) — deeply nested dicts, missing `info`/`metadata`, non-dict types
- `RawStore._search_bundle_file` — corrupted JSONL bundles, lines with no tab separator

---

## Integration Test Scenarios (not yet implemented)

- **IT-08:** Seed with priority -> verify Discovery paused until complete
- **IT-09:** Two manual seeds -> verify both process before any discovery
- **IT-10:** Priority TTL expiry -> verify Discovery resumes (mock time)
- **IT-11:** DLQ round-trip preserves priority field
- **IT-12:** Concurrent fetchers respect rate limit under load
- **IT-13:** Parser handles Riot API schema change gracefully (missing fields)
- **IT-14:** Full pipeline E2E: seed -> crawl -> fetch -> parse -> analyze -> UI displays stats

---

## Feature: Champion Build Recommendations

The single largest feature gap vs OP.GG/U.GG. Pipeline already collects items, runes, skill
order, and summoner spells per participant but never aggregates or displays them.

**Components:**
1. Analyzer: new aggregation keys (`champion:builds:*`, `champion:runes:*`, `champion:skills:*`, `champion:spells:*`)
2. UI: `/champions/{name}` build section with DDragon icons
3. No new streams or envelope changes needed

**Complexity:** Medium (~300 lines). **Risk:** Low (additive, no existing changes).

---

## Security (open items)

- UI `player:name:` cache has no TTL — unbounded memory growth
- UI auto-seed has no rate limiting — unlimited `publish()` calls per anonymous user
- No input validation on `region` parameter in UI
- Admin CLI `_resolve_puuid` prints unsanitized input to stderr (terminal injection)
- Redis ACLs — per-service users with minimal permissions
- TLS reverse proxy docs (Caddy/nginx)
- Redis TLS (`rediss://`) for production

---

## UI/UX (open items)

- Bugfix: switching language/theme should not navigate to a different page afterwards (stay on current page)
- Audit all fallback/default values — replace with explicit errors. No silent fallbacks to magic strings/numbers.
- Wire `lol_pipeline.i18n.label()` into all UI displays of roles, tiers, queues (currently raw English codes)
- README: Player Stats screenshot should show an actual player with sufficient entries to showcase
- Render skip-to-content `<a>` (`.skip-link` CSS exists but no element uses it)
- Wire up gauge/progressbar for stream depths (CSS defined but never rendered)
- Match detail page (click a match row for full participant data)
- Player comparison view (side-by-side stats)
- `/players`: server-side sort controls (name, region, date)
- `/stats`: sparkline for win rate trend
- Toast notifications for seed instead of page reload
- WebSocket for `/logs` and `/streams` (replace polling)
- Dark/light theme toggle
- Export stats as CSV/JSON

---

## Infrastructure (open items)

- `docker-compose.prod.yml` (baked images, `--requirepass`, resource limits, log rotation)
- Redis `maxmemory 4gb` + `noeviction` policy in compose
- Integration test CI job (testcontainers)
- Trivy image scanning in CI
- Prometheus + Redis Exporter + Grafana monitoring stack
- `pip-audit` in CI for dependency scanning
- Kubernetes Helm chart
- GitHub Actions deploy workflow

---

## Performance (open items)

- Analyzer creates a new pipeline per match in a loop — batch all HINCRBY/ZINCRBY into one pipeline
- RawStore `_exists_in_bundles` scans all JSONL files — redundant full-file scan in `set()`
- RawStore: sorted JSONL bundles + binary search (future)
- Discovery batch pipelining when batch_size > 10 (future)
- `pytest-xdist` parallel test execution across all services
