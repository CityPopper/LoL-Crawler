# TODO — Open Work Items

---

## Feature: op.gg Dual-Source Integration

See `questions.md` for locked architecture decisions.

### OPGG-0: Research op.gg internal API

Identify exact JSON endpoints used by their SPA for match history and match detail per region (e.g., `https://lol.op.gg/api/v1.0/...`). Map response fields to Riot match-v5 schema. Document the ETL field mapping before any code is written.

**Output:** ETL mapping document (can live in `docs/architecture/` or inline in `OpggClient`).

---

### OPGG-1: Parameterize rate limiter stored-limit keys by prefix

**File:** `lol-pipeline-common/src/lol_pipeline/rate_limiter.py` lines 48-49 and `_rate_limiter_data.py` lines 23-24

- [ ] **Red:** Write failing test asserting `acquire_token(r, key_prefix="ratelimit:opgg")` reads limits from `ratelimit:opgg:limits:short` / `ratelimit:opgg:limits:long`, NOT `ratelimit:limits:short`
- [ ] **Green:** Parameterize `KEYS[3]`/`KEYS[4]` in Lua script as `{key_prefix}:limits:short` / `{key_prefix}:limits:long`. Add `limit_long: int` parameter to `acquire_token()` and `wait_for_token()`.
- [ ] **Refactor:** Verify existing Riot API tests still pass (no regression)

---

### OPGG-2: Add op.gg config vars to Config and .env.example

- [ ] **Red:** Write failing test that `Config()` raises on missing required op.gg fields (or that optional fields default correctly)
- [ ] **Green:** Add to `lol-pipeline-common/src/lol_pipeline/config.py` and `.env.example`: `OPGG_RATE_LIMIT_PER_SECOND` (default: 2), `OPGG_RATE_LIMIT_LONG` (default: 30), `OPGG_MATCH_DATA_DIR` (default: `/pipeline-data/opgg`), `OPGG_API_KEY: str | None = None`
- [ ] **Refactor:** Verify all services that import `Config` still load correctly

---

### OPGG-3: Update `match_id_payload.json` PACT schema

**File:** `lol-pipeline-common/contracts/schemas/payloads/match_id_payload.json`

- [ ] **Red:** Write failing contract test that a `stream:match_id` message containing `"source": "opgg"` is rejected by the current schema (proves `additionalProperties: false` blocks it)
- [ ] **Green:** Add `source` as an optional `enum: ["riot", "opgg"]` property to the schema
- [ ] **Refactor:** Update Fetcher and Crawler pacts; verify all existing contract tests still pass

---

### OPGG-4: Implement `OpggClient` in lol-pipeline-common

New module `lol-pipeline-common/src/lol_pipeline/opgg_client.py`.

- [ ] **Red:** Write failing tests for: (1) `get_match_history(puuid, region)` returns list of match IDs, (2) `get_match(match_id, region)` returns match-v5-shaped dict with `source: "opgg"`, (3) ETL drops proprietary fields (OP Score etc.), (4) unexpected schema → raises `OpggParseError`
- [ ] **Green:** Implement `OpggClient` — accepts injected `httpx.AsyncClient`, realistic browser headers, ETL layer normalizing to match-v5 format, schema validation, `source` + `fetched_at` on all output. Use `respx` fixtures.
- [ ] **Refactor:** Extract ETL mapping to `_opgg_etl.py`, schema validation to `_opgg_schema.py`

---

### OPGG-5: Integrate source selector into Fetcher and Crawler

**Files:** `lol-pipeline-fetcher/src/lol_fetcher/main.py`, `lol-pipeline-crawler/src/lol_crawler/main.py`

- [ ] **Red:** Write failing tests: (1) when op.gg has budget, Fetcher calls `OpggClient` not `RiotClient`, (2) when op.gg fails with `opgg_*` code, falls through to Riot API and succeeds, (3) `system:halted` is never set on op.gg failure, (4) `raw:opgg:match:{match_id}` written for op.gg source, `raw:match:{match_id}` for Riot
- [ ] **Green:** Add source selector logic; op.gg failure → log warning + fallthrough, never halt
- [ ] **Refactor:** Verify all existing Fetcher and Crawler tests still pass

