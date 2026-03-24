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

### Contract Drift

| ID | Issue | Fix |
|----|-------|-----|
| D1 | `correlation_id` missing from all 6 pact files | Add `"correlation_id": ""` + type matcher to every pact |
| D2 | `dlq_attempts` missing from all 6 MessageEnvelope pacts | Add `"dlq_attempts": 0` + integer matcher to every pact |
| D3 | Provider contract tests validate partial documents (7/10 fields) | Use full `to_redis_fields()` round-trip in provider tests |

---

## Medium

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
- Envelope schema mismatch: `contracts/schemas/envelope.json` defines `dlq_attempts` as `type: "string"` but model stores `int`.

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
