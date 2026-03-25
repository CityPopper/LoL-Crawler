# TODO — Open Work Items

---

### OPGG-5: Integrate source selector into Fetcher and Crawler

**Files:** `lol-pipeline-fetcher/src/lol_fetcher/main.py`, `lol-pipeline-crawler/src/lol_crawler/main.py`

- [x] **Red:** Write failing tests: (1) when op.gg enabled, Fetcher uses opgg RawStore, (2) when op.gg data not found, falls through to Riot API, (3) `system:halted` is never set on op.gg failure, (4) `raw:opgg:match:{match_id}` written for op.gg source, `raw:match:{match_id}` for Riot
- [x] **Green:** Added `opgg_enabled` config field, `_try_opgg()` helper, `opgg` param to `_fetch_match()`, source selector logic with fallthrough
- [x] **Refactor:** All fetcher tests pass (28/28), all crawler tests pass (64/64)

---

### OPGG-6: Add `opgg-status` admin command

- [x] **Red:** Write failing test that `cmd_opgg_status` exists and returns enabled status, disk size, fetch count
- [x] **Green:** Implement `just admin opgg-status` — `opgg:fetch_count` counter key, `pipeline-data/opgg/` disk usage
- [x] **Refactor:** Verify existing admin commands unaffected

---

## Critical

### RDB-2: Replace `match:status:parsed` SET with per-match `HSETNX`

**File:** `lol-pipeline-parser/src/lol_parser/main.py` lines 576-583

Unbounded global SET with same growth problem as `seen:matches`. `HSETNX match:{match_id} status parsed` gives identical first-writer-wins semantics using the already-existing per-match hash (with its own 7-day TTL).

- [x] **Red:** Write failing test that the parser no longer writes to a `match:status:parsed` SET; idempotency is enforced via `match:{match_id}.status` field
- [x] **Green:** Replace `SADD match:status:parsed` + conditional EXPIRE with `HSETNX match:{match_id} status parsed` (returns 1 on first write). Update admin commands that do `SMEMBERS match:status:parsed` to use `SCAN match:*` + `HGET`.
- [x] **Refactor:** Update `04-storage.md`; verify all parser tests pass

---

### RDB-3: Paginate `players:all` reads in UI (N+1 amplification)

**File:** `lol-pipeline-ui/src/lol_ui/routes/players.py` line 85

Fetches all PUUIDs then pipelines 2 Redis calls per player. At 50K players = 100K commands per page load.

- [x] **Red:** Write failing test asserting `/players` calls `ZREVRANGE` with `start`/`stop` bounds, not `0 -1`
- [x] **Green:** Add `page` + `per_page` parameters; use `ZREVRANGE players:all (page*per_page) ((page+1)*per_page - 1)`; pipeline only those N players
- [x] **Refactor:** Verify players route tests pass; update UI to render pagination controls

---

### TCG-5: Test `_handle_with_retry` when `nack_to_dlq` itself fails persistently

**File:** `lol-pipeline-common/src/lol_pipeline/service.py`

**Gap:** When `nack_to_dlq` raises `RedisError` (e.g. DLQ stream at `maxlen`), the exception propagates to `_dispatch_batch` which sleeps and retries. On PEL redelivery, `_incr_retry` increments the counter again. Since `count >= max_retries` is already true, `nack_to_dlq` is called again. If it keeps failing, the retry counter grows unboundedly and the message is never DLQ'd or archived — stuck forever.

- [x] **Red:** Write failing test: handler always fails + `nack_to_dlq` stub raises `RedisError`; assert that after 3 cycles the message reaches a terminal state (not an infinite nack-fail loop)
- [x] **Green:** Add a `nack_to_dlq` attempt cap or a fallback archive path so even persistent nack failures reach a terminal state
- [x] **Refactor:** Verify all existing `service.py` tests pass; verify IT-03 DLQ round-trip still passes

---

## Think Cycle 5 — Medium Fixes + Test Coverage Gaps

### E1: Log DDragon fetch errors instead of silently swallowing

**File:** `lol-pipeline-ui/src/lol_ui/ddragon.py` lines 61, 87

- [x] **Red:** Write failing test asserting that a DDragon HTTP failure emits a `logging.WARNING` record (currently: silent `return None`)
- [x] **Green:** Add `_log = logging.getLogger("ui.ddragon")` to `ddragon.py`; in `_get_ddragon_json` `except Exception` block add `_log.warning("DDragon fetch failed", extra={"url": url}, exc_info=True)` before `return None`; same in `_get_ddragon_version`
- [x] **Refactor:** Verify all existing DDragon tests still pass

