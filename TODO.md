# TODO — Improvement Proposals (All 21 Agents)

Phase 10 "ILLUMINATE", Phase 11 "APEX".
920+ unit tests + contract tests. 19-20 agent review cycle per phase.

---

## ✅ Completed — Orchestrator Cycle 2 (Phase 9)

All B1–B15, C1–C2 items resolved. All I2-C1 through I2-M7 items resolved. See CLAUDE.md for detail.

---

## Phase 10 — "ILLUMINATE" (Orchestrator Cycle 4, 19-agent review)

### Critical

- [ ] P10-CR-1: Rate limiter hardcodes 1s/120s windows — production API keys use 10s/600s windows → 25× throughput cliff when going to prod. Add `RATE_LIMIT_SHORT_WINDOW_MS` / `RATE_LIMIT_LONG_WINDOW_MS` env vars; fix `_parse_app_rate_limit` to search configured windows. (`riot_api.py`)
- [ ] P10-CR-7: Discovery `_resolve_names` calls Riot API without `wait_for_token()` → bypasses shared rate limiter. Add `await wait_for_token(r)` before every Riot API call in discovery. (`discovery/main.py`)
- [ ] P10-QA-1: UI `_auto_seed_player()` still publishes BEFORE `set_priority()` — regression of I2-H1 fix. Swap order: `set_priority` first, then `publish`. (`ui/main.py`)
- [ ] P10-QA-2: UI `_auto_seed_player()` never writes to `players:all` sorted set → player never appears in /players list after auto-seeding from stats page. Add `r.zadd("players:all", {puuid: now_ts})`. (`ui/main.py`)

### High