---

### OPGG-6: Add `opgg-status` admin command

- [ ] **Red:** Write failing test that `cmd_opgg_status` exists and returns enabled status, disk size, fetch count
- [ ] **Green:** Implement `just admin opgg-status` — `opgg:fetch_count` counter key, `pipeline-data/opgg/` disk usage
- [ ] **Refactor:** Verify existing admin commands unaffected

---

## Critical

### RDB-1: Bucket `seen:matches` into daily sets (unbounded memory)

**File:** `lol-pipeline-fetcher/src/lol_fetcher/main.py` lines 56-72, `lol-pipeline-crawler/src/lol_crawler/main.py` lines 83-87

Single global SET grows ~500 MB over 7 days at 20 req/s. A TODO comment in the code already acknowledges this.

- [ ] **Red:** Write failing test asserting `seen:matches:{YYYY-MM-DD}` is written instead of `seen:matches`; crawler dedup checks today's + yesterday's buckets
- [ ] **Green:** In Fetcher, change `r.sadd("seen:matches", match_id)` to `r.sadd(f"seen:matches:{today}", match_id)`; set 8-day TTL on first write. In Crawler, check today's + yesterday's bucket in pipeline. Update `_constants.py` and `config.py` key references.
- [ ] **Refactor:** Update `04-storage.md` key schema; remove `seen_matches_ttl_seconds` from single-key logic

---

### RDB-2: Replace `match:status:parsed` SET with per-match `HSETNX`

**File:** `lol-pipeline-parser/src/lol_parser/main.py` lines 576-583

Unbounded global SET with same growth problem as `seen:matches`. `HSETNX match:{match_id} status parsed` gives identical first-writer-wins semantics using the already-existing per-match hash (with its own 7-day TTL).

- [ ] **Red:** Write failing test that the parser no longer writes to a `match:status:parsed` SET; idempotency is enforced via `match:{match_id}.status` field
- [ ] **Green:** Replace `SADD match:status:parsed` + conditional EXPIRE with `HSETNX match:{match_id} status parsed` (returns 1 on first write). Update admin commands that do `SMEMBERS match:status:parsed` to use `SCAN match:*` + `HGET`.
- [ ] **Refactor:** Update `04-storage.md`; verify all parser tests pass

---

### RDB-3: Paginate `players:all` reads in UI (N+1 amplification)

**File:** `lol-pipeline-ui/src/lol_ui/routes/players.py` line 85

Fetches all PUUIDs then pipelines 2 Redis calls per player. At 50K players = 100K commands per page load.

- [ ] **Red:** Write failing test asserting `/players` calls `ZREVRANGE` with `start`/`stop` bounds, not `0 -1`
- [ ] **Green:** Add `page` + `per_page` parameters; use `ZREVRANGE players:all (page*per_page) ((page+1)*per_page - 1)`; pipeline only those N players
- [ ] **Refactor:** Verify players route tests pass; update UI to render pagination controls

---

### OPGG-4.5: Add `key_prefix` parameter to `RawStore`

**File:** `lol-pipeline-common/src/lol_pipeline/raw_store.py`

Prerequisite for OPGG-4/OPGG-5 multi-source support. Currently `RawStore` always uses `raw:match:` as the Redis key prefix. Each source needs its own prefix (`raw:opgg:match:`, `raw:ugg:match:`, etc.).

- [ ] **Red:** Write failing test asserting `RawStore(r, key_prefix="raw:opgg:match:").set("mid", ...)` writes to `raw:opgg:match:mid` not `raw:match:mid`
- [ ] **Green:** Add `key_prefix: str = "raw:match:"` constructor parameter; use it in `set()`, `get()`, `exists()`, `_redis_key()`
- [ ] **Refactor:** Verify all existing RawStore tests still pass (default behavior unchanged)

---

## Think Cycle 5 — Medium Fixes + Test Coverage Gaps

### E1: Log DDragon fetch errors instead of silently swallowing