---

### E3: Fix DLQ corrupt entry error message — references nonexistent `dlq clear {id}` command

**File:** `lol-pipeline-ui/src/lol_ui/routes/dlq.py` lines 176, 188

`cmd_dlq_clear` only accepts `--all`. The per-ID syntax `just admin dlq clear {id}` does not exist.

- [x] **Red:** Write failing test that asserts `just admin dlq clear {id}` text does NOT appear in corrupt-entry HTML
- [x] **Green:** Change both occurrences to suggest `just admin dlq clear --all` (remove per-ID hint)
- [x] **Refactor:** Verify DLQ route tests still pass

---

### E4: Add "routing to DLQ for retry" context to Riot API error log

**File:** `lol-pipeline-common/src/lol_pipeline/helpers.py` line 147

- [x] **Red:** Write failing test asserting log message contains "DLQ" for a `ServerError` path in `handle_riot_api_error`
- [x] **Green:** Change `"Riot API error"` to `"Riot API error — routing to DLQ for retry"`
- [x] **Refactor:** Verify all callers' tests pass

---

### E5: Add `--json` flag to `delayed-list`, `recalc-priority`, `recalc-players`

**File:** `lol-pipeline-admin/src/lol_admin/main.py`, `cmd_delayed.py`, `cmd_player.py`

- [x] **Red:** Write failing tests asserting each command accepts `--json` and outputs valid JSON lines
- [x] **Green:** Add `--json` flag to each parser; add JSON output branch to each handler
- [x] **Refactor:** Verify all existing admin tests pass

---

### TCG-1: Test `_dispatch_batch` shutdown mid-batch

**File:** `lol-pipeline-common/src/lol_pipeline/service.py`

- [x] **Red:** Write failing test that cancels the asyncio task mid-batch and asserts no messages are double-acked
- [x] **Green:** Existing code should handle this — test validates it
- [x] **Refactor:** N/A if test passes green

---

### TCG-3: Test `seen:matches` conditional TTL false-branch (F5)

**File:** `lol-pipeline-fetcher/src/lol_fetcher/main.py` line 71

- [x] **Red:** Write failing test for the path where `seen_ttl >= 0` (TTL already set) — assert that `r.expire("seen:matches:{today}", ...)` is NOT called
- [x] **Green:** Existing code should handle this — test validates it
- [x] **Refactor:** N/A if test passes green

---

### TCG-4: Test crawler activity rate low/medium tiers

**File:** `lol-pipeline-crawler/src/lol_crawler/main.py`

- [x] **Red:** Write 2 failing tests for low-tier and medium-tier activity rate paths that currently have no coverage
- [x] **Green:** Existing code should handle these — tests validate behavior
- [x] **Refactor:** N/A if tests pass green

---

### TCG-6: Verify parser re-parse does not silently enqueue duplicate `stream:analyze` messages

**File:** `lol-pipeline-parser/tests/unit/test_main.py`

**Gap:** `test_reparse_idempotent` verifies Redis state (sorted set, hash) is idempotent on re-parse but does NOT assert on `xlen(stream:analyze)`. After two parses of the same 10-participant match, there will be 20 analyze messages. System relies on the analyzer cursor to deduplicate — but no test verifies this cross-service guarantee.

- [x] **Red:** Write failing test: parse a match twice, assert `xlen(stream:analyze)` is still 10 (not 20) — expect the assertion to fail, proving the gap exists
- [x] **Green:** Documented intentional behavior — 20 messages is correct; analyzer cursor is the dedup layer. Test added with explanation.
- [x] **Refactor:** All parser tests pass

---

### TCG-7: Test analyzer lock mutual exclusion under true concurrency (not cursor dedup)

**File:** `lol-pipeline-analyzer/tests/unit/test_main.py`

- [x] **Red:** Write failing test: two concurrent `_analyze_player` calls for the same PUUID with the identical match; assert `total_games == 1` (lock prevents double-count, not cursor)
- [x] **Green:** Existing `SET NX` in `_analyze_player` satisfies this; test documents the invariant explicitly
- [x] **Refactor:** All analyzer tests pass

---

### TCG-8: Test `has_priority_players` cleanup with multiple orphaned members

**File:** `lol-pipeline-common/src/lol_pipeline/priority.py`

- [x] **Red:** Write failing test: add 3 orphaned members (no corresponding Redis keys) to the priority set; call `has_priority_players` repeatedly; assert it returns `False` within a bounded number of calls
- [x] **Green:** Existing probabilistic cleanup satisfies this; test documents the O(n) bound
- [x] **Refactor:** All priority tests pass

---

