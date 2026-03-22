# TODO — Improvement Proposals (All 21 Agents)

Phase 10 "ILLUMINATE", Phase 11 "APEX".
920+ unit tests + contract tests. 19-20 agent review cycle per phase.

---

## ✅ Completed — Orchestrator Cycle 2 (Phase 9)

All B1–B15, C1–C2 items resolved. All I2-C1 through I2-M7 items resolved. See CLAUDE.md for detail.

---

## Phase 10 — "ILLUMINATE" (Orchestrator Cycle 4, 19-agent review)

### Critical

- [x] P10-CR-1: Rate limiter configurable windows — `RATE_LIMIT_SHORT_WINDOW_S` / `RATE_LIMIT_LONG_WINDOW_S` env vars added (`riot_api.py`)
- [x] P10-CR-7: Discovery `_resolve_names` now calls `wait_for_token()` before Riot API calls (`discovery/main.py`)
- [x] P10-QA-1: UI `_auto_seed_player()` calls `set_priority()` before `publish()` — fixed in Phase 14 (`ui/main.py`)
- [x] P10-QA-2: UI `_auto_seed_player()` writes to `players:all` sorted set — fixed in Phase 14 (`ui/main.py`)

### High

- [x] P10-DB-4/FV-2: Rate limiter Lua script passes limit keys via KEYS array — CROSSSLOT fixed (`riot_api.py`)
- [x] P10-FV-6: Corrupt messages in `consume()` XAUTOCLAIM path are archived to `stream:dlq:archive` before ACK — audit trail preserved. (`streams.py`)
- [x] P10-SEC-4: Security headers (X-Content-Type-Options, X-Frame-Options, Referrer-Policy, CSP) — fixed in Phase 12 (`ui/main.py`)
- [x] P10-DX-2/R7: All Dockerfiles updated to `python:3.14-slim`; pyproject.toml target-version updated — fixed in Phase 12
- [x] P10-RD-5: `.form-inline` flex targets label children at 768px breakpoint — fixed (`ui/main.py`)
- [x] P10-RD-6: Sort control links have `min-height: 44px` — fixed (`ui/main.py`)
- [x] P10-RD-7: DLQ replay button uses `btn-sm` class with proper touch target — fixed (`ui/main.py`)
- [x] P10-RD-8: `.table-scroll td, th { white-space: nowrap }` — fixed (`ui/main.py`)

### Medium

- [x] P10-CR-6: `player:matches` capped at PLAYER_MATCHES_MAX (500) via ZREMRANGEBYRANK in parser — fixed in Phase 13
- [x] P10-DB-1: player stat keys have 30-day EXPIRE in analyzer pipeline — fixed in Phase 13
- [x] P10-DB-3: Replaced counter with SCAN-based `has_priority_players()` — fixed in Phase 14
- [x] P10-DD-3/PM-01/QA-3: Dashboard nav link `("/", "Dashboard")` in `_NAV_ITEMS` — fixed
- [x] P10-CW-7/DD-9: Riot Games attribution footer in `_page()` — fixed in Phase 12
- [x] P10-DD-7: Dashboard uses full `_REGIONS` list — verified correct in Phase 14
- [x] P10-RD-9: Players table timestamps truncated to date-only — fixed
- [x] P10-RD-10: `.table-scroll` has white-space nowrap on cells — fixed
- [x] P10-RD-11: `#pause-btn` has `min-height: 44px` — fixed
- [x] P10-RD-12: `.log-line` CSS merged into mobile-first declaration — fixed
- [x] P10-GD-1: `cmd_stats` uses `_format_stats_output()` with ordered keys, aligned columns, formatted values — fixed
- [x] P10-GD-2: `cmd_dlq_list` defaults to human-readable table; `--json` for machine output — fixed
- [x] P10-GD-3: Admin CLI uses standardized `[OK]`/`[ERROR]`/`[--]` prefixes — fixed
- [x] P10-GD-4: `just streams` uses shared `_stream_depths` recipe with `system:halted` — fixed
- [x] P10-DX-3: Testing standards aligned with pyproject.toml timeout config — fixed
- [x] P10-DX-4: CI and local lint scope aligned — fixed
- [x] P10-DD-4: Admin CLI uses `_print_ok()`/`_print_error()`/`_print_info()` — fixed (same as GD-3)
- [x] P10-DD-5: `.badge--error` uses `var(--color-error-bg)` token — fixed

### Low / Polish

- [x] P10-UX-1/WD/PM-05: Champion icons — `_get_ddragon_version()`, `_champion_icon_html()` with Redis cache — fixed in Phase 12
- [ ] P10-ARC-4/OPT-2: Adaptive `wait_for_token()` backoff — return remaining wait_ms from Lua script on denial; sleep until next slot instead of fixed 50ms polling. (`riot_api.py`)
- [x] P10-DX-1: `just venv` recipe added to Justfile — fixed
- [x] P10-RD-13: `.page-link` CSS class with `min-height: 44px` — fixed
- [x] P10-RD-14: `#player-search` has `width: 100%` on mobile — fixed in Phase 11
- [x] P10-GD-5: `just status` shows container health via `compose ps` — improved
- [x] P10-GD-6: Destructive admin ops now have confirmation prompts (`--yes` to skip) — fixed
- [x] P10-DD-8: Inline styles migrated to CSS classes — mostly fixed
- [x] P10-DD-11: Dashboard and streams page stream tables share `_stream_depths` helper — fixed
- [x] P10-DD-13: Empty states consistently use `_empty_state()` — fixed
- [x] P10-DX-20: README test count updated to 987 unit tests + 44 contract tests — fixed

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

- [x] P11-DB-2: `player:{puuid}` hashes have 30-day EXPIRE in seed, crawler, discovery, parser — fixed in Phase 13
- [x] P11-RD-4: Card CTA links have 44px touch target via `.card a` CSS rule — fixed
- [x] P11-RD-8: `.form-inline label` mobile font-size override — fixed
- [x] P11-RD-10: `td, th` mobile padding reduction — fixed
- [x] P11-RD-16: DLQ payload cells use `.cell-wrap` class for wrapping — fixed
- [ ] P11-DD-8: CLI uses `[OK]`/`[ERROR]` text — design director prefers checkmark/x-mark symbols. DEFERRED (debate: ASCII-safe vs Unicode).
- [x] P11-DD-10: All tables use `<thead>`/`<tbody>` semantic markup — fixed
- [x] P11-DD-11: Empty states consistently use `_empty_state()` — fixed
- [x] P11-DD-15: Both pages show totals; DLQ uses cursor-based pagination — fixed
- [x] P11-DX-2: `just venv` recipe added — fixed
- [x] P11-DX-11: Env vars migrated to pydantic `Config` class — fixed
- [x] P11-DX-18: CI pip cache via `cache: 'pip'` on all setup-python steps — fixed
- [x] P11-GD-1/2/3: DLQ table uses proper box-drawing borders and aligned columns — fixed
- [x] P11-GD-9/10: `just status` separator styles — standardized
- [x] P11-GD-11: `--json` help text updated to "supported: stats, dlq list" — fixed

### Champion Icons (P10-UX-1, deferred from Phase 10)

- [x] Champion icons implemented: `_get_ddragon_version()`, `_champion_icon_html()` with Redis cache — fixed in Phase 12

---

## Phase 12 — "ZENITH" (Orchestrator Cycle 6, 20-agent review)

### Implemented

- [x] Champion icons (P10-UX-1): `_get_ddragon_version()`, `_get_champion_map()`, `_champion_icon_html()` — 32px icons in match history
- [x] P10-SEC-4: Security headers middleware (`X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`)
- [x] P10-DX-2: Python 3.12 → 3.14 in all Dockerfiles and pyproject.toml target-version
- [x] P10-CW-7: Riot Games attribution footer in `_page()`
- [x] CSP headers with nonce for inline scripts
- [x] Async log I/O (`asyncio.to_thread`)
- [x] TTLs on `match:{match_id}` and `participant:{match_id}:{puuid}` hashes (I2-C3 / I2-H12 follow-ups)
- [x] ARIA improvements on streams/logs page

---

## Phase 13 — "SUMMIT" (Orchestrator Cycle 7, 20-agent review)

### Explicitly REJECTED