**File:** `lol-pipeline-ui/src/lol_ui/ddragon.py` lines 61, 87

- [ ] **Red:** Write failing test asserting that a DDragon HTTP failure emits a `logging.WARNING` record (currently: silent `return None`)
- [ ] **Green:** Add `_log = logging.getLogger("ui.ddragon")` to `ddragon.py`; in `_get_ddragon_json` `except Exception` block add `_log.warning("DDragon fetch failed", extra={"url": url}, exc_info=True)` before `return None`; same in `_get_ddragon_version`
- [ ] **Refactor:** Verify all existing DDragon tests still pass

---

### E3: Fix DLQ corrupt entry error message — references nonexistent `dlq clear {id}` command

**File:** `lol-pipeline-ui/src/lol_ui/routes/dlq.py` lines 176, 188

`cmd_dlq_clear` only accepts `--all`. The per-ID syntax `just admin dlq clear {id}` does not exist.

- [ ] **Red:** Write failing test that asserts `just admin dlq clear {id}` text does NOT appear in corrupt-entry HTML
- [ ] **Green:** Change both occurrences to suggest `just admin dlq clear --all` (remove per-ID hint)
- [ ] **Refactor:** Verify DLQ route tests still pass

---

### E4: Add "routing to DLQ for retry" context to Riot API error log

**File:** `lol-pipeline-common/src/lol_pipeline/helpers.py` line 147

- [ ] **Red:** Write failing test asserting log message contains "DLQ" for a `ServerError` path in `handle_riot_api_error`
- [ ] **Green:** Change `"Riot API error"` to `"Riot API error — routing to DLQ for retry"`
- [ ] **Refactor:** Verify all callers' tests pass

---

### E5: Add `--json` flag to `delayed-list`, `recalc-priority`, `recalc-players`

**File:** `lol-pipeline-admin/src/lol_admin/main.py`, `cmd_delayed.py`, `cmd_player.py`

- [ ] **Red:** Write failing tests asserting each command accepts `--json` and outputs valid JSON lines
- [ ] **Green:** Add `--json` flag to each parser; add JSON output branch to each handler
- [ ] **Refactor:** Verify all existing admin tests pass

---

### TCG-1: Test `_dispatch_batch` shutdown mid-batch

**File:** `lol-pipeline-common/src/lol_pipeline/service.py`

- [ ] **Red:** Write failing test that cancels the asyncio task mid-batch and asserts no messages are double-acked
- [ ] **Green:** Existing code should handle this — test validates it
- [ ] **Refactor:** N/A if test passes green

---

### TCG-3: Test `seen:matches` conditional TTL false-branch (F5)

**File:** `lol-pipeline-fetcher/src/lol_fetcher/main.py` line 71

- [ ] **Red:** Write failing test for the path where `seen_ttl >= 0` (TTL already set) — assert that `r.expire("seen:matches", ...)` is NOT called
- [ ] **Green:** Existing code should handle this — test validates it
- [ ] **Refactor:** N/A if test passes green

---

### TCG-4: Test crawler activity rate low/medium tiers

**File:** `lol-pipeline-crawler/src/lol_crawler/main.py`

- [ ] **Red:** Write 2 failing tests for low-tier and medium-tier activity rate paths that currently have no coverage
- [ ] **Green:** Existing code should handle these — tests validate behavior
- [ ] **Refactor:** N/A if tests pass green

---

## Medium

### Error Messages

| ID | Surface | Severity | Issue |
|----|---------|----------|-------|
| E1 | Web UI | minor | Data Dragon fetch errors silently swallowed (no logging) |
| E3 | Web UI | minor | DLQ corrupt entry message suggests nonexistent `dlq clear {id}` subcommand |
| E4 | Logs | nit | Fetcher "server error" log lacks "will retry via DLQ" context |
| E5 | Admin CLI | nit | `--json` missing from `delayed-list`, `recalc-priority`, `recalc-players` |

---

### RDB-4: Add 30-day safety-net TTL to `discover:players`

**File:** `lol-pipeline-discovery/src/lol_discovery/main.py`

ZSET has no TTL — if Discovery is disabled for 30+ days, queue is stale but persists forever.