### TCG-9: Verify `_REPLAY_LUA` is idempotent on double-call (DLQ replay crash safety)

**File:** `lol-pipeline-common/src/lol_pipeline/streams.py`

- [x] **Red:** Write failing test: call `replay_from_dlq` twice for the same DLQ entry; assert the target stream has exactly 1 message (currently will have 2)
- [x] **Green:** Added XRANGE existence guard in `_REPLAY_LUA` before XADD; returns 0 on second call (idempotent)
- [x] **Refactor:** All streams tests pass

---

## Medium

### Error Messages

| ID | Surface | Severity | Issue | Status |
|----|---------|----------|-------|--------|
| E1 | Web UI | minor | Data Dragon fetch errors silently swallowed (no logging) | ✅ FIXED |
| E3 | Web UI | minor | DLQ corrupt entry message suggests nonexistent `dlq clear {id}` subcommand | ✅ FIXED |
| E4 | Logs | nit | Fetcher "server error" log lacks "will retry via DLQ" context | ✅ FIXED |
| E5 | Admin CLI | nit | `--json` missing from `delayed-list`, `recalc-priority`, `recalc-players` | ✅ FIXED |

---

### RDB-4: Add 30-day safety-net TTL to `discover:players`

**File:** `lol-pipeline-discovery/src/lol_discovery/main.py`

ZSET has no TTL — if Discovery is disabled for 30+ days, queue is stale but persists forever.

- [x] **Red:** Write failing test asserting `discover:players` gets a 30-day TTL on creation
- [x] **Green:** After ZADD in Parser (`parser/main.py:512`), check TTL and set 30d if not set
- [x] **Refactor:** Verify parser + discovery tests pass

---

### RDB-5: Store envelope ID (not full JSON) as `delayed:messages` ZSET member

**File:** `lol-pipeline-common/src/lol_pipeline/streams.py`

Current members are ~500-byte JSON blobs. Should store the `id` field as member, envelope in a separate hash `delayed:envelope:{id}`. Memory: 500B → ~40B per entry. Also prevents duplicate entries for the same logical message.

- [x] **Red:** Write failing test asserting `delayed:messages` member is the envelope `id` string, not the full JSON; `delayed:envelope:{id}` hash holds the full envelope
- [x] **Green:** ZADD with envelope `id`; HSET `delayed:envelope:{id}` with JSON; Delay Scheduler fetches from hash and HDEL after dispatch. Legacy JSON-blob format handled with backwards-compat reader.
- [x] **Refactor:** All delay scheduler + recovery + streams tests pass

---

## Low


### CLI symbols debate (P11-DD-8)

CLI uses `[OK]`/`[ERROR]` text. Design director prefers checkmark/x-mark symbols.
Deferred: ASCII-safe vs Unicode.

---

### ~~DRY-4: `_DISCOVER_KEY` redefined instead of importing constant~~ (DONE)

**Files:** `parser/main.py`, `discovery/main.py`

**Fix:** Import `DISCOVER_PLAYERS_KEY` from `lol_pipeline.constants`. Both `_data.py` files now import from common.

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

- [x] **Red:** Test asserting pipeline.execute() called (TestFetcherPipelineBatching)
- [x] **Green:** Already implemented in `_store_and_publish` via `r.pipeline(transaction=False)`
- [x] **Refactor:** All fetcher tests pass (28/28)

---

### CR-6 (Complexity Review): Crawler `_compute_activity_rate` 3 sequential Redis calls

ZRANGE + ZCARD + HSET could be pipelined.

- [x] **Red:** Test asserting ZRANGE+ZCARD not separate top-level calls (TestActivityRatePipeline)
- [x] **Green:** Already implemented via `r.pipeline(transaction=False)` in `_compute_activity_rate`
- [x] **Refactor:** All crawler tests pass (64/64)

---

### CR-7 (Complexity Review): Crawler rank storage 2 sequential Redis calls

HSET + EXPIRE could be pipelined.

- [x] **Red:** Test asserting HSET+EXPIRE batched in pipeline (TestRankStoragePipeline)
- [x] **Green:** Already implemented via `r.pipeline(transaction=False)` in `_fetch_rank`
- [x] **Refactor:** All crawler tests pass (64/64)

---

### ~~CR-8 (Complexity Review): DLQ summary page redundant XLEN call~~ (DONE)