- P13-WD-14/DD-16/ARC-7/QA-1: `except A, B:` — PERMANENTLY REJECTED (valid PEP 758, Phase 11 decision stands)
- P13-FV-1: XAUTOCLAIM 60s < lock TTL 300s — REJECTED (Worker B's `nx=True` SET correctly fails; Worker B discards duplicate and ACKs; Worker A completes normally)

### Critical

- [x] P13-DBG-3: `_ensured` WeakKeyDictionary cache not cleared on NOGROUP ResponseError → permanent NOGROUP loop after Redis restart (`streams.py`)

### High

- [x] P13-DEV-3: `REDIS_PASSWORD` missing from `.env.example` (`.env.example`)
- [x] P13-UX-6: Streams/logs fetch errors silently swallowed — no inline error state shown to user (`ui/main.py` JS)

### Medium

- [x] P13-UX-11: "Load more" replaces match history container instead of appending rows (`ui/main.py`)
- [x] P13-OPT-6: Parser sequential post-write RTTs (ZREMRANGEBYRANK + expire per participant) → pipeline them (`parser/main.py`)
- [x] P13-OPT-7: Parser sequential analyze `publish()` calls → batch into pipeline (`parser/main.py`)
- [x] P11-DB-2: `player:{puuid}` hashes have no TTL in discovery `_promote_batch` (`discovery/main.py`)
- [x] P13-INT-4: Raw blob 24h TTL inconsistent with `MATCH_DATA_TTL_SECONDS` (7d) — configurable via `RAW_STORE_TTL_SECONDS` env var (`raw_store.py`, `.env.example`)

### Low

- [x] P13-CR-4: Recovery `_archive()` sets `match:{match_id}` with no TTL — unbounded growth for archived matches (`recovery/main.py`)

---

## Phase 14 — "HORIZON" (Orchestrator Cycle 8, 20-agent review)

### Rejected

- P14-DBG-5: `except A, B:` — PERMANENTLY REJECTED (valid PEP 758, Phase 11 decision stands)
- P14-FV-6: XAUTOCLAIM cursor not persisted — DEFERRED (only matters during large-PEL recovery; O(N) scan is bounded by small normal PELs)

### Critical

- [x] P14-ARC-2/DBG-1: `wait_for_token()` infinite loop — add `max_wait_s=60` timeout, raise `TimeoutError` after deadline (`riot_api.py`)
- [x] P14-DBG-2: Lua rate-limit script uses limit=0 as real limit → permanent deadlock; add floor guard: if `limit < 1`, use default (`rate_limiter.py`)
- [x] P14-DBG-4: Recovery `_requeue_delayed` ZADD + XACK not atomic → duplicate delivery on crash; wrap in MULTI/EXEC pipeline (`recovery/main.py`)
- [x] P14-CR-1: Fetcher `match:{match_id}` hashes have no TTL → unbounded Redis growth; add `EXPIRE` with `MATCH_DATA_TTL_SECONDS` after HSET (`fetcher/main.py`)
- [x] P14-CR-6: `_is_idle` catches ALL `ResponseError` from `xinfo_groups` → masks real errors; narrow to only `NOGROUP` (`discovery/main.py`)

### High

- [x] P14-CR-4/DEV-4: Unhandled `ValueError` in crawler `datetime.fromisoformat()` — wrap in try/except, treat as stale and skip (`crawler/main.py`)
- [x] P14-FV-2: Recovery busy-spins on PEL when `system:halted` — add `await asyncio.sleep(5)` when no messages ACK'd (`recovery/main.py`)
- [x] P14-FV-7: Crawler `NotFoundError` (404) handler never calls `clear_priority()` → orphaned priority key blocks Discovery for 24h (`crawler/main.py`)
- [x] P14-PM-1: `_auto_seed_player()` order verified correct (set_priority before publish) (`ui/main.py`)
- [x] P14-PM-2: `_auto_seed_player()` never writes to `players:all` sorted set → auto-seeded players invisible in /players (`ui/main.py`)
- [x] P14-PM-3: Dashboard seed form already used full `_REGIONS` list — verified correct (`ui/main.py`)
- [x] P14-UX-2: JS `fetch()` never checks `r.ok` — 4xx/5xx response bodies silently injected as HTML; add `if (!r.ok) throw` before `.text()` (`ui/main.py`)
- [x] P14-TST-3: `test_http_5xx_requeued` never verifies `dlq_attempts=1` or `source_stream` — assertions added (`lol-pipeline-recovery/tests/unit/test_main.py`)

### Medium

- [x] P14-UX-1: Auto-seed success uses `css_class="warning"` (yellow) instead of `"success"` (green) (`ui/main.py`)
- [x] P14-UX-3: DLQ replay failure silently returns 303 redirect with no error shown — return error message inline (`ui/main.py`)
- [x] P14-UX-5: Active nav link uses exact path match → subpages show no active state; use `path.startswith(href)` (`ui/main.py`)
- [x] P14-UX-10: Streams/logs auto-refresh error prepends duplicate `<p>` on each poll; clear old error before prepending (`ui/main.py`)
- [x] P14-OPT-1: Analyzer 4 sequential `EXPIRE` calls → pipeline them (`analyzer/main.py`)
- [x] P14-OPT-4: Crawler sequential `publish()` per match ID → batch all XADDs into one pipeline (`crawler/main.py`)
- [x] P14-DB-1: `players:all` sorted set grows unbounded → cap at 50K with `ZREMRANGEBYRANK` after ZADD (`seed/main.py`, `ui/main.py`, `discovery/main.py`)
- [x] P14-SEC-10: `/players` negative `page` parameter not clamped → clamp to `max(0, page)` (`ui/main.py`)
- [x] P14-DX-1: `MAX_STREAM_BACKLOG` missing from `.env.example` — added entry with comment (`env.example`)
- [x] P14-FV-3/DBG-3: Delay scheduler `_member_failures` dict never resets on circuit expiry → counter persists, circuit re-trips on first next failure; reset counter when circuit clears (`delay_scheduler/main.py`)
- [x] P14-CW-2/GD-5: `--json` flag help text says "supported: stats" but `dlq list` also supports it — fixed help string (`admin/main.py`)
- [x] P14-WD-2: `btn.className = paused ? 'paused' : ''` clobbers existing classes — use `classList.toggle('paused')` (`ui/main.py`)
- [x] P14-TST-1: `test_lock_stolen_logs_warning` has no assertion — added `caplog` + ACK assertion (`lol-pipeline-analyzer/tests/unit/test_main.py`)
- [x] P14-TST-2: `test_invalidate_ensured_no_error_when_not_cached` has no assertion — added assertion on `_ensured` state (`lol-pipeline-common/tests/unit/test_streams.py`)
- [x] P14-TST-6: Recovery tests share `match_id="NA1_123"` across tests — use unique IDs per test (`lol-pipeline-recovery/tests/unit/test_main.py`)

### Low / Polish

- [x] P14-CW-1/4: Halt banner shows raw Redis key `(system:halted is set)` — stripped parenthetical; added actionable text (`ui/main.py`)
- [x] P14-CW-10: Streams page `<h2>` says "Stream Depths" — unified to "Streams" (`ui/main.py`)
- [x] P14-DX-2: `docs/guides/01-local-dev.md` says `just lint` runs `--fix` — fixed doc (`docs/guides/01-local-dev.md`)
- [x] P14-DOC-1: `07-containers.md` shows `python:3.12-slim` throughout — updated to 3.14 (`docs/architecture/07-containers.md`)
- [x] P14-DOC-2: README unit/contract test count stale — updated to 963 unit / 44 contract (`README.md`)
- [x] P14-DOC-3: `03-streams.md` envelope table missing `priority` field — added row (`docs/architecture/03-streams.md`)
- [x] P14-DOC-6: `02-services.md` admin command table missing `recalc-players` — added row (`docs/architecture/02-services.md`)
- [x] P14-DOC-11: `docs/services/discovery.md` references nonexistent `just admin unhalt` — fixed to `just admin system-resume` (`docs/services/discovery.md`)

### Deferred

- P14-SEC-2: CSRF protection for `/dlq/replay/{id}` — needs token infrastructure, defer to Phase 15
- P14-ARC-4: Migrate 5 config values to pydantic `Config` — large refactor, defer
- P14-FV-1: Analyzer cursor stalls on expired participant data — complex edge case, defer
- P14-FV-4: Analyzer premature priority clear on partial match data — edge case requiring data loss, defer
- P14-FV-5: Parser analyze pipeline partial-XADD + raw-blob-expiry compound failure — extremely rare, defer
- P14-FV-8: Recovery 404 discards with no audit trail — audit gap only, defer
- P14-PM-4/PM-6: `cmd_dlq_list` table mode + `dlq clear` preflight scope line — feature, defer
- P14-UX-4/12: DLQ pagination total count + cursor-based pagination — refactor, defer
- P14-UX-6: Dashboard double-queries `stream:dlq` (redundant pipeline call) — cosmetic, defer
- P14-WD/UX ARIA: nav aria-label, aria-current, role="alert", form label pairing — accessibility sprint, Phase 16
- P14-RD-*: Responsive CSS improvements — defer to Phase 16 UI sprint
- P14-DD-*: Design system cleanup (rgba tokens, h2/h3 rules, spacing scale) — defer
- P14-GD-*: CLI output formatting (DLQ table borders, stats JSON, progress signals) — defer
- P14-DX-4-13: DevEx improvements (conftest.py, pre-commit mypy, parallel check) — defer
- P14-DOC-4/5/7/8/12-18: Large env var table updates, storage schema, deployment docs — defer
- P14-DBG-6: rate_limiter stored-limit keys not scoped to key_prefix — defer

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

## Complexity Review Findings

Reviewed 2026-03-21. Only findings with >=80% confidence of real improvement.

### CR-1: Analyzer `_update_champion_stats` — sequential EVAL per match (warning)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-analyzer/src/lol_analyzer/main.py:208-253`

**Issue:** `_update_champion_stats` calls `r.eval(_UPDATE_CHAMPION_LUA, ...)` once per match
in a sequential Python loop. Each EVAL is a full Redis round-trip. For a player with M new
matches, this is M round-trips with no pipelining.

**Current complexity:** O(M) Redis round-trips where M = new matches for this player.
- At M=10: 10 round-trips (~5ms each = 50ms)
- At M=50: 50 round-trips (~250ms)
- At M=200: 200 round-trips (~1s)

**Why it matters:** The `_process_matches` function on line 158-205 has a legitimate reason
to be sequential (lock ownership check per-match via Lua return value). But
`_update_champion_stats` has no such dependency -- each Lua call is independent and could
be batched.

**Fix:** Use `r.pipeline(transaction=False)` to batch all `_UPDATE_CHAMPION_LUA` EVAL calls
into a single round-trip. The Lua script is self-contained per key, so pipelining is safe:

```python
async with r.pipeline(transaction=False) as pipe:
    for (match_id, score), p, meta in zip(...):
        # ... validation ...
        pipe.eval(_UPDATE_CHAMPION_LUA, 3, stats_key, index_key, "patch:list", ...)
    await pipe.execute()
```

**Priority:** warning -- linear round-trips, constant factor improvement.

---

### CR-2: RawStore.set calls `_exists_in_bundles` on every new write (warning)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/raw_store.py:144`

**Issue:** On line 144, after a successful Redis SET NX (meaning this IS a new match),
the code calls `_exists_in_bundles(match_id)` which scans ALL JSONL bundle files
line-by-line and ALL compressed `.jsonl.zst` bundles for this match ID. This is a disk
I/O operation on the hot write path.

**Current complexity:** O(B * L) per write, where B = number of bundle files,
L = average lines per bundle.
- At 1 month (1 bundle, 10K lines): 10K string comparisons per write
- At 6 months (6 bundles, 60K total lines): 60K string comparisons per write
- At 12 months: 120K+ string comparisons per write

This runs on every single match fetched, inside `asyncio.to_thread` so it blocks a
thread pool thread for the entire scan duration.

**Why it matters:** The purpose is to prevent duplicate disk writes after a Redis restart.
But when `was_set` is True (SET NX succeeded), it means the key was NOT in Redis, which
after a Redis restart would be the case for ALL matches. In that scenario, every write
pays the full bundle scan cost -- the worst case is the common case.

**Fix:** Replace the linear scan with a more targeted check. Options:
1. **Check only the current month's bundle** (the only one this write would target):
   ```python
   if not was_set or await asyncio.to_thread(self._search_bundle_file, bp, match_id):
       return
   ```
   This changes the scope from "all bundles across all months" to "just the current
   month's bundle", reducing the scan from O(B*L) to O(L_current).

2. **Maintain a small bloom filter or set of recently-written match IDs** on the
   RawStore instance (bounded, in-memory, process-local). Since writes are append-only,
   a match ID written to disk will always be in the current bundle file.

**Priority:** warning -- I/O-bound linear scan on every write, worsens with data age.

---

### CR-3: RiotClient._get writes 2 Redis SETs on every successful API call (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/riot_api.py:274-277`

**Issue:** On every successful API response, `_get` unconditionally writes both
`ratelimit:limits:short` and `ratelimit:limits:long` via two sequential `r.set()` calls.
At 20 API calls/second, this is 40 extra Redis round-trips/second writing the same
values repeatedly.

**Current:** 2 Redis SET round-trips per API call = 40/s at full throughput.

**Fix:** Two options, both simple:
1. **Cache the last-written limits in-process** and only SET when the value changes:
   ```python
   if limits and limits != self._cached_limits:
       self._cached_limits = limits
       async with self._r.pipeline(transaction=False) as pipe:
           pipe.set("ratelimit:limits:short", str(limits[0]), ex=_RATE_LIMIT_KEY_TTL)
           pipe.set("ratelimit:limits:long", str(limits[1]), ex=_RATE_LIMIT_KEY_TTL)
           await pipe.execute()
   ```
2. **At minimum, pipeline the two SETs** into one round-trip (1 RTT instead of 2).

**Priority:** nit -- constant factor, the values rarely change in practice.

---

### CR-4: Fetcher `_fetch_match` has 4 sequential Redis calls that could be pipelined (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-fetcher/src/lol_fetcher/main.py:105-113`

**Issue:** After storing the raw blob, lines 105-113 execute 4 sequential Redis operations:
1. `r.hset("match:{id}", mapping={"status": "fetched"})`
2. `r.expire("match:{id}", ttl)`
3. `r.sadd("seen:matches", match_id)`
4. `r.ttl("seen:matches")` (+ conditional `r.expire`)

These are independent operations that could be batched in a single pipeline round-trip.

**Current:** 4-5 Redis round-trips per match fetch.

**Fix:**
```python
async with r.pipeline(transaction=False) as pipe:
    pipe.hset(f"match:{match_id}", mapping={"status": "fetched"})
    pipe.expire(f"match:{match_id}", cfg.match_data_ttl_seconds)
    pipe.sadd("seen:matches", match_id)
    pipe.ttl("seen:matches")
    results = await pipe.execute()
if results[3] < 0:
    await r.expire("seen:matches", cfg.seen_matches_ttl_seconds)
```

**Priority:** nit -- constant factor (saves ~15-20ms per fetch at typical latency).

---

### CR-5: `has_priority_players` uses SCAN on every streams page refresh (warning)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-common/src/lol_pipeline/priority.py:66-78`
**Called from:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-ui/src/lol_ui/main.py:1507`

**Issue:** `has_priority_players` does a full SCAN of the Redis keyspace matching
`player:priority:*` on every call. The `/streams` page auto-refreshes every 5 seconds
via `/streams/fragment`, and each refresh calls `has_priority_players`. SCAN iterates
the entire keyspace (not just matching keys) in O(N) total over multiple calls, where
N = total keys in Redis.

**Current complexity:** O(N) per call where N = total Redis keys.
- At N=1K keys: trivial
- At N=100K keys (100K matches + 10K players + metadata): multiple SCAN iterations,
  each touching ~100 keys but needing ceil(N/100) iterations to complete
- At N=1M keys: ~10K SCAN iterations per check

The streams page does this every 5 seconds per connected browser tab.

The discovery service also calls this on every poll cycle (line 60 of discovery/main.py).

**Fix:** Replace SCAN-based detection with a Redis SET that tracks active priority
PUUIDs. `set_priority` would SADD the PUUID; `clear_priority` would SREM it;
`has_priority_players` becomes `SCARD("player:priority:active") > 0` -- O(1).

The TTL-expiry concern (mentioned in the docstring) can be handled by having
`clear_priority` also SREM, and periodically reconciling the set against actual keys
(e.g., once per minute in discovery, not on every call).

**Priority:** warning -- O(N) SCAN on a timer in the UI, worsens as keyspace grows.

---

### CR-6: Crawler `_compute_activity_rate` makes 3 sequential Redis calls (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-crawler/src/lol_crawler/main.py:230-257`

**Issue:** `_compute_activity_rate` does 3 sequential Redis calls:
1. `r.zrange("player:matches:{puuid}", 0, 0, withscores=True)` (get first match)
2. `r.zcard("player:matches:{puuid}")` (get count)
3. `r.hset("player:{puuid}", ...)` (write result -- actually 2 HSETs on lines 246, 255)

The ZRANGE and ZCARD operate on the same key and could be pipelined. The two HSET calls
could also be combined into a single HSET with a mapping.

**Fix:** Pipeline ZRANGE + ZCARD, and merge the two HSET calls:
```python
async with r.pipeline(transaction=False) as pipe:
    pipe.zrange(f"player:matches:{puuid}", 0, 0, withscores=True)
    pipe.zcard(f"player:matches:{puuid}")
    first_match, total_matches = await pipe.execute()
# ... compute rate and cooldown ...
await r.hset(f"player:{puuid}", mapping={
    "activity_rate": f"{rate:.2f}",
    "recrawl_after": recrawl_after,
})
```

**Priority:** nit -- constant factor, runs once per crawl completion.

---

### CR-7: Crawler `_crawl_player` makes 2 sequential Redis calls for rank storage (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-crawler/src/lol_crawler/main.py:217-224`

**Issue:** In `_fetch_rank`, the HSET for rank data (line 217) and EXPIRE (line 224)
are separate round-trips, and the summoner level HSET (line 209) is yet another one.

**Fix:** Pipeline all rank-related writes.

**Priority:** nit -- constant factor.

---

### CR-8: DLQ summary page reads up to 500 entries then re-reads total count (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-ui/src/lol_ui/main.py:1603-1689`

**Issue:** `_dlq_summary_html` calls `r.xlen("stream:dlq")` and
`r.xlen("stream:dlq:archive")` as 2 separate calls (lines 1605-1606), then does
`r.xrange("stream:dlq", ..., count=500)` as a third. These could be pipelined.
Additionally, when the DLQ page handler calls `_dlq_summary_html` (line 1698),
it then calls `r.xlen("stream:dlq")` AGAIN on line 1711 -- a redundant read.

**Fix:** Combine the XLEN calls into the existing pipeline in `_dlq_summary_html`,
and pass the `dlq_depth` to the caller to avoid the redundant XLEN.

**Priority:** nit -- 1 redundant Redis call per DLQ page load.

---

### CR-9: Admin `_dlq_entries` loads entire DLQ into memory (warning)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-admin/src/lol_admin/main.py:60`

**Issue:** `_dlq_entries` does `r.xrange(_STREAM_DLQ, "-", "+")` with no count limit,
loading ALL DLQ entries into memory at once. This is used by `cmd_dlq_list`,
`cmd_dlq_replay`, and `cmd_dlq_clear`. The DLQ stream has a maxlen of 50,000.

**Current complexity:** O(N) memory where N = DLQ entries.
- At N=100: trivial
- At N=50K (max): 50K DLQEnvelope objects in memory, each with JSON payload

**Fix:** For `cmd_dlq_list`, paginate with cursor-based XRANGE.
For `cmd_dlq_replay --all`, iterate in batches.
For `cmd_dlq_clear --all`, use `XTRIM stream:dlq MAXLEN 0` instead of reading all
entries then XDEL-ing them.

**Priority:** warning -- unbounded memory at DLQ max capacity (50K entries).
Note: this is a CLI tool, not a hot path, so impact is limited to operator experience.

---

### CR-10: Parser `_write_matchups` generates up to 12 pipeline commands per lane position (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-parser/src/lol_parser/main.py:191-260`

**Issue:** This is actually well-pipelined already. Including for completeness: the
matchup write generates ~60 Redis commands (12 per shared position x 5 positions) in a
single pipeline. This is efficient. No change needed.

**Priority:** no action needed -- already uses pipeline correctly.

---

### CR-11: Discovery `_is_idle` makes sequential XINFO GROUPS calls per stream (nit)

**File:** `/mnt/c/Users/WOPR/Desktop/LoL-Crawler/lol-pipeline-discovery/src/lol_discovery/main.py:46-74`

**Issue:** `_is_idle` calls `r.xinfo_groups(stream)` sequentially for each of the 4
pipeline streams. Since this short-circuits on the first non-idle stream, pipelining
is not straightforward (you would pipeline all 4, then check, which is slightly
wasteful if the first stream is busy). The current approach is acceptable given the
discovery service polls at multi-second intervals.

**Priority:** no action needed -- sequential short-circuit is appropriate here.

---

## Correctness Review Findings

Formal verification and correctness review of the full pipeline codebase.
Confidence threshold: >=80% for all findings.

---

### CR-1: SyntaxError — unparenthesized multi-except clauses (CRITICAL)

**Confidence:** 100%

**Files and lines:**
- `lol-pipeline-common/src/lol_pipeline/service.py` lines 143, 172
- `lol-pipeline-common/src/lol_pipeline/redis_client.py` line 29
- `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py` line 191

**Invariant violated:** System liveness — services cannot start at all.

**Description:**
Three source files use `except X, Y:` syntax (Python 2 style) instead of the
required `except (X, Y):` (parenthesized tuple). This is a `SyntaxError` in
all Python 3.x versions. Python parses the entire file at import time, so any
module importing from these files will crash immediately with `SyntaxError`.

Since `service.py` contains `run_consumer` (imported by every stream-consuming
service) and `redis_client.py` contains `get_redis` (imported by every service
needing Redis), this renders the entire pipeline non-functional.

**Regression:** Introduced in v1.2.0 (Phase 10). The parenthesized form was
correct in v1.1.0.

**Concrete scenario:**
```
$ python -m lol_crawler
SyntaxError: multiple exception types must be parenthesized (service.py, line 143)
```

**Fix:** Add parentheses around each multi-except clause:
- `except RedisError, OSError:` -> `except (RedisError, OSError):`
- `except RedisConnectionError, RedisTimeoutError, OSError:` -> `except (RedisConnectionError, RedisTimeoutError, OSError):`

---

### CR-2: Fetcher drops priority on outbound parse envelopes

**Confidence:** 95%

**Files and lines:**
- `lol-pipeline-fetcher/src/lol_fetcher/main.py` lines 49-54, 131-136

**Invariant violated:** Priority propagation — manually seeded players lose
their `manual_20` priority at the fetcher stage, causing all downstream
parse and analyze messages to default to `"normal"` priority.

**Description:**
The fetcher creates outbound `MessageEnvelope` objects for `stream:parse`
without propagating `envelope.priority` from the input message. Both code
paths (idempotent re-delivery on line 49 and fresh fetch on line 131) omit
the `priority=envelope.priority` argument, so `MessageEnvelope.__init__`
defaults to `priority="normal"`.

This is the same class of bug as F1 (parser dropping priority) which was
already fixed. The fetcher was missed.

**Concrete scenario:**
1. Seed service publishes to `stream:puuid` with `priority="manual_20"`
2. Crawler publishes to `stream:match_id` with `priority="manual_20"`
3. Fetcher receives match_id message, creates parse envelope with `priority="normal"`
4. Parser receives parse message with `priority="normal"` (should be `"manual_20"`)
5. Analyzer receives analyze message with `priority="normal"` (should be `"manual_20"`)
6. Priority-based batch sorting in `run_consumer` treats manually seeded
   players the same as auto-discovered ones.

**Fix:** Add `priority=envelope.priority` to both `MessageEnvelope(...)` calls
in `_fetch_match` (lines 49 and 131).

---

### CR-3: Crawler XADD pipeline omits maxlen trimming

**Confidence:** 90%

**Files and lines:**
- `lol-pipeline-crawler/src/lol_crawler/main.py` line 136

**Invariant violated:** Bounded stream growth — `stream:match_id` can grow
without limit during heavy crawl periods.

**Description:**
The crawler publishes match IDs to `stream:match_id` via a pipelined
`pipe.xadd(_OUT_STREAM, ...)` call that omits the `maxlen` parameter. The
standard `publish()` helper applies `MATCH_ID_STREAM_MAXLEN = 500_000` with
approximate trimming, but the crawler bypasses `publish()` for pipeline
efficiency.

While the backpressure check (`match_id_backpressure_threshold`, default 5000)
limits how fast the crawler adds messages, it does not cap the stream's total
size. If the fetcher falls behind, the stream grows unbounded until Redis
memory is exhausted.

**Concrete scenario:**
1. Crawler crawls 100 players, each with 500+ new matches
2. Crawler publishes 50,000 match IDs without maxlen trimming
3. Fetcher processes slowly (rate limited by Riot API)
4. Stream grows to millions of entries over time

**Fix:** Add `maxlen=MATCH_ID_STREAM_MAXLEN` and `approximate=True` to the
`pipe.xadd()` call:
```python
from lol_pipeline.streams import MATCH_ID_STREAM_MAXLEN
pipe.xadd(_OUT_STREAM, env.to_redis_fields(), maxlen=MATCH_ID_STREAM_MAXLEN, approximate=True)
```

---

### CR-4: Analyzer champion stats lost on lock expiry mid-processing

**Confidence:** 85%

**Files and lines:**
- `lol-pipeline-analyzer/src/lol_analyzer/main.py` lines 304-318

**Invariant violated:** Data completeness — champion aggregate stats can
permanently miss data points when the analyzer lock expires during processing.

**Description:**
The analyzer processes matches in two phases:
1. `_process_matches` (line 304): updates player stats + advances cursor atomically via Lua
2. `_update_champion_stats` (line 318): updates champion aggregate stats (HINCRBY, not cursor-guarded)

If the lock expires between these two phases (or during `_process_matches`),
the flow is:
- `_process_matches` returns `False` (lock lost at match N)
- Matches 1..N-1 have their player stats committed and cursor advanced
- The function returns early (line 315-316) WITHOUT calling `_update_champion_stats`
- On redelivery, the cursor has advanced past matches 1..N-1
- Champion stats for matches 1..N-1 are permanently lost

This affects only per-champion aggregate stats (win rates, pick rates), not
per-player stats. The impact is proportional to lock expiry frequency.

**Concrete scenario:**
1. Analyzer A acquires lock for PUUID X, processes 50 matches
2. Lock TTL expires at match 30 (default TTL = 300s, slow Redis/API)
3. Analyzer B acquires lock, reads cursor at match 30, processes 31-50
4. Matches 1-30 have player stats but no champion stats contribution

**Fix:** Run `_update_champion_stats` within the per-match Lua loop (heavy
refactor), or accept the data loss as negligible for aggregate statistics.
A simpler mitigation: increase `analyzer_lock_ttl_seconds` and add lock
refresh calls between `_process_matches` and `_update_champion_stats`.

---

### CR-5: Parser ban/matchup idempotency guard has TOCTOU race

**Confidence:** 80%

**Files and lines:**
- `lol-pipeline-parser/src/lol_parser/main.py` lines 373, 392, 401-404

**Invariant violated:** Idempotent writes — ban and matchup HINCRBY can
double-count when two parser instances process the same match_id concurrently.

**Description:**
The parser checks `SISMEMBER "match:status:parsed" match_id` (line 373),
then later does `SADD "match:status:parsed" match_id` inside a MULTI/EXEC
(line 392), then conditionally writes bans/matchups (line 401-404).

The check and the conditional write are not atomic. If two parser instances
receive messages for the same match_id (possible when a match appears in
multiple crawled players' match histories), both can see
`already_parsed = False` before either commits the SADD:

```
Parser A: SISMEMBER -> False
Parser B: SISMEMBER -> False
Parser A: MULTI/EXEC (SADD match:status:parsed)
Parser B: MULTI/EXEC (SADD match:status:parsed)
Parser A: _write_bans (HINCRBY +1)
Parser B: _write_bans (HINCRBY +1)  -- double-counted
```

**Impact:** Ban rates and matchup win rates are inflated by a small amount
for matches that are concurrently parsed. The probability depends on how
often two stream messages for the same match_id are in-flight simultaneously.

**Fix:** Move the `SISMEMBER` check into the same MULTI/EXEC transaction as
the `SADD`, or use a Lua script that atomically checks-and-sets the parsed
flag and returns whether the ban/matchup writes should proceed.

---

### CR-6: Delay scheduler `_DISPATCH_LUA` does not guard against duplicate dispatch

**Confidence:** 80%

**Files and lines:**
- `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py` lines 89-109, 112-170

**Invariant violated:** At-most-once delayed dispatch — if the delay scheduler
process crashes after `ZRANGEBYSCORE` but before completing all dispatches
in a batch, the undispatched members will be re-read and dispatched on restart.
Members already dispatched by the Lua script cannot be detected (ZREM already
removed them), but the Lua script itself has no guard against being called
for a member that was already ZREMed by a prior call.

**Description:**
The `_tick` function reads ready members via `ZRANGEBYSCORE`, then dispatches
each one via `_DISPATCH_LUA` (atomic XADD + ZREM). The Lua script always
executes XADD before ZREM, without checking whether the member still exists
in the sorted set. If two executions target the same member:
- First call: XADD (message published) + ZREM (member removed) -- both succeed
- Second call: XADD (duplicate message published) + ZREM (no-op, member gone)

This can happen when:
1. Scheduler crashes mid-batch and restarts
2. Two scheduler instances run simultaneously (misconfiguration)

Downstream consumers handle duplicates via at-least-once semantics, so this
is not catastrophic, but it violates the design intent of the atomic Lua
script.

**Fix:** Add a ZSCORE existence check to `_DISPATCH_LUA` before XADD:
```lua
if redis.call("ZSCORE", zkey, member) == false then
    return 0  -- already dispatched
end
```

---

## Think Round 1: Critical Bugs

**Scan date:** 2026-03-21
**Scope:** All `lol-pipeline-*/src/lol_*/main.py` files, `lol-pipeline-common/src/lol_pipeline/{service,streams,models,riot_api,rate_limiter,config,helpers,priority,redis_client,raw_store,resolve}.py`
**Target runtime:** Python 3.14+ (per `requires-python = ">=3.14"` in all `pyproject.toml` files)

### Result: No critical bugs found

Checked for:
- NameError / ImportError from missing imports or references: **None found.**
- Race conditions causing data corruption: **All guarded.** Analyzer uses Lua for atomic lock+update. Parser uses SADD for idempotency. Delay scheduler uses atomic XADD+ZREM Lua.
- Infinite loops or unbounded memory growth: **None found.** All loops have convergence conditions (ZREM removes processed members, empty batch returns). All sorted sets and streams are capped (MAXLEN, ZREMRANGEBYRANK).
- Silent data loss (ACK without processing): **None found.** All ACK calls follow completed processing or explicit terminal decisions (404, system halt leaves in PEL).
- Division by zero in hot paths: **None found.** All divisions guarded (`max(deaths, 1)`, `if games == 0: return`, `if self.games` checks).

**Note on `except X, Y:` syntax:** 12 occurrences across 9 files use unparenthesized multi-exception syntax. This is valid Python 3.14 (PEP 758). Previously reviewed and rejected as a finding in Phases 11, 13, and 14. Not a runtime bug on the target platform.

## Think Round 3: Contract Drift

### D1 (warning): `correlation_id` missing from ALL 6 pact files

**Canonical schemas** (`envelope.json:56-60`, `dlq_envelope.json:65-69`) define `correlation_id` as an optional field with `default: ""`. The `MessageEnvelope` dataclass (`models.py:31`) and `DLQEnvelope` dataclass (`models.py:83`) both carry `correlation_id`. All services propagate it correctly in production code (seed, crawler, fetcher, parser, admin, UI, recovery, discovery, delay-scheduler).

However, **no pact file** includes `correlation_id` in its `contents` or `matchingRules`:

| Pact file | Status |
|-----------|--------|
| `crawler-seed.json` | Missing `correlation_id` |
| `fetcher-crawler.json` | Missing `correlation_id` |
| `parser-fetcher.json` | Missing `correlation_id` |
| `analyzer-parser.json` | Missing `correlation_id` |
| `recovery-common.json` (all 5 messages) | Missing `correlation_id` |
| `delay-scheduler-common.json` (both messages) | Missing `correlation_id` |

**Impact:** Contract tests never verify that `correlation_id` is present or correctly typed in inter-service messages. A regression that drops `correlation_id` during serialization would not be caught by CDCT.

**Fix:** Add `"correlation_id": ""` to the `contents` of every pact message. Add a matching rule to `matchingRules.body`:
```json
"$.correlation_id": { "matchers": [{ "match": "type" }] }
```

### D2 (warning): `dlq_attempts` missing from all 6 MessageEnvelope pact files

The canonical `envelope.json:44-49` defines `dlq_attempts` as optional with `default: 0`. The `MessageEnvelope` dataclass (`models.py:29`) carries it, and `to_redis_fields()` always serializes it. The DLQ pact (`recovery-common.json`) correctly includes `dlq_attempts`, but the following pacts omit it:

- `crawler-seed.json`
- `fetcher-crawler.json`
- `parser-fetcher.json`
- `analyzer-parser.json`
- `delay-scheduler-common.json` (both messages)

**Impact:** If a consumer depends on `dlq_attempts` for retry logic after DLQ round-trip (e.g., the delay scheduler re-publishing an envelope that has been through DLQ), the contract tests would not catch its absence. The field IS always present in actual wire messages because `to_redis_fields()` serializes it unconditionally.

**Fix:** Add `"dlq_attempts": 0` to each pact's `contents` and add a matching rule:
```json
"$.dlq_attempts": { "matchers": [{ "match": "integer" }] }
```

### D3 (warning): Provider contract tests validate partial documents against `envelope.json`

All 4 provider test files construct a partial `document` dict with only 7 of 10 fields for `jsonschema.validate()`:

- `seed/tests/contract/test_provider.py:40-48`
- `crawler/tests/contract/test_provider.py:39-47`
- `fetcher/tests/contract/test_provider.py:38-46`
- `parser/tests/contract/test_provider.py:41-49`

Each omits `dlq_attempts`, `priority`, and `correlation_id`. Because `envelope.json` uses `additionalProperties: false` but these 3 fields are not in `required`, validation passes with the partial document. This means the provider tests do NOT verify that the full 10-field message the code actually produces is schema-valid.

**Impact:** If a new field were added to `to_redis_fields()` but NOT to `envelope.json`'s `properties`, these tests would not catch the `additionalProperties` violation because they only validate a hand-picked subset of fields.

**Fix:** Replace the hand-built `document` dict with a full round-trip reconstruction:
```python
fields = envelope.to_redis_fields()
document = {
    k: (json.loads(v) if k == "payload" else (int(v) if v.isdigit() else v))
    for k, v in fields.items()
}
validate(instance=document, schema=schema)
```
Or more explicitly, include all 10 fields in the document.

### Summary

| ID | Severity | Schema/Code agreement | Pact agreement | Test coverage |
|----|----------|----------------------|----------------|---------------|
| D1 | warning | OK (both have `correlation_id`) | DRIFT: 0/6 pacts include it | Not tested |
| D2 | warning | OK (both have `dlq_attempts`) | DRIFT: 0/6 envelope pacts include it | Not tested (DLQ pact is fine) |
| D3 | warning | OK | N/A | Partial validation weakens provider tests |

No critical drift found. Schemas and dataclasses are fully aligned. The drift is between pacts and schemas — pacts lag behind the schema by 2 optional fields (`correlation_id`, `dlq_attempts`), and provider tests only validate a subset of what the code produces.

---

## Think Round 10: Top Priority

### Champion Build Recommendations (Items + Runes + Skill Order)

**Confidence: 95%**

**The gap.** The single largest feature gap between this pipeline and OP.GG/U.GG/Mobalytics
is champion build recommendations: "what items, runes, and skill order should I use on
Champion X in Role Y this patch?" Every major competitor surfaces this as their primary
page for each champion. This pipeline already collects all the raw data but never aggregates
or displays it.

**What the pipeline already has (but does not use).**

The Parser (`lol-pipeline-parser/src/lol_parser/main.py` lines 69-114) already extracts
and stores per-participant:
- `items` — 7 item slots as JSON array (line 71, 89)
- `perk_keystone`, `perk_primary_style`, `perk_sub_style` — rune page (lines 37-45, 110-112)
- `summoner1_id`, `summoner2_id` — summoner spells (lines 92-93)
- `build:{match_id}:{puuid}` — item purchase order from timeline (lines 306-313)
- `skills:{match_id}:{puuid}` — skill level-up order from timeline (lines 314-322)

None of this data is aggregated by the Analyzer (`lol-pipeline-analyzer/src/lol_analyzer/main.py`).
The `_UPDATE_CHAMPION_LUA` script (lines 93-118) aggregates games/wins/kills/deaths/assists/
gold/cs/damage/vision/multikills -- but zero build data. The UI champion detail page
(`lol-pipeline-ui/src/lol_ui/main.py` lines 2941-3027) shows stats and matchups but has no
build/rune section.

**What competitors show (and this pipeline could replicate with existing data).**

1. **Highest win-rate item build** — final-item set with win rate and pick rate
   (e.g., "Kraken Slayer > Phantom Dancer > Infinity Edge — 54.2% WR, 12.3% pick rate")
2. **Highest win-rate rune page** — keystone + primary tree + secondary tree with win rate
   (e.g., "Lethal Tempo + Precision/Domination — 53.8% WR")
3. **Skill order** — max order (Q>W>E or similar) with win rate
4. **Summoner spells** — most common pair with win rate

**Proposed implementation (3 components, service-isolated).**

1. **Analyzer: new aggregation keys** — Extend `_UPDATE_CHAMPION_LUA` or add a second Lua
   script that, for each ranked match, increments counters on:
   - `champion:builds:{name}:{patch}:{role}` — Hash where field = sorted item-set fingerprint,
     value = `{games},{wins}` (e.g., `"3031,3046,3036" -> "847,459"`)
   - `champion:runes:{name}:{patch}:{role}` — Hash where field = `{keystone}:{primary}:{sub}`,
     value = `{games},{wins}`
   - `champion:skills:{name}:{patch}:{role}` — Hash where field = max-order string
     (e.g., `"Q,W,E"`), value = `{games},{wins}`
   - `champion:spells:{name}:{patch}:{role}` — Hash where field = sorted spell pair
     (e.g., `"4,7"`), value = `{games},{wins}`

   These use the same HINCRBY pattern as existing champion stats. Idempotency is already
   guarded by the `match:status:parsed` SADD check in the Parser (line 433). TTL matches
   `CHAMPION_STATS_TTL_SECONDS` (90 days).

2. **UI: `/champions/{name}` build section** — Read the aggregation hashes, sort by games,
   compute win rate, and render:
   - "Recommended Build" card (top 3 item builds by games played, showing WR%)
   - "Rune Page" card (top 3 rune setups by games played, showing WR%)
   - "Skill Order" card (top 2 max orders by games played)
   - Use DDragon CDN for item/rune icons (already used for champion/item icons)

3. **Contract/schema update** — No new streams or envelope changes needed. This is purely
   an Analyzer write-side expansion and a UI read-side expansion. No cross-service coupling.

**Why this over the alternatives considered.**

| Alternative | Why not |
|---|---|
| Redis 8.6 upgrade (idempotent XADD) | Infrastructure improvement, not user-facing. Current at-least-once + idempotency guards already work. ~2-5% throughput gain is not the bottleneck (API rate limit is). |
| Mobalytics-style GPI (8-axis performance radar) | Requires substantial ML/normalization work. High complexity, medium value. Better as a follow-up after builds ship. |
| Redis 8.2 XACKDEL | Marginal ops improvement (saves one round-trip per message). Not user-visible. |
| Python 3.14 specific features | Already on Python 3.14. No new asyncio primitives change the architecture. |
| Riot API new endpoints | No significant new endpoints announced for 2026. SummonerID/AccountID deprecation (June 2025) already handled — pipeline uses PUUIDs exclusively. |

**Complexity: Medium.** The data extraction path already exists end-to-end. The work is:
aggregation logic in Analyzer (~100 lines of Lua + Python), UI rendering (~200 lines of
HTML/Python), tests, and schema docs. No new services, no new streams, no new dependencies.

**Risk: Low.** Additive change — no modifications to existing aggregation, no new failure
modes. Worst case: insufficient sample size per build path makes win rates noisy (mitigated
by requiring minimum N games before displaying).

**Sources consulted:**
- OP.GG champion build pages (e.g., op.gg/lol/champions/yone/build)
- U.GG champion build pages (e.g., u.gg/lol/champions/riven/build)
- Mobalytics GPI documentation (mobalytics.gg/gpi/)
- Redis 8.6 announcement (redis.io/blog/announcing-redis-86-performance-improvements-streams/)
- Redis 8.6 idempotent production docs (redis.io/docs/latest/develop/data-types/streams/idempotency/)
- Riot Games API changelog (riotgames.com/en/DevRel/riot-games-api-change-log)

---

## Think Round 5: Redis TTL Audit

Audit date: 2026-03-21. Confidence: 95%.

Cross-referenced `docs/architecture/04-storage.md` against all service source files.
Only findings with real unbounded growth risk under 24/7 operation are listed.
Keys already bounded by MAXLEN, ZREMRANGEBYRANK, or explicit EXPIRE are excluded.

### Finding 1: `priority:active` SET has no TTL and no cap

- **Key**: `priority:active` (Set)
- **Source**: `lol-pipeline-common/src/lol_pipeline/priority.py` lines 17, 60, 66
- **Issue**: `set_priority()` does `SADD priority:active <puuid>`. `clear_priority()` does `SREM priority:active <puuid>`. The `player:priority:{puuid}` String key has a 24h TTL, but when that TTL expires naturally (without an explicit `clear_priority()` call), the PUUID remains orphaned in the `priority:active` SET permanently.
- **Growth scenario**: Any code path where `set_priority()` runs but neither crawler nor analyzer calls `clear_priority()` -- e.g., seed followed by system halt, 403 auth error, or message dropped by MAXLEN trimming before reaching the crawler. Each orphan is ~40 bytes.
- **Impact at scale**: At 10K players with 1% orphan rate per seed cycle, ~100 stale members accumulate per cycle. After months of 24/7 operation, the SET grows to tens of thousands of stale entries. The `has_priority_players()` check (SCARD) returns a false positive, blocking Discovery indefinitely.
- **Recommendation**: Periodic cleanup. Two options:
  - (a) In `has_priority_players()`, after SCARD > 0, verify at least one `player:priority:{puuid}` key actually exists (sample check). If none do, delete the SET.
  - (b) Add a scheduled cleanup (e.g., in Discovery's `_promote_batch`) that scans `priority:active` members and SREMs any whose `player:priority:{puuid}` key has expired.
- **Priority**: critical -- false-positive blocks Discovery entirely, starving the pipeline of new players.

### Finding 2: `match:status:parsed` SET grows unbounded (TTL reset on every write)

- **Key**: `match:status:parsed` (Set)
- **Source**: `lol-pipeline-parser/src/lol_parser/main.py` lines 433-434
- **Issue**: Every parsed match does `SADD match:status:parsed <match_id>` then `EXPIRE match:status:parsed 7776000` (90 days). Because the EXPIRE is reset on every SADD, the key never actually expires under continuous operation. Members are never removed.
- **Growth scenario**: At 20 matches/second sustained, ~1.7M matches/day. Each member is ~15 bytes (match ID like `NA1_1234567890`). After 90 days: ~153M members, ~2.3 GB.
- **Impact at scale**:
  - 1K players: ~50K members, ~750 KB (negligible)
  - 10K players: ~3M members, ~45 MB (acceptable)
  - 100K players: ~30M members, ~450 MB (significant)
  - Unbounded 24/7 for 1 year: grows to multi-GB
- **Recommendation**: Stop resetting the TTL on every write. Apply the same TTL-guard pattern used by `seen:matches` (check TTL before setting EXPIRE). Change line 434 in `lol-pipeline-parser/src/lol_parser/main.py` to only set EXPIRE when no TTL exists.
- **Priority**: warning -- only becomes a real problem at >10K players or after months of operation.

### Finding 3: `match:status:failed` SET has the same TTL-reset problem

- **Key**: `match:status:failed` (Set)
- **Source**: `lol-pipeline-recovery/src/lol_recovery/main.py` lines 63-64
- **Issue**: Same pattern as `match:status:parsed`: `SADD` + `EXPIRE 7776000` on every archive. The TTL resets on each write, so the key never expires under continuous operation.
- **Growth scenario**: Much smaller than `parsed` since only exhausted DLQ entries are archived. Typical rate: <100/day. After 1 year: ~36K members, ~540 KB.
- **Impact at scale**: Negligible memory impact.
- **Recommendation**: Apply the same TTL-guard fix as Finding 2 for consistency.
- **Priority**: optimization -- growth is inherently bounded by DLQ exhaustion rate, which is small.

### Finding 4: `player:rank:history:{puuid}` ZSET has no member cap

- **Key**: `player:rank:history:{puuid}` (Sorted Set)
- **Source**: `lol-pipeline-crawler/src/lol_crawler/main.py` lines 304-306
- **Issue**: Each crawl appends `{tier}:{division}:{lp}` with score=epoch_ms. The key has a 30-day EXPIRE (PLAYER_DATA_TTL_SECONDS), so inactive players self-evict. But for active players who are crawled frequently, the TTL resets and members accumulate indefinitely. There is no ZREMRANGEBYRANK cap.
- **Growth scenario**: A player crawled every 2 hours for 30 days accumulates ~360 entries. A player crawled every 30 minutes (high activity rate) accumulates ~1440 entries. Each member is ~20-30 bytes. Per player: ~43 KB worst case.
- **Impact at scale**:
  - 1K active players: ~43 MB (noticeable)
  - 10K active players: ~430 MB (significant if many are high-activity)
  - Realistic (mixed activity rates): ~50 MB at 10K players
- **Recommendation**: Add ZREMRANGEBYRANK after ZADD to cap at e.g. 500 entries. Add after line 305: `await r.zremrangebyrank(hist_key, 0, -(501))`
- **Priority**: warning -- memory overhead is manageable at 10K players but the lack of a cap is a design gap.

### Non-findings (keys confirmed safe)

| Key | Why safe |
|-----|---------|
| `player:{puuid}` | 30d TTL, refreshed on crawl/seed. Inactive players self-evict. |
| `player:matches:{puuid}` | 30d TTL + ZREMRANGEBYRANK cap at PLAYER_MATCHES_MAX (500). |
| `player:stats:{puuid}` | 30d TTL set by analyzer after every analysis. |
| `player:stats:cursor:{puuid}` | 30d TTL set by analyzer. |
| `player:champions:{puuid}` | 30d TTL set by analyzer. |
| `player:roles:{puuid}` | 30d TTL set by analyzer. |
| `player:stats:lock:{puuid}` | 300s TTL (px) on SET NX. Self-expires. |
| `player:priority:{puuid}` | 24h TTL. Self-expires. |
| `match:{match_id}` | 7d TTL (MATCH_DATA_TTL_SECONDS). |
| `participant:{match_id}:{puuid}` | 7d TTL set by parser. |
| `raw:match:{match_id}` | 24h TTL (RAW_STORE_TTL_SECONDS). Disk fallback covers misses. |
| `raw:timeline:{match_id}` | 7d TTL (match_data_ttl_seconds). |
| `discover:players` | Capped by ZREMRANGEBYRANK at MAX_DISCOVER_PLAYERS (50K). |
| `players:all` | Capped at 50K by ZREMRANGEBYRANK + stale entry trimming in Discovery. |
| `delayed:messages` | Transient; members removed by Delay Scheduler after dispatch. |
| `player:name:{name}#{tag}` | 24h TTL (CACHE_TTL_S). |
| `name_cache:index` | Capped at 10K by ZREMRANGEBYRANK. |
| `consumer:retry:{stream}:{msg_id}` | 7d TTL. Self-expires. |
| `autoseed:cooldown:{puuid}` | 300s TTL. Self-expires. |
| `ddragon:version` | 24h TTL. |
| `ratelimit:short` / `ratelimit:long` | PEXPIRE set by Lua script (1s / 120s). Cleaned by ZREMRANGEBYSCORE. |
| `ratelimit:limits:short` / `ratelimit:limits:long` | 1h TTL. |
| `ratelimit:throttle` | 2s TTL. |
| `crawl:cursor:{puuid}` | 10m TTL; deleted after pagination completes. |
| `system:halted` | Singleton. Set/cleared manually. No growth risk. |
| `patch:list` | 90d TTL set by `_UPDATE_CHAMPION_LUA`. Members bounded by game patch count (~24/year). |
| `champion:stats:*`, `champion:index:*`, `champion:bans:*` | 90d TTL (CHAMPION_STATS_TTL_SECONDS). |
| `matchup:*`, `matchup:index:*` | 90d TTL (CHAMPION_STATS_TTL_SECONDS). |
| `build:{match_id}:{puuid}`, `skills:{match_id}:{puuid}` | 7d TTL. |
| `player:rank:{puuid}` | 24h TTL. |
| `seen:matches` | 7d TTL set once (F5 fix). Bounded by TTL sawtooth. Acceptable. |
| `correlation_id` | Not a Redis key -- field inside stream entries. Bounded by stream MAXLEN. |
| Streams (all 6) | Bounded by MAXLEN (~approximate): 10K-500K per stream. |

### Recently added keys status

| Key | Status |
|-----|--------|
| `priority:active` (SET) | **FINDING 1** -- no TTL, no cap, orphan accumulation blocks Discovery. |
| `player:rank:history:{puuid}` (ZSET) | **FINDING 4** -- has 30d TTL but no member cap per key. |
| `correlation_id` field in envelopes | Safe -- not a Redis key, just a field inside stream entries bounded by MAXLEN. |

---

## Think Round 6: UI Security

**Scope**: Full security audit of `lol-pipeline-ui/src/lol_ui/main.py` (3430 lines).

**Result: The UI service is secure.** No exploitable vulnerabilities found at >=85% confidence.

### Checklist Summary

| Category | Status | Notes |
|----------|--------|-------|
| XSS | PASS | All user-controlled values are `html.escape()`d before rendering. The `_badge()`, `_render_log_lines()`, `_stats_table()`, `_render_champion_rows()`, `_match_history_html()`, `_profile_header_html()`, `_rank_card_html()`, `_rank_history_html()`, `_playstyle_pills_html()`, `_match_badges_html()`, `_matchup_table_html()`, `_champion_tier_table()`, and `_champion_detail_html()` functions all escape dynamic content. The `_badge_html()` raw-HTML variant is called exactly once with a static string (`&#10003; Verified`). |
| Input validation | PASS | `_PUUID_RE` validates PUUIDs (`/stats/matches`). `_CHAMPION_NAME_RE` validates champion names (`/champions/{name}`, `/matchups`). `_PATCH_RE` validates patch format (`/matchups`). `_STREAM_ENTRY_ID_RE` validates DLQ entry IDs (`/dlq/replay`). `_REGIONS_SET` validates region (`/stats`). `_MATCHUP_ROLES` validates role (`/matchups`). `_CHAMPION_ROLES_SET` validates role (`/champions`). |
| Header security | PASS | Middleware at line 1144 adds `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, and `Content-Security-Policy` with domain-restricted `img-src`. |
| SSRF | PASS | Only two outbound HTTP targets: `ddragon.leagueoflegends.com/api/versions.json` (hardcoded) and `ddragon.leagueoflegends.com/cdn/{version}/...` where `version` comes from Riot's API response, not user input. All Riot API calls go through `RiotClient`. |
| Path traversal | PASS | `_tail_file()` and `_merged_log_lines()` only read from `cfg.log_dir` (env var) using `glob("*.log")`. No user input influences file paths. |
| Redis key injection | PASS | All Redis keys are constructed from validated inputs: PUUIDs (regex-validated), champion names (regex-validated), roles (frozenset-validated), patches (regex-validated or from Redis `patch:list`), entry IDs (regex-validated). |
| DLQ analytics | PASS | `failure_code` and `original_stream` values from Redis are escaped via `_badge()` (which calls `html.escape`) and `html.escape()` respectively. |
| Consumer lag | PASS | `_format_group_cells()` escapes group names with `html.escape()`. Numeric values (`pending`, `lag`) are integers from Redis. |
| Match badges | PASS | `_match_badges()` returns hardcoded string tuples. `_match_badges_html()` escapes badge names with `html.escape()`. |
| Tilt indicator | PASS | `_streak_indicator()` computes from Redis numeric data. `_tilt_banner_html()` uses `_badge()` (auto-escaping) and `html.escape()` for win rate display. |
| Playstyle tags | PASS | `_playstyle_tags()` returns hardcoded string tuples. `_playstyle_pills_html()` escapes tag names with `html.escape()`. |
| Rank history | PASS | `_rank_history_html()` escapes all parts of the `TIER:DIVISION:LP` string with `html.escape()`. Date strings are escaped. |
| Champion diversity | PASS | `_champion_diversity()` returns computed numeric score and a label from the hardcoded `_DIVERSITY_LABELS` list. Label is escaped with `html.escape()` in `_stats_table()`. |
| Tier list | PASS | `_champion_tier_table()` escapes champion names, roles. Tier letters come from hardcoded `_TIER_COLORS` dict. PBI scores are computed floats. |

### Observations (non-exploitable)

1. **CSP uses `'unsafe-inline'` for scripts** (line 1153). Required for the inline auto-refresh scripts on `/logs` and `/streams`. Not exploitable because all user inputs are escaped, but a nonce-based CSP would provide stronger defense-in-depth.

2. **`/stats/matches` does not validate `region` or `riot_id`** against allowlists. However, these values are only used in HTML output (escaped at lines 2525-2527) and in `data-*` attributes (also escaped). They are not used in Redis keys or API calls. Not exploitable.

3. **`page` parameter** in `/stats/matches`, `/players` is not capped. A very large value would cause Redis `ZREVRANGE` with a large offset, which is O(N). This is a minor DoS concern, not a security vulnerability.

## Think Round 9: Error Messages

**Reviewer:** QA Engineer (error message quality, user-facing text)
**Date:** 2026-03-21
**Verdict:** Generally good. Error messages across all surfaces are actionable and log
levels are appropriate. The following findings represent the genuinely confusing or
misleading cases.

### Findings

#### E1 — Silent swallow of Data Dragon fetch errors (minor)
- **Surface:** Web UI
- **File:** `lol-pipeline-ui/src/lol_ui/main.py` lines 712, 745
- **Issue:** `_get_ddragon_version()` and `_get_champion_id_map()` catch bare
  `except Exception` and return `None` / `{}` with no logging at all. If the
  Data Dragon CDN is down, the UI silently degrades (no champion icons) with no
  log entry to explain why. An operator diagnosing missing icons has no trail.
- **Severity:** minor
- **Suggested fix:** Add `_log.warning("Data Dragon fetch failed", exc_info=True)`
  inside the except blocks.

#### E2 — Config validation crash gives pydantic traceback, not actionable hint (major)
- **Surface:** All services, Admin CLI, Seed CLI
- **File:** Every `main.py` that calls `Config()` (9 services)
- **Issue:** When `RIOT_API_KEY` or `REDIS_URL` is missing, pydantic raises a
  `ValidationError` with a raw traceback ending in `Field required`. This is the
  most common first-run failure for new operators, yet none of the 9 entry points
  wrap `Config()` in a try/except. The admin CLI wraps `get_redis()` but not
  `Config()` itself, so missing `RIOT_API_KEY` still produces a traceback.
  The pydantic error text ("Field required [type=missing, input_value={},
  input_type=dict]") does not mention `.env` or `.env.example`.
- **Severity:** major
- **Suggested fix:** In each entry point (or via a shared wrapper in
  `lol-pipeline-common`), catch `pydantic.ValidationError` and print:
  `"Configuration error: {field} is required. Copy .env.example to .env and fill
  in the missing values. See README for details."` Then exit with code 1.

#### E3 — DLQ corrupt entry message suggests nonexistent clear subcommand (minor)
- **Surface:** Web UI
- **File:** `lol-pipeline-ui/src/lol_ui/main.py` lines 2362-2363, 2374-2375
- **Issue:** When a corrupt or invalid-stream DLQ entry is found during replay, the
  UI tells the user to run `just admin dlq clear {entry_id}`. But the admin CLI's
  `dlq clear` command requires `--all` and does not accept individual entry IDs.
  The suggested command will fail with an argparse error.
- **Severity:** minor (misleading remediation advice)
- **Suggested fix:** Change the suggestion to:
  `"To clear all DLQ entries, run <code>just admin dlq clear --all</code>."` or
  implement per-entry clear in the admin CLI.

#### E4 — Fetcher logs "server error" at ERROR but no remediation context (nit)
- **Surface:** Logs
- **File:** `lol-pipeline-fetcher/src/lol_fetcher/main.py` line 167
- **Issue:** `log.error("server error", ...)` when Riot returns 5xx. The message
  is terse. The nack_to_dlq happens right after, so the message is not silent, but
  it does not say "will retry via DLQ" which would help an operator reading logs
  understand the entry is not lost.
- **Severity:** nit
- **Suggested fix:** Change to `"Riot server error — nacking to DLQ for retry"`.

#### E5 — Admin CLI has no --json flag on several subcommands that could use it (nit)
- **Surface:** Admin CLI
- **File:** `lol-pipeline-admin/src/lol_admin/main.py`
- **Issue:** `stats` and `dlq list` support `--json`, but `delayed-list`,
  `recalc-priority`, `recalc-players`, and `clear-priority` do not. This is not
  an error per se, but it breaks the pattern for scripting automation. An operator
  writing a script that pipes admin output discovers some commands lack JSON.
- **Severity:** nit (consistency, not a bug)
- **Suggested fix:** Low priority. Consider adding `--json` to `delayed-list` at
  minimum, since it outputs structured data.

### Summary

| ID | Surface   | Severity | Status |
|----|-----------|----------|--------|
| E1 | Web UI    | minor    | open   |
| E2 | All       | major    | open   |
| E3 | Web UI    | minor    | open   |
| E4 | Logs      | nit      | open   |
| E5 | Admin CLI | nit      | open   |

**Overall assessment:** Error handling quality is high. The UI handles all four Riot
API error classes (NotFoundError, AuthError, RateLimitError, ServerError) with
specific, actionable messages. The admin CLI gives clear Redis-down messages. The
service loop in `service.py` correctly retries transient errors and sends poison
messages to DLQ after max retries. Log levels are consistently appropriate: INFO for
normal flow, WARNING for recoverable/degraded, CRITICAL for halt conditions, ERROR
for failures that need attention. The HALT_BANNER in the UI is exemplary -- it tells
the operator exactly what happened and the 3-step recovery procedure.

The one major finding (E2) is worth fixing because it affects every new operator's
first interaction with the pipeline.

## Think Round 8: DRY Violations

Scan date: 2026-03-21. Confidence threshold: 85%.

### DRY-1: `_make_replay_envelope` duplicated in 3 locations (critical)

**Files:**
- `lol-pipeline-admin/src/lol_admin/main.py:80-91`
- `lol-pipeline-ui/src/lol_ui/main.py:862-874`
- `lol-pipeline-recovery/src/lol_recovery/main.py:78-91` (inline variant)

**Detail:** Identical `_make_replay_envelope(dlq, max_attempts) -> MessageEnvelope` function
exists in admin and UI (13 lines each, character-for-character identical). Recovery has
the same logic inlined in `_requeue_delayed`. All three reconstruct a `MessageEnvelope`
from a `DLQEnvelope` using the same `dlq.original_stream.removeprefix("stream:")` pattern.

**Fix:** Move `make_replay_envelope` to `lol-pipeline-common/src/lol_pipeline/models.py`
(or `streams.py`) as a public function. All three services import from there.

---

### DRY-2: `_maxlen_for_stream` duplicated across 3 modules (warning)

**Files:**
- `lol-pipeline-common/src/lol_pipeline/streams.py:285-291` (`_maxlen_for_replay`)
- `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py:43-45` (`_maxlen_for_stream`)
- `lol-pipeline-admin/src/lol_admin/main.py:51-57` (`_maxlen_for_stream`)

**Detail:** Three implementations of the same per-stream MAXLEN policy lookup. All map
`stream:match_id` -> `MATCH_ID_STREAM_MAXLEN`, `stream:analyze` -> `ANALYZE_STREAM_MAXLEN`,
everything else -> `_DEFAULT_MAXLEN`. The admin version also re-declares `_DEFAULT_MAXLEN = 10_000`
instead of importing it from `streams.py`.

**Fix:** Export a single `maxlen_for_stream(stream: str) -> int` from `lol_pipeline.streams`.
The delay-scheduler's variant returns `int | None` but could use `0` for "no trimming" to unify.

---

### DRY-3: Player registration pattern duplicated across seed, discovery, UI (warning)

**Files:**
- `lol-pipeline-seed/src/lol_seed/main.py:116-128`
- `lol-pipeline-discovery/src/lol_discovery/main.py:155-174`
- `lol-pipeline-ui/src/lol_ui/main.py:1428-1442`

**Detail:** All three services perform the same player registration sequence:
1. `hset player:{puuid}` with `{game_name, tag_line, region, seeded_at}`
2. `expire player:{puuid}` with `PLAYER_DATA_TTL_SECONDS`
3. `zadd players:all {puuid: timestamp}`
4. `zremrangebyrank players:all 0 -(cfg.players_all_max + 1)`

The fields and TTL are identical. Discovery wraps it in a pipeline (good); seed and UI do not.
The ordering of publish vs. registration also differs subtly between the three, which may indicate
a latent bug rather than an intentional difference.

**Fix:** Extract `register_player(r, cfg, puuid, game_name, tag_line, region)` into
`lol_pipeline.helpers`. Each caller invokes it after publishing.

---

### DRY-4: `_DISCOVER_KEY = "discover:players"` redefined instead of importing constant (nit)

**Files:**
- `lol-pipeline-parser/src/lol_parser/main.py:25`
- `lol-pipeline-discovery/src/lol_discovery/main.py:27`

**Detail:** `lol_pipeline.constants` already exports `DISCOVER_PLAYERS_KEY = "discover:players"`.
Both parser and discovery define their own `_DISCOVER_KEY` with the same literal value.

**Fix:** Import `DISCOVER_PLAYERS_KEY` from `lol_pipeline.constants` (alias locally if desired).

---

### DRY-5: `is_system_halted()` helper exists but raw `r.get("system:halted")` used in 7+ locations (warning)

**Files:**
- `lol-pipeline-common/src/lol_pipeline/helpers.py:55-60` (defines `is_system_halted`)
- `lol-pipeline-common/src/lol_pipeline/service.py:160` (raw `r.get`)
- `lol-pipeline-recovery/src/lol_recovery/main.py:190,234` (raw `r.get`)
- `lol-pipeline-discovery/src/lol_discovery/main.py:243,308` (raw `r.get`)
- `lol-pipeline-seed/src/lol_seed/main.py:83` (raw `r.get`)
- `lol-pipeline-ui/src/lol_ui/main.py:1837,2240,2558,3349` (raw `r.get`)

**Detail:** The helper `is_system_halted(r)` was added in the common library and is used by
crawler, fetcher, parser, and analyzer. However, service.py itself, recovery, discovery, seed,
and UI all use the raw Redis call `await r.get("system:halted")` instead. If the key name or
semantics ever change, these 9+ call sites would need manual updates.

**Fix:** Replace all raw `r.get("system:halted")` calls with `is_system_halted(r)`. The
`SYSTEM_HALTED_KEY` constant from `constants.py` is also unused by the helper itself.

---

### DRY-6: Riot API error handling pattern (403->halt, 429->DLQ, 5xx->DLQ) duplicated (warning)

**Files:**
- `lol-pipeline-crawler/src/lol_crawler/main.py:219-251` (`_handle_crawl_error`)
- `lol-pipeline-fetcher/src/lol_fetcher/main.py:139-176` (inline try/except chain)

**Detail:** Both crawler and fetcher implement the same 4-branch Riot API error routing:
- 404 -> discard + ack
- 403 -> set `system:halted`, do NOT ack
- 429 -> `nack_to_dlq` with `http_429` + `retry_after_ms`, then ack
- 5xx -> `nack_to_dlq` with `http_5xx`, then ack

The crawler refactored this into `_handle_crawl_error`, but the fetcher still has it inlined.
The logic is ~25 lines in each service with the same branching structure. The only differences
are the `failed_by` label ("crawler" vs "fetcher") and the entity identifier in log extras
("puuid" vs "match_id").

**Fix:** Extract `handle_riot_api_error(r, stream, group, msg_id, envelope, exc, service_name, log)`
into `lol_pipeline.helpers` (or a new `lol_pipeline.error_routing` module). The service passes
its name and the function does the dispatch. Extra log context can be passed as kwargs.

---

### DRY-7: Consumer `main()` boilerplate repeated across 4 services (nit)

**Files:**
- `lol-pipeline-crawler/src/lol_crawler/main.py:441-466`
- `lol-pipeline-fetcher/src/lol_fetcher/main.py:181-207`
- `lol-pipeline-parser/src/lol_parser/main.py:502-527`
- `lol-pipeline-analyzer/src/lol_analyzer/main.py:367-391`

**Detail:** All four `main()` functions follow the same 15-line template:
1. `log = get_logger("<name>")`
2. `cfg = Config()`
3. `r = get_redis(cfg.redis_url)`
4. `consumer = f"{socket.gethostname()}-{os.getpid()}"`
5. Define `async def _handler(msg_id, envelope)` closure
6. `autoclaim_ms = cfg.stream_ack_timeout * 1000`
7. `await run_consumer(r, _IN_STREAM, _GROUP, consumer, _handler, log, autoclaim_min_idle_ms=autoclaim_ms)`
8. `finally: await r.aclose()`

This is small enough that inlining is defensible for clarity, but the `autoclaim_ms` computation
(line 6) and consumer-name generation (line 4) are pure boilerplate.

**Fix:** Low priority. Could add `consumer_id()` and pass `autoclaim_from_config(cfg)` as
helpers, but the current inline form is also acceptable.

---

### Summary

| ID | Severity | Lines duplicated | Locations | Extractable to |
|----|----------|-----------------|-----------|----------------|
| DRY-1 | critical | 13 | 3 services | `lol_pipeline.models` or `lol_pipeline.streams` |
| DRY-2 | warning | 7-10 | 3 modules | `lol_pipeline.streams` (public export) |
| DRY-3 | warning | 12-15 | 3 services | `lol_pipeline.helpers.register_player` |
| DRY-4 | nit | 1 | 2 services | Import `DISCOVER_PLAYERS_KEY` from constants |
| DRY-5 | warning | 1 per site (9 sites) | 5+ modules | Use `is_system_halted()` everywhere |
| DRY-6 | warning | 25 | 2 services | `lol_pipeline.helpers.handle_riot_api_error` |
| DRY-7 | nit | 15 | 4 services | Optional helper; acceptable inline |

## Think Round 7: Async Correctness

**Date**: 2026-03-21

### Verdict: 2 VIOLATIONS FOUND

---

### ASYNC-1: SyntaxError — `except A, B:` must be `except (A, B):` (CRITICAL / P0)

**Severity**: CRITICAL — every affected module fails to import with `SyntaxError`
**Confidence**: 100%

The syntax `except ExceptionA, ExceptionB:` is invalid in Python 3 (has been since Python 3.0). It must be `except (ExceptionA, ExceptionB):` with parentheses. Python raises `SyntaxError: multiple exception types must be parenthesized` at import time, preventing the module from loading at all.

**Affected files (17 instances across 10 files)**:

| File | Line(s) | Expression |
|------|---------|------------|
| `lol-pipeline-common/src/lol_pipeline/service.py` | 124, 171 | `except RedisError, OSError:` |
| `lol-pipeline-common/src/lol_pipeline/riot_api.py` | 114, 147, 208 | `except ValueError, TypeError:` / `except ValueError, TypeError, OverflowError:` |
| `lol-pipeline-common/src/lol_pipeline/redis_client.py` | 29 | `except RedisConnectionError, RedisTimeoutError, OSError:` |
| `lol-pipeline-recovery/src/lol_recovery/main.py` | 236 | `except RedisError, OSError:` |
| `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py` | 206 | `except RedisError, OSError:` |
| `lol-pipeline-discovery/src/lol_discovery/main.py` | 126, 325 | `except ValueError, TypeError:` / `except RedisError, OSError:` |
| `lol-pipeline-crawler/src/lol_crawler/main.py` | 148 | `except ValueError, TypeError:` |
| `lol-pipeline-parser/src/lol_parser/main.py` | 340 | `except json.JSONDecodeError, TypeError:` |
| `lol-pipeline-ui/src/lol_ui/main.py` | 616, 2498, 3281, 3324 | `except ValueError, TypeError:` / `except json.JSONDecodeError, AttributeError:` / etc. |
| `lol-pipeline-admin/src/lol_admin/main.py` | 528 | `except KeyError, ValueError, TypeError:` |

**Impact**: No service can start. `service.py` and `riot_api.py` are imported by every consumer service (fetcher, crawler, parser, analyzer). `redis_client.py` is imported by every service that connects to Redis. The entire pipeline is non-functional.

**Fix**: Replace `except A, B:` with `except (A, B):` in all 17 locations.

For example, `service.py` line 124:
```python
# BEFORE (SyntaxError):
except RedisError, OSError:

# AFTER (correct):
except (RedisError, OSError):
```

---

### ASYNC-2: Blocking disk write on the event loop in `RawStore.set()` (MODERATE / P2)

**Severity**: Moderate — blocks the event loop for the duration of a synchronous file write
**Confidence**: 95%
**File**: `lol-pipeline-common/src/lol_pipeline/raw_store.py`, lines 161-164

In `RawStore.set()`, the `_exists_in_current_bundle` check on line 159 is properly delegated to `asyncio.to_thread`, but the actual disk write that follows is synchronous:

```python
# Line 159: correctly wrapped
if not was_set or await asyncio.to_thread(self._exists_in_current_bundle, match_id):
    return
try:
    # Lines 162-164: BLOCKING — runs on the event loop
    bp.parent.mkdir(parents=True, exist_ok=True)
    with bp.open("a") as f:
        f.write(f"{match_id}\t{data}\n")
```

The `mkdir()`, `open()`, and `write()` calls are synchronous OS operations that block the event loop thread. For a typical match JSON blob (10-50 KB), this stalls all other coroutines (Redis reads, HTTP requests, stream consumption) for the duration of the disk I/O.

**Impact**: Under load (many concurrent fetches), this serializes disk writes on the event loop, increasing latency for all concurrent Redis and HTTP operations in the fetcher. On slow storage (network mounts, spinning disks), the stall can be hundreds of milliseconds per write.

**Fix**: Extract the disk write into a synchronous helper method and delegate to `asyncio.to_thread`, consistent with how `_exists_in_current_bundle` is already handled:

```python
def _write_to_bundle(self, match_id: str, data: str) -> None:
    """Synchronous disk write — intended for asyncio.to_thread."""
    bp = self._bundle_path(match_id)
    if bp is None:
        return
    bp.parent.mkdir(parents=True, exist_ok=True)
    with bp.open("a") as f:
        f.write(f"{match_id}\t{data}\n")

async def set(self, match_id: str, data: str) -> None:
    was_set = await self._r.set(...)
    bp = self._bundle_path(match_id)
    if bp is None:
        return
    if not was_set or await asyncio.to_thread(self._exists_in_current_bundle, match_id):
        return
    try:
        await asyncio.to_thread(self._write_to_bundle, match_id, data)
    except OSError as exc:
        await self._r.delete(f"{_KEY_PREFIX}{match_id}")
```

---

### Items verified as CORRECT

| Check | Verdict | Reasoning |
|-------|---------|-----------|
| **No `time.sleep()` on event loop** | PASS | Zero instances found. All sleep calls use `await asyncio.sleep()`. |
| **Resource cleanup (Redis, httpx)** | PASS | Every service closes Redis (`await r.aclose()`) and RiotClient (`await riot.close()`) in `finally` blocks. UI uses lifespan context manager. |
| **Task cancellation safety** | PASS | SIGTERM sets a flag checked between loop iterations and mid-batch. `CancelledError` (BaseException in 3.9+) is not caught by `except Exception`, so it propagates correctly. Messages stay in PEL for reclaim on restart. |
| **No deadlock potential** | PASS | No `asyncio.Lock` or `asyncio.Semaphore` used anywhere. Redis locks use atomic Lua scripts. No nested await-inside-held-lock patterns. |
| **No blocking DNS** | PASS | No synchronous `socket.getaddrinfo` or DNS lookups. httpx uses its own async resolver. |
| **No concurrent consumer races** | PASS | Each consumer instance processes messages sequentially (one at a time). Cross-consumer conflicts are handled by Redis-level atomicity (Lua scripts, SET NX, consumer groups). |
| **`_ensured` cache safety** | PASS | `WeakKeyDictionary` is only accessed from the single event loop thread. Check-then-set between `await` points is safe in cooperative multitasking. |
| **`input()` in admin CLI** | ACCEPTABLE | `input()` blocks the event loop but admin is an interactive CLI tool, not a long-running service. No concurrent operations need to proceed during user input. |
| **Disk reads in RawStore** | PASS | `exists()` and `get()` both delegate synchronous file scanning to `asyncio.to_thread`. |

---

## Think Round 4: Doc Accuracy

Audit date: 2026-03-21. All findings verified against actual source: `rate_limiter.py`, `priority.py`, `crawler/main.py`, `streams.py`, `models.py`, `Dockerfile.service`, `docker-compose.yml`. Confidence threshold: 85%. Cosmetic/style issues excluded.

| # | Doc | Section | Issue | Fix |
|---|-----|---------|-------|-----|
| 1 | `docs/architecture/03-streams.md` | Message Envelope table | `correlation_id` and `dlq_attempts` are serialized by `models.py` `to_redis_fields()` and present on every stream entry, but neither field appears in the envelope table. | Add two rows: `correlation_id` (string, trace/correlation ID, default `""`) and `dlq_attempts` (integer, DLQ recovery attempt count, default `0`). |
| 2 | `docs/architecture/03-streams.md` | Stream Registry table | No maxlen values documented. `streams.py` defines: `stream:match_id` = 500,000; `stream:analyze` = 50,000; all other streams = 10,000 (`_DEFAULT_MAXLEN`); `stream:dlq` and `stream:dlq:archive` hardcode 50,000. | Add a `Maxlen (~)` column to the stream registry table with these values. |
| 3 | `docs/architecture/04-storage.md` | Redis Key Schema table | `priority:active` (Set, no TTL) is absent. `priority.py` defines `PRIORITY_ACTIVE_SET = "priority:active"`, written by `set_priority()` and `clear_priority()`, read by `has_priority_players()` via SCARD to gate Discovery in O(1). | Add row: `priority:active` / Set / none / "member=puuid; set of players with active priority; read by Discovery `_is_idle()` via SCARD." |
| 4 | `docs/architecture/04-storage.md` | Champion Analytics Keys table | `player:rank:history:{puuid}` (Sorted Set, 30d TTL) is absent. `crawler/main.py` writes `ZADD hist_key {"{tier}:{division}:{lp}": epoch_ms}` with `PLAYER_DATA_TTL_SECONDS` on each successful rank fetch. | Add row: `player:rank:history:{puuid}` / Sorted Set / 30d (`PLAYER_DATA_TTL_SECONDS`) / "member=`{tier}:{division}:{lp}`, score=epoch ms; rank snapshot timeline written by Crawler." |
| 5 | `docs/architecture/05-rate-limiting.md` | Python Usage — `acquire_token()` | Described as returning `True`/`False` (bool); code shows `return int(result) == 1`. Actual signature is `-> int`: returns `1` on grant, or a **negative** int where `abs(value)` = estimated ms to wait on denial. | Update prose and code snippet. Return type is `int`. On denial, `abs(result)` = ms until next slot. Remove the `== 1` bool cast. |
| 6 | `docs/architecture/05-rate-limiting.md` | Python Usage — `wait_for_token()` | Described as "polling every 50ms" with fixed `asyncio.sleep(0.05)`. Actual: uses the negative wait hint from `acquire_token()` to sleep precisely, adds 10–50% random jitter to prevent thundering herd, and raises `TimeoutError` after `max_wait_s=60.0` seconds. | Rewrite: remove fixed 50ms; document adaptive sleep from hint + jitter; note `max_wait_s` and `TimeoutError`. |
| 7 | `docs/architecture/05-rate-limiting.md` | Lua Script section | Documented script: reads stored limits via hardcoded string `redis.call("GET", "ratelimit:limits:short")`; uses 2 KEYS; returns `0` on denial. Actual script (`rate_limiter.py`): reads limits via `KEYS[3]`/`KEYS[4]` (Redis Cluster safe); uses 4 KEYS; returns negative wait-hint on denial; clamps dynamic limits to fallback if < 1. | Replace Lua block with actual script from `lol-pipeline-common/src/lol_pipeline/rate_limiter.py`. Update KEYS[1–4] / ARGV[1–6] comment header. |
| 8 | `docs/architecture/06-failure-resilience.md` | Failure Modes table | Row "XADD succeeds, ZREM fails (Scheduler): Duplicate delivered to target stream; handled idempotently." `_DISPATCH_LUA` in `delay_scheduler/main.py` now has `local exists = redis.call("ZSCORE", zkey, member); if not exists then return 0 end` before XADD, preventing duplicate dispatch on crash-restart. The described scenario is now actively prevented. | Update row: "XADD+ZREM atomic within `_DISPATCH_LUA`; `ZSCORE` guard skips XADD if member already removed (crash-restart safe). Duplicates remain possible only on multi-instance misconfiguration; consumers handle idempotently." |
| 9 | `docs/architecture/07-containers.md` | Image Design / Base Image | Documents `base.Dockerfile` as a separate file "built once; used by all services." This file does not exist. `Dockerfile.service` is fully self-contained with `python:3.14-slim` in both stages. | Remove the `base.Dockerfile` block. State that `Dockerfile.service` is self-contained. |
| 10 | `docs/architecture/07-containers.md` | docker-compose.yml section | Shown YAML uses old per-service pattern: `context: lol-pipeline-crawler`, `args: COMMON_VERSION: local`, `command: sh -c "pip install -q -e /common ..."`. Real `docker-compose.yml`: `x-service-build` anchor pointing to `Dockerfile.service` at repo root; `SERVICE_NAME`/`MODULE_NAME` build args; `x-worker-defaults` with Redis-ping healthchecks; direct `command: ["python", "-m", "lol_crawler"]`. | Replace or mark YAML as outdated. Accurately describe `x-service-build`, `x-worker-defaults`, `Dockerfile.service` pattern. |
| 11 | `ARCHITECTURE.md` | Implementation Phases table | Phase 20 (INSIGHT, v2.2.0) missing. Latest commit: `v2.2.0 — Phase 20 INSIGHT: champion analytics, priority tiers, crawler improvements, UI redesign`. | Add row: `| 20 | INSIGHT — Champion analytics, priority tiers, crawler improvements, UI redesign (v2.2.0) |` |

## Think Round 2: Test Coverage Gaps

Analysis date: 2026-03-21. Compared source files to test files across all 11 services.
Focus: recently-changed files (service.py, rate_limiter.py, priority.py, streams.py, all main.py).

### Gap 1: `service.py` — `_dispatch_batch` shutdown mid-batch (confidence: 95%)

**Source:** `lol-pipeline-common/src/lol_pipeline/service.py` lines 110-114
**Test file:** `lol-pipeline-common/tests/unit/test_service.py`

The `shutdown_check()` callback inside `_dispatch_batch` that skips remaining messages
when shutdown is flagged mid-batch has zero direct test coverage. This is branching logic
in an error-handling path. No test verifies the "shutdown mid-batch -- skipping remaining"
behavior where some messages in a batch are processed and others are skipped.

```
test_dispatch_batch__shutdown_mid_batch__skips_remaining
```

### Gap 2: `streams.py` — `_archive_corrupt` audit trail (confidence: 90%)

**Source:** `lol-pipeline-common/src/lol_pipeline/streams.py` lines 76-104
**Test file:** `lol-pipeline-common/tests/unit/test_streams.py`

`TestCorruptMessageHandling.test_consume__corrupt_entry_acked_and_skipped` verifies that
corrupt entries are acked and not returned, but does NOT assert that corrupt messages are
written to `stream:dlq:archive`. The `_archive_corrupt` function's XADD to the archive
stream is untested -- no test checks `r.xlen("stream:dlq:archive")` or verifies the
archived fields (`failure_code`, `failure_reason`, `original_stream`, `raw_fields`).

```
test_consume__corrupt_entry__archived_to_dlq_archive
```

### Gap 3: `fetcher/main.py` — `seen:matches` TTL conditional guard (F5 fix) (confidence: 95%)

**Source:** `lol-pipeline-fetcher/src/lol_fetcher/main.py` lines 62-64
**Test file:** `lol-pipeline-fetcher/tests/unit/test_main.py`

The F5 fix added: "Only set TTL when none exists (`ttl < 0`) to avoid resetting expiry
on every write." The existing test `test_fetcher_adds_to_seen_set` checks that TTL is set
on first write, but there is no test verifying that a *second* fetch does NOT reset the
TTL. The conditional `if seen_ttl < 0` branch is only half-tested (the true-branch on
first write). The false-branch (TTL already exists, skip EXPIRE) is untested.

```
test_seen_matches__second_fetch__does_not_reset_ttl
```

### Gap 4: `crawler/main.py` — `_compute_activity_rate` low-activity tier (confidence: 88%)

**Source:** `lol-pipeline-crawler/src/lol_crawler/main.py` lines 333-340
**Test file:** `lol-pipeline-crawler/tests/unit/test_main.py`

The function has 3-way branching: `rate > 5` (2h cooldown), `rate > 1` (6h cooldown),
`else` (24h cooldown). Tests cover the high-activity tier (`test_activity_rate__high_rate_short_cooldown`)
and compute behavior (`test_activity_rate__computed_after_crawl`), but no test exercises
the low-activity path (rate <= 1 -> 24h cooldown) or the medium tier (1 < rate <= 5 -> 6h).

```
test_activity_rate__low_rate__24h_cooldown
test_activity_rate__medium_rate__6h_cooldown
```

### Gap 5: `streams.py` — `consume_typed` XAUTOCLAIM corrupt entry handling (confidence: 87%)

**Source:** `lol-pipeline-common/src/lol_pipeline/streams.py` lines 173-183
**Test file:** `lol-pipeline-common/tests/unit/test_streams.py`

The XAUTOCLAIM path within `consume_typed` has its own deserialization + archive logic
(lines 177-182) that is separate from `_deserialize_entries_typed`. When an autoclaimed
entry has corrupt fields, it should be archived and acked. While recovery service tests
(`TestConsumeDlqCorruptInXautoclaim`) test this path through `consume_typed` with
`DLQEnvelope` deserializer, the generic `consume()` path (with `MessageEnvelope`
deserializer) has no test for corrupt entries within autoclaim results. Only the PEL
drain and new-message paths are tested for corruption in `test_streams.py`.

```
test_consume__autoclaim_corrupt_entry__archived_and_skipped
```

### Summary

| # | File | Gap | Tests |
|---|------|-----|-------|
| 1 | `service.py` | `_dispatch_batch` shutdown mid-batch | 1 |
| 2 | `streams.py` | `_archive_corrupt` audit trail verification | 1 |
| 3 | `fetcher/main.py` | `seen:matches` conditional TTL (F5) | 1 |
| 4 | `crawler/main.py` | Activity rate low/medium tiers | 2 |
| 5 | `streams.py` | Autoclaim corrupt entry in `consume()` | 1 |
| **Total** | | | **6** |

### Coverage that is adequate (no gaps found)

- **priority.py**: 35 tests covering all functions, tiers, ordering, TTL, downgrade, idempotency.
- **rate_limiter.py**: 22 tests covering stored limits, fallbacks, floor guard, cluster compat, wait semantics, timeout, throttle.
- **service.py** retry logic: 12 tests covering retry counter, TTL, persistence, DLQ nack, priority reordering.
- **streams.py** core ops: publish, consume, ack, nack, group creation, caching, NOGROUP invalidation, replay.
- **seed/main.py**: 22 tests covering happy path, cooldown, errors, region normalization, priority, CLI.
- **parser/main.py**: 45+ tests covering parse, validation, participants, discovery, bans, matchups, timeline, idempotency.
- **analyzer/main.py**: 40+ tests covering lock, cursor, derived stats, champion stats, TTL, ownership, pipeline.
- **recovery/main.py**: 35+ tests covering all failure codes, backoff, archive, atomic requeue, shutdown, DLQ corruption.
- **delay-scheduler/main.py**: 35+ tests covering timing, batch, dispatch, Lua guard, circuit breaker, shutdown.
- **discovery/main.py**: 45+ tests covering idle check, name resolution, promotion, priority, recrawl, shutdown.
- **admin/main.py**: 70+ tests covering all CLI commands, formatting, validation, error prefixes.
- **All common modules** (config, log, models, redis_client, raw_store, riot_api, helpers, resolve): Adequate.