- [ ] **Red:** Write failing test asserting `discover:players` gets a 30-day TTL on creation
- [ ] **Green:** After ZADD in Parser (`parser/main.py:512`), check TTL and set 30d if not set
- [ ] **Refactor:** Verify parser + discovery tests pass

---

### RDB-5: Store envelope ID (not full JSON) as `delayed:messages` ZSET member

**File:** `lol-pipeline-common/src/lol_pipeline/streams.py`

Current members are ~500-byte JSON blobs. Should store the `id` field as member, envelope in a separate hash `delayed:envelope:{id}`. Memory: 500B → ~40B per entry. Also prevents duplicate entries for the same logical message.

- [ ] **Red:** Write failing test asserting `delayed:messages` member is the envelope `id` string, not the full JSON; `delayed:envelope:{id}` hash holds the full envelope
- [ ] **Green:** In `nack_to_dlq()` / delayed delivery write path: ZADD with envelope `id`; HSET `delayed:envelope:{id}` with JSON. In Delay Scheduler: ZRANGEBYSCORE returns IDs; fetch envelope from hash; HDEL after dispatch.
- [ ] **Refactor:** Update `03-streams.md` and `04-storage.md`; verify all delay scheduler + streams tests pass

---

## Low


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

## UI Visual Analysis — Phase 15 Findings

### High — Broken / Blocking

- **UI-H1:** Art Pop mobile: decorative SVGs (shield/swords) overlap interactive content (table rows, form fields) at full desktop scale. WCAG contrast failure in overlap zones. Fix: `@media (max-width:600px)` guard to hide or scale down decorations in `themes.py`.
- **UI-H2:** Streams mobile: STATUS/PENDING/LAG columns silently truncated — `min-width:600px` forces overflow but iOS hides scrollbars, leaving columns invisible with no affordance. Fix: add visible scroll hint or responsive column hide/show.
- **UI-H3:** Mobile nav clips rightmost items (Matchups, Logs, DLQ) with no scroll indicator. The `›` arrow overlaps the label it hints past. Fix: proper `overflow-x: auto` + scroll-fade mask on nav.
- **UI-H4 (zh-CN):** Consumer group names (`crawlers`, `fetchers`, etc.) display as raw English in the Streams table despite the header being translated. Blocking for Chinese-only users.
- **UI-H5 (zh-CN):** Redis stream key names (`stream:puuid`, `stream:match_id`, etc.) exposed raw with no Chinese labels or tooltips. Chinese-only users can't know what each stream does.

### Medium — Confusing / Degraded UX

- **UI-M1:** Theme picker (fixed bottom-right) overlaps footer disclaimer text on all mobile pages — no `padding-bottom` clearance on footer. Fix: add `~50px` bottom padding to footer/main.
- **UI-M2:** Dashboard mobile: stream STATUS badge column (green squares) not visible — absent from mobile stream depths table.
- **UI-M3:** Art Pop theme breaks sticky form — `themes.py` sets `position:relative` overriding `position:sticky` from `css.py`. Form no longer pins on scroll in Art Pop.
- **UI-M4:** Art Pop mobile: header band takes ~25% of 375px viewport, pushing nav and content below fold.
- **UI-M5 (zh-CN):** Players empty state broken grammar — "运行 `just seed GameName#Tag` 开始追踪。" splits into 3 disconnected fragments. Fix: rewrite as single sentence.
- **UI-M6 (zh-CN):** "死信" (DLQ) navigation label is unintuitive. "失败队列" or keeping "DLQ" would be clearer.
- **UI-M7:** Logs mobile: service badge text (~40px wide) unreadable. No contrast or sizing adjustment at mobile viewport.

### Low — Polish

- **UI-L1:** DLQ mobile stats: "n/a oldest message" wraps to its own row (2+1 layout) instead of uniform 3-column grid.
- **UI-L2:** Art Pop h1 not full-bleed on mobile — 2rem body margin creates white gaps in the red header bar.
- **UI-L3:** Stats page has no onboarding text — no hint it's a lookup-by-tracked-player form.
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