`_dlq_summary_html` now returns `(html, depth)` tuple; caller unpacks instead of calling XLEN again.

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
| TCG-10 | `analyzer/main.py` `_PROCESS_MATCH_LUA` | Cursor SET does not enforce monotonicity; Lua script unconditionally sets cursor to `ARGV[7]` — caller enforces ascending order but script has no guard | 1 (verify caller always passes ascending-score matches) |
| TCG-11 | `analyzer/test_main.py`, `parser/test_main.py` | `system:halted` unit tests verify absence of side effects but not PEL preservation; a stray `ack()` in the halted path would go undetected | 2 (one per service: assert `pending["pending"] == 1` after halted path) |
| TCG-12 | `test_rate_limiter.py` | Rate limiter atomicity only tested at integration tier (IT-12); no unit-tier test that verifies Lua script KEYS[1..4] are all touched atomically | 1 (unit test with fakeredis asserting all window keys updated in single Lua eval) |
| TCG-13 | `streams.py` `consume_typed` | Three-phase ordering (own PEL → XAUTOCLAIM → new) untested as a sequence; phase 2 skip when PEL non-empty never exercised | 1 (PEL non-empty → assert XAUTOCLAIM not called; PEL empty → XAUTOCLAIM runs) |
| TCG-14 | `recovery/main.py` `_requeue_delayed` | `MULTI/EXEC` wraps `ZADD(delayed:messages)` + `XACK(stream:dlq)` — different hash slots; incompatible with Redis Cluster | 0 now (single-node); flag before any Cluster migration — add note in `04-storage.md` |

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

## UI Visual Analysis — Phase 15 Findings

### High — Broken / Blocking

- **UI-H1:** Art Pop mobile: decorative SVGs (shield/swords) overlap interactive content (table rows, form fields) at full desktop scale. WCAG contrast failure in overlap zones. Fix: `@media (max-width:600px)` guard to hide or scale down decorations in `themes.py`. ✅ FIXED
- **UI-H2:** Streams mobile: STATUS/PENDING/LAG columns silently truncated — `min-width:600px` forces overflow but iOS hides scrollbars, leaving columns invisible with no affordance. Fix: add visible scroll hint or responsive column hide/show. ✅ FIXED
- **UI-H3:** Mobile nav clips rightmost items (Matchups, Logs, DLQ) with no scroll indicator. The `›` arrow overlaps the label it hints past. Fix: proper `overflow-x: auto` + scroll-fade mask on nav. ✅ FIXED
- **UI-H4 (zh-CN):** Consumer group names (`crawlers`, `fetchers`, etc.) display as raw English in the Streams table despite the header being translated. Blocking for Chinese-only users.
- **UI-H5 (zh-CN):** Redis stream key names (`stream:puuid`, `stream:match_id`, etc.) exposed raw with no Chinese labels or tooltips. Chinese-only users can't know what each stream does.

### Medium — Confusing / Degraded UX

- **UI-M1:** Theme picker (fixed bottom-right) overlaps footer disclaimer text on all mobile pages — no `padding-bottom` clearance on footer. Fix: add `~50px` bottom padding to footer/main. ✅ FIXED
- **UI-M2:** Dashboard mobile: stream STATUS badge column (green squares) not visible — absent from mobile stream depths table. ✅ FIXED
- **UI-M3:** Art Pop theme breaks sticky form — `themes.py` sets `position:relative` overriding `position:sticky` from `css.py`. Form no longer pins on scroll in Art Pop. ✅ FIXED
- **UI-M4:** Art Pop mobile: header band takes ~25% of 375px viewport, pushing nav and content below fold. ✅ FIXED
- **UI-M5 (zh-CN):** Players empty state broken grammar — "运行 `just seed GameName#Tag` 开始追踪。" splits into 3 disconnected fragments. Fix: rewrite as single sentence.
- **UI-M6 (zh-CN):** "死信" (DLQ) navigation label is unintuitive. "失败队列" or keeping "DLQ" would be clearer.
- **UI-M7:** Logs mobile: service badge text (~40px wide) unreadable. No contrast or sizing adjustment at mobile viewport. ✅ FIXED

### Low — Polish

- **UI-L1:** DLQ mobile stats: "n/a oldest message" wraps to its own row (2+1 layout) instead of uniform 3-column grid. ✅ FIXED
- **UI-L2:** Art Pop h1 not full-bleed on mobile — 2rem body margin creates white gaps in the red header bar. ✅ FIXED
- **UI-L3:** Stats page has no onboarding text — no hint it's a lookup-by-tracked-player form. ✅ FIXED
- **UI-L4 (zh-CN):** "优先玩家处理中：否" is awkward machine-translation. Should be "玩家优先模式：关闭".
- **UI-L5 (zh-CN):** Dashboard shows "–" vs "0" inconsistently for empty lag/pending values. Meaning is unclear.

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