- [ ] P10-DB-4/FV-2: Rate limiter Lua script accesses `ratelimit:limits:short/long` as literal strings, not via KEYS array → CROSSSLOT violation in Redis Cluster mode. Pass as KEYS[3]/KEYS[4]. (`riot_api.py`)
- [ ] P10-FV-6: Corrupt messages in `consume()` XAUTOCLAIM path are ACK'd and silently dropped without DLQ archiving — audit gap, messages lost forever. Add `nack_to_dlq()` call before ACK on parse failure. (`streams.py`)
- [ ] P10-SEC-4: Missing security headers — add `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer` middleware to UI. (`ui/main.py`)
- [ ] P10-DX-2/R7: Python version skew — Dockerfiles use `python:3.12-slim`, CI uses 3.14, local dev uses 3.14. Update all 10 Dockerfiles to `python:3.14-slim`; update `pyproject.toml` `python_version = "3.14"` / `target-version = "py314"`. (all Dockerfiles, all pyproject.toml)
- [ ] P10-RD-5: `.form-inline` flex targets `input`/`select` but actual flex children are `<label>` wrappers → tablet layout broken (fields don't expand). Fix CSS to target `label` children at 768px breakpoint. (`ui/main.py`)
- [ ] P10-RD-6: Sort control links have `padding: 4px 8px` → ~22px tap target, half the 44px minimum. Fix to `min-height: 44px`. (`ui/main.py`)
- [ ] P10-RD-7: DLQ replay button has inline `style="padding:2px 8px;font-size:12px"` overriding global `min-height: 44px` → ~16px tap target on mobile. Remove inline style, use CSS class. (`ui/main.py`)
- [ ] P10-RD-8: DLQ and match history tables missing `white-space: nowrap` on cells → cells wrap inside scroll container instead of extending table. (`ui/main.py`)

### Medium

- [ ] P10-CR-6: `player:matches:{puuid}` sorted set grows unbounded. Add `ZREMRANGEBYRANK` cap (e.g. keep last 500) after ZADD in parser. (`parser/main.py`)
- [ ] P10-DB-1: `player:stats:{puuid}`, `player:champions:{puuid}`, `player:roles:{puuid}`, `player:cursor:{puuid}` hashes have no TTL → unbounded growth for inactive players. Add 30-day EXPIRE in analyzer pipeline. (`analyzer/main.py`)
- [ ] P10-DB-3 (DEBATED — see below): Replace `system:priority_count` INCR/DECR with SCAN-based `_is_idle()` check → eliminates TTL-expiry counter drift. Trade-off: O(N) SCAN vs O(1) counter with known leak. Decision after debate.
- [ ] P10-DD-3/PM-01/QA-3: Dashboard `/` has no nav link in `_NAV_ITEMS` → can't navigate back to dashboard from other pages. Add `("/", "Dashboard")` as first entry. (`ui/main.py`)
- [ ] P10-CW-7/DD-9: No Riot Games attribution footer — required by Riot ToS. Add `<footer>` to `_page()` with attribution text. (`ui/main.py`)
- [ ] P10-DD-7: Dashboard region selector shows only 4 regions vs stats page showing all 15. Use full `_REGIONS` list on dashboard form. (`ui/main.py`)
- [ ] P10-RD-9: Players table renders full ISO timestamps in Seeded column → forces 550px+ min-width on mobile. Truncate to date-only (`seeded_at[:10]`). (`ui/main.py`)
- [ ] P10-RD-10: Match history table cells wrap inside `.table-scroll` container. Add `white-space: nowrap` to `td, th` inside scroll tables. (`ui/main.py`)
- [ ] P10-RD-11: `#pause-btn` CSS `padding: 0.4rem 1rem` overrides global `min-height: 44px` → ~25px touch target. Add `min-height: 44px` to `#pause-btn` rule. (`ui/main.py`)
- [ ] P10-RD-12: Duplicate `.log-line` CSS rules — second rule unconditionally overrides first. Merge into single mobile-first declaration. (`ui/main.py`)
- [ ] P10-GD-1: `cmd_stats` outputs raw Redis key dump in random sort order with unformatted values (e.g. `win_rate: 0.5374` not `53.7%`). Add fixed key ordering, aligned columns, formatted values. (`admin/main.py`)
- [ ] P10-GD-2: `cmd_dlq_list` always outputs raw JSON blobs — no plain-text table mode. Default to human-readable table; add `--json` for machine-readable. (`admin/main.py`)
- [ ] P10-GD-3: Admin CLI error messages use 3 different casing conventions (`error:`, `Error:`, `Warning:`). Standardize to `[ERROR]` / `[WARN]` / `[OK]` prefixes. (`admin/main.py`)
- [ ] P10-GD-4: `just streams` and `just status` duplicate stream block code with different label alignments; `just streams` missing `system:halted`. Factor into shared helper. (`Justfile`)
- [ ] P10-DX-3: Testing standards doc mandates 0.5s timeout but all pyproject.toml files set 10s. Align doc to 10s or vice versa. (`docs/standards/03-testing-standards.md`)
- [ ] P10-DX-4: CI lints only `src/` while local `just lint` lints `.` (src + tests). Align to same scope. (`.github/workflows/ci.yml`)
- [ ] P10-DD-4: Admin CLI status output uses plain lowercase `error:` prefix with no visual distinction between error/warning/success. Introduce `[OK]`/`[WARN]`/`[ERROR]` scheme. (`admin/main.py`)
- [ ] P10-DD-5: `.badge--error` uses hardcoded `#cc3333` instead of `var(--color-error)`. Add `--color-error-bg` token. (`ui/main.py`)

### Low / Polish

- [ ] P10-UX-1/WD/PM-05: Champion icons from Data Dragon CDN — add `_get_ddragon_version()`, `_get_champion_map()` (cached in Redis 24h), `_champion_icon_html()` helper; render 32px icon in match history table. (`ui/main.py`)
- [ ] P10-ARC-4/OPT-2: Adaptive `wait_for_token()` backoff — return remaining wait_ms from Lua script on denial; sleep until next slot instead of fixed 50ms polling. (`riot_api.py`)
- [ ] P10-DX-1: `just setup` does not create venv or install dev deps — developer must do this manually with no documented steps. Add `just venv` recipe. (`Justfile`)
- [ ] P10-RD-13: Pagination links have no padding/min-height → ~20px touch target. Add `.page-link` CSS class with `min-height: 44px`. (`ui/main.py`)
- [ ] P10-RD-14: Player filter `#player-search` input has no `width: 100%` on mobile. Add CSS rule. (`ui/main.py`)
- [ ] P10-GD-5: `just status` container health uses raw `compose ps` output with no healthy/unhealthy summary line. Post-process with `[OK]`/`[ERR]` prefix. (`Justfile`)
- [ ] P10-GD-6: Destructive admin ops (`dlq clear --all`, `replay --all`) act immediately with no preflight scope line. Add "About to act on N entries…" before loop. (`admin/main.py`)
- [ ] P10-DD-8: Route handlers scatter inline `style` attributes instead of using CSS classes. Extract to named classes. (`ui/main.py`)
- [ ] P10-DD-11: Dashboard and streams page both render stream tables with different HTML structure. Extract shared `_render_stream_table()` helper. (`ui/main.py`)
- [ ] P10-DD-13: Empty state inconsistency — logs/stats pages use plain `<p>` while players/DLQ use `_empty_state()` helper. Use `_empty_state()` consistently. (`ui/main.py`)
- [ ] P10-DX-20: README hardcodes test count "541 unit tests" — stale. Update to "866 unit tests + 61 contract tests". (`README.md`)

### Debated Items

#### P10-DB-3: SCAN-based priority_count vs INCR/DECR counter

**Proposal:** Replace `system:priority_count` INCR/DECR with `SCAN player:priority:* COUNT 1` in `_is_idle()` to eliminate TTL-expiry counter drift (P10-FV-1 confirmed: no Lua-only fix).

**For:** Eliminates permanent +1 leak per TTL expiry. SCAN is O(N) but N=1 in common case (at most a handful of priority keys exist at once). Atomic by nature — no counter to go out of sync.

**Against:** O(N) SCAN is not O(1); could be slow if keyspace is large. Adds Redis cursor management. Counter approach is O(1) and the leak is bounded (at most +1 per player per pipeline run — negligible in practice).

**Decision (supermajority needed):** Run debate with architect + database + formal-verifier agents. Tentatively defer to Phase 11 unless debate resolves in favor of SCAN.

#### Champion icons scope

**Proposal (P10-UX-1/WD/PM-05):** Add Data Dragon CDN champion icon rendering in match history. Full spec from web-designer: `_get_ddragon_version()` (cached), `_get_champion_map()` (Redis `ddragon:champion_map`, 24h TTL), CSS `.champion-icon`.

**For:** High visual value. No self-hosting needed. Data Dragon is official Riot CDN.

**Against:** Requires 2 extra HTTP calls per page render (version + champion map), plus Redis cache. Adds complexity. Needs fallback for API downtime.

**Decision:** Include in Phase 10 with Redis cache guard (graceful degradation if CDN unreachable).

---

## Phase 11 — "APEX" (Orchestrator Cycle 5, 20-agent review)

### Debated / Rejected

- [x] P11-PM-02-A: `except A, B:` syntax across 9 files — REJECTED. This is valid Python 3.14 syntax (PEP 758 "except without parentheses"). Tests pass. No change needed.
- [x] P11-ARC-1: `resolve_puuid()` bypasses rate limiter — REJECTED. `resolve_puuid()` calls `RiotClient._get()` which waits for token. Rate limiting is handled by callers. No change needed.

### Critical / Implemented

- [x] P11-PM-03-A: Discovery `_is_idle()` crashes with `int(None)` when Redis returns `lag: None` for empty streams (`discovery/main.py:81`). Fix: `int(g.get("lag") or 0)` and `int(g.get("pending") or 0)`.
- [x] P11-DEV-2: Worker Dockerfile HEALTHCHECK uses `print()` which always exits 0 — Docker never sees unhealthy containers. Fix: `sys.exit(0 if ... else 1)` in all 7 worker Dockerfiles.
- [x] P11-DX-4: Pre-commit `ruff format` auto-modifies files instead of failing. Fix: add `--check` flag.
- [x] P11-DK-3: 12 env vars consumed by code are missing from `.env.example`. Fix: add RATE_LIMIT_SHORT_WINDOW_S, RATE_LIMIT_LONG_WINDOW_S, PRIORITY_KEY_TTL, REDIS_SOCKET_TIMEOUT, REDIS_CONNECT_TIMEOUT, MATCH_DATA_TTL_SECONDS, MAX_DISCOVER_PLAYERS, PLAYER_MATCHES_MAX, MAX_HANDLER_RETRIES, LOG_LEVEL, LOG_DIR to .env.example.
- [x] P11-TST-3: Admin `_format_stat_value` renders "nan%" and "inf%" for non-finite floats. Fix: `math.isfinite()` guard.
- [x] P11-GD-4/5: `cmd_recalc_priority`, `cmd_recalc_players`, and DLQ empty-state use bare `print()` instead of `[OK]`/`[--]` prefixes. Fix: use `_print_ok()` and new `_print_info()`.
- [x] P11-GD-12: Log formatter uses `datetime.now()` (format-time) instead of `record.created` (emit-time); microsecond precision adds noise. Fix: ms precision with Z suffix.
- [x] P11-RD-1: `#player-search` has no `width: 100%` on mobile — Phase 10 fix was described but not applied to CSS. Fix: add rule.
- [x] P11-RD-2: Footer uses inline style, blocking responsive padding. Fix: `.site-footer` CSS class.
- [x] P11-RD-9: `body { margin: 2rem auto }` wastes 64px on 320px screens. Fix: `1rem auto` on mobile.
- [x] P11-DD-5: Log badge colors use hardcoded hex (`#c00`, `#e33`, `#e80`, `#555`) instead of CSS variables. Fix: map to `--color-error`, `--color-warning`, `--color-muted`.
- [x] P11-DD-13: Match history load shows plain "Loading..." text with no spinner. Fix: use `.spinner` element.
- [x] P11-DD-17: Streams table depth column lacks `.text-right` alignment, unlike dashboard's table. Fix: add `class="text-right"` to `<th>` and `<td>`.
- [x] P11-RD-15: `#streams-pause-btn.paused` has no CSS — pause state invisible. Fix: extend `#pause-btn.paused` rule.
- [x] P11-DX-6: `hypothesis` missing from 9 of 11 services' dev deps. Fix: add to all pyproject.toml.
- [x] P11-GD-14: `system:halted` mixed into stream depths table as a fake depth. Fix: separate labeled line.

### High (Not yet implemented)

- [ ] P11-DB-2: `player:{puuid}` hashes have no TTL — unbounded growth for inactive players. Add 30-day EXPIRE in seed, crawler, discovery, parser.
- [ ] P11-RD-4: Card CTA links (`<a>`) have no 44px touch target. Fix: `.card-link` CSS class.
- [ ] P11-RD-8: `.form-inline label` 12px text on 16px input. Fix: mobile font-size override.
- [ ] P11-RD-10: `td, th { padding: 0.4rem 0.8rem }` no mobile reduction. Fix: mobile override.
- [ ] P11-RD-16: Blanket `.table-scroll td { white-space: nowrap }` forces 80-char DLQ payload cells non-wrapping. Fix: scope to `.col-nowrap` class.
- [ ] P11-DD-8: CLI uses `[OK]`/`[ERROR]` text — design director prefers checkmark/x-mark symbols. DEFERRED (debate needed: ASCII-safe vs Unicode).
- [ ] P11-DD-10: Tables missing `<thead>`/`<tbody>` semantic markup on 5 of 6 table instances.
- [ ] P11-DD-11: Empty state uses `_empty_state()` only on 2 of 8 empty-state scenarios.
- [ ] P11-DD-15: Pagination inconsistent between Players and DLQ pages (total vs no-total).
- [ ] P11-DX-2: No `just venv` recipe. Fix: add recipe to create .venv and install all dev deps.
- [ ] P11-DX-11: 12 env vars bypass `Config` pydantic-settings class — no startup validation. Fix: migrate to Config.
- [ ] P11-DX-18: CI doesn't cache pip dependencies. Fix: add `cache: 'pip'` to setup-python steps.
- [ ] P11-GD-1/2/3: DLQ table has no top/bottom border; Attempts column alignment broken; separator widths mismatch. Fix: rewrite `_format_dlq_table` with proper box-drawing borders.
- [ ] P11-GD-9/10: `just status` mixes `===`, `---`, `---` separator styles. Fix: standardize.
- [ ] P11-GD-11: `--json` flag is global but help says "supported: stats" only. Fix: move to subparsers or update help.

### Champion Icons (P10-UX-1, deferred from Phase 10)

- [ ] Add `_get_ddragon_version()`, `_get_champion_map()` (Redis cache 24h), `_champion_icon_html()` helper. Render 32px icon in match history table champion column.

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
