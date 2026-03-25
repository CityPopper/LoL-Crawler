# TODO — Open Work Items

## Implementation Order

Two parallel lanes:

**Priority lane — sequential, in order:**
STRUCT-1 → STRUCT-2 → STRUCT-3

**Parallel lane — safe to run concurrently with STRUCT work (do not touch seed, analyzer, or UI):**
1. `TEST-1/2/3` + `BUG-1` — fast, unblock CI
2. `PRIN-COM-1..4` — common library foundation; do before per-service violations
3. `PRIN-XS-1..5` — cross-service DRY; do before per-service refactors (avoids double-editing)
4. Per-service violations on unaffected services: `PRIN-CRL-*`, `PRIN-FET-*`, `PRIN-PAR-*`, `PRIN-REC-*`, `PRIN-SCH-*`, `PRIN-DIS-*`, `PRIN-ADM-*`
5. `REFACTOR-1..8` — after PRIN-COM-* and PRIN-XS-* land
6. `IT-OPG-1` — after common is stable

**After all STRUCTs complete:**
- `PRIN-UI-1/2/3`, `REFACTOR-9/10/11` — apply to both new UI services
- `PRIN-XS-4` player-stats instance — apply after STRUCT-2

---

## Service Structure Changes

### STRUCT-1 · Merge `lol-pipeline-seed` → `lol-pipeline-admin` as `track` command
**Decision:** Seed is a single-operation CLI identical in mechanics to admin's `reseed`. Merge
eliminates a service, Docker image, and pyproject.toml. Rename `seed` → `track` (user intent
over database jargon).

**Implementation:**
- [ ] **Red:** Write failing test for `just admin track <GameName#TagLine>` in `lol-pipeline-admin/tests/`
- [ ] **Green:** Create `cmd_track.py` in `lol-pipeline-admin/src/lol_admin/` with seed logic (resolve Riot ID → PUUID, cooldown check, HSET player, publish to `stream:puuid`, set `PRIORITY_MANUAL_20`). Register as `track` sub-command in `main.py`. Update `Justfile`: replace `just seed` with `just admin track`. Update `docker-compose.yml` to remove seed service.
- [ ] **Refactor:** Delete `lol-pipeline-seed/` entirely. Update all docs that reference `seed` command or `lol-pipeline-seed`. Remove seed from `docs/architecture/02-services.md` service list; document `track` as admin sub-command.

**Note:** REFACTOR-7 (seed helpers extraction) is superseded — seed is deleted, not refactored.

---

### STRUCT-2 · Decompose `lol-pipeline-analyzer` → `lol-pipeline-player-stats` + `lol-pipeline-champion-stats`
**Decision:** Player history aggregation and champion meta-game analysis are independent concerns.
Separate services allow independent reprocessing and scaling.

**Implementation:**
- [ ] **Red:** Write failing tests for `lol-pipeline-player-stats` (player KDA, win rate, cursor-based processing, lock) and `lol-pipeline-champion-stats` (per-patch/role builds, runes, matchup aggregation) as new service packages.
- [ ] **Green:** Create `lol-pipeline-player-stats/` (consumer group `player-stats-workers`, writes `player:stats:{puuid}`, `player:champions:{puuid}`, `player:roles:{puuid}`, cursor, lock). Create `lol-pipeline-champion-stats/` (consumer group `champion-stats-workers`, writes `champion:stats:{champion}:{patch}:{role}`, builds, runes, matchups). Both consume `stream:analyze`. Update `docker-compose.yml` to replace `analyzer` with both new services.
- [ ] **Refactor:** Delete `lol-pipeline-analyzer/` entirely. Update architecture docs, PRIN-XS-4 (`"420"` constant) to reference `lol-pipeline-player-stats`. Re-evaluate REFACTOR-5 and PRIN-ANL-1 against the two new services.

---

### STRUCT-3 · Split `lol-pipeline-ui` into read-only UI + `lol-pipeline-admin-ui`
**Decision:** True process isolation — UI process has zero write access to Redis. New
`lol-pipeline-admin-ui` is a separate Docker service providing a web admin panel with write
capabilities (replay-player, DLQ management, system halt/resume).

**Implementation:**
- [ ] **Red:** Write failing tests for `lol-pipeline-admin-ui` routes (track player, DLQ list/replay/clear, system halt/resume). Write test asserting `lol-pipeline-ui` has no Redis write calls.
- [ ] **Green:** Create `lol-pipeline-admin-ui/` as a new FastAPI service. Move DLQ routes (`/dlq`), replay triggers, and halt/resume controls from `lol-pipeline-ui` to `lol-pipeline-admin-ui`. Strip all `r.xadd()`, `r.set()`, `r.delete()` calls from `lol-pipeline-ui`. Add `lol-pipeline-admin-ui` to `docker-compose.yml` (separate port, `profiles: ["tools"]` or always-on per preference). Redis client in `lol-pipeline-ui` should use a read-only connection or simply never call write methods.
- [ ] **Refactor:** Update `lol-pipeline-ui` docs and route list to note read-only posture. Update `PRIN-UI-1` and `PRIN-UI-2` — some violations may be split between the two services.

---

## Production — Needs Investigation

### BUG-1 · UI: Lag fallback value disputed — "0" vs "?" for unknown consumer-group lag
**Service:** `lol-pipeline-ui`
**Files:** `src/lol_ui/streams_helpers.py:74` AND `src/lol_ui/streams_helpers.py:135` (duplicate code path)
**Status:** Conflicting signals — needs resolution before fixing.

Commit `2a543cd` deliberately changed the fallback FROM `"?"` TO `"0"` with message "UI-L5: fix null-lag '?' → '0' for consistency". The test `test_lag_none__shows_question_mark` predates that commit and was not updated. Either:
- The commit is the spec → test assertion is stale (TEST-only fix)
- `"?"` is correct → commit message is wrong and `"0"` is a production bug

Additionally, Redis 7.x `XINFO GROUPS` returns `null` lag when lag tracking is unavailable — semantically different from `lag == 0` (healthy). Verify against actual Redis 7.x output before deciding.

**Note:** Fix must touch BOTH line 74 (`_format_group_cells`) AND line 135 (inline copy in `_streams_fragment_html`) — fixing only one creates inconsistency between single-group and multi-group rendering paths.

---

## Test Failures (assertions need updating — not production bugs)

### TEST-1 · common: Hypothesis fuzz tests — zero coverage risk (4 tests)
**Service:** `lol-pipeline-common`
**File:** `tests/unit/test_raw_store_fuzz.py` — `TestSearchBundleFileFuzz` (all 4 tests)
**Root cause:** `tmp_path` is function-scoped but used inside `@given(...)` — Hypothesis rejects before generating a single example (FailedHealthCheck).

**Risk:** If these tests have never passed since being added, `RawStore._search_bundle_file` may have had zero property-based coverage. Verify function correctness independently before fixing test infrastructure.

**Fix:** Replace `tmp_path` fixture with an inline `tempfile.TemporaryDirectory()` context manager inside each test body. Do NOT use `tmp_path_factory` (session-scoped — makes file-sharing between examples worse).

---

### TEST-2 · UI: Stream depth assertions use stale HTML pattern (2 tests)
**Service:** `lol-pipeline-ui`
**File:** `tests/unit/test_main.py` — `TestStreamsFragmentHtmlEdgeCases`
**Root cause:** Stream depth cells now include a `<div class="depth-bar">` visualization. Tests assert the old bare `<td>5</td>` pattern.
**Fix:** Update assertions to match current HTML structure (depth number + bar wrapper).

---

### TEST-3 · UI: i18n change breaks tier/role/rank assertions (4 tests)
**Service:** `lol-pipeline-ui`
**File:** `tests/unit/test_main.py` — `TestRankHistoryHtml`, `TestRenderRoleRows`, `TestStatsPageRankDisplay`
**Root cause:** i18n now renders `"GOLD"→"Gold"`, `"TOP"→"Top"` etc. Four tests assert on raw uppercase values.

**Tests affected:**
- `TestRankHistoryHtml::test_single_entry__renders_table`
- `TestRankHistoryHtml::test_multiple_entries__renders_all_rows`
- `TestRenderRoleRows::test_no_breakdown__games_only`
- `TestStatsPageRankDisplay::test_stats_page__shows_rank_when_available` *(rank card renders correctly — failure is casing only)*

**Caution before fixing:** Verify no downstream consumers (CSS selectors, scraping integrations, accessibility patterns) depend on the raw uppercase tier/role names appearing in rendered HTML.

**Fix:** Update assertions to use localized label format, or assert on a data attribute/key that doesn't change with locale.

---

## Integration / E2E Tests — Improve API Coverage

Blanket "remove all mocking" was rejected (see below). The correct approach is targeted: keep failure-injection and rate-limit tests mocked (they test pipeline internals, not API contracts), and extend real-data coverage via captured fixture files.

**Why blanket de-mocking was rejected:**
- IT-03 and IT-05 inject specific 429→200 and 200→403 sequences to exercise DLQ recovery and `system:halted` — impossible to reproduce with a real API
- IT-07 publishes 200 requests; at Riot's 100 req/2min limit that's 240s minimum — well beyond the 120s CI timeout
- CI (`.github/workflows/ci.yml`) has no `RIOT_API_KEY` secret provisioned; key gates the `docker-build` job; Riot dev keys expire every 24h
- op.gg has no public API contract; ETL field-level assertions require known input

### IT-OPG-1 · Add op.gg fixture capture to `update-mocks` and add op.gg integration test
**Files:**
- `scripts/update_mocks.py` — extend to capture one op.gg match for a stable test player
- `lol-pipeline-common/tests/fixtures/` — add `opgg_match.json`, `opgg_summoner.json`
- `tests/integration/test_it15_opgg_pipeline.py` (new) — replay captured fixtures through full ETL pipeline

**Approach:** Use the existing live-capture → fixture-file → deterministic-replay pattern (same as `Pwnerer#1337` in `update-mocks`). Do NOT hardcode a live player by ladder rank — choose a stable account. Mocking stays for HTTP layer; real data is captured once via `just update-mocks`.

---

## Refactoring — One-Concern-Per-Module Compliance

The `developer.md` directive requires each file to have a single clear purpose, with shared helpers in `_helpers.py`. Tasks below were audited against the older "one function per file" wording and remain valid under the revised "one concern per module" principle — but each task should be re-evaluated at implementation time: if all functions in the file serve the same concern, extraction may not be warranted. Each task is a Feedback-Pattern candidate — run the doc-bookend before and after.

### REFACTOR-1 · common: Split `helpers.py` into per-concern modules
**Service:** `lol-pipeline-common`
**File:** `src/lol_pipeline/helpers.py`
**Functions:** `_sanitize`, `validate_name_lengths`, `name_cache_key`, `is_system_halted`, `consumer_id`, `register_player`, `handle_riot_api_error`
Rename to `_helpers.py` (standard naming). Optionally split by concern: name utilities (`_sanitize`/`validate_name_lengths`/`name_cache_key`), player registration (`register_player`), error handling (`handle_riot_api_error`).

---

### REFACTOR-2 · common: Extract helpers from `streams.py`, `service.py`, `models.py`
**Service:** `lol-pipeline-common`
**Files:**
- `src/lol_pipeline/streams.py` — `_invalidate_ensured`, `_maxlen_for_replay`, `maxlen_for_stream`
- `src/lol_pipeline/service.py` — `_retry_key`, `_incr_retry`
- `src/lol_pipeline/models.py` — `make_replay_envelope` (move out; `_now_iso`/`_new_id` are field factory helpers, leave in place)
Move the above to `_helpers.py` (or a `_streams_helpers.py` for the streams utilities).

---

### REFACTOR-3 · common: `_opgg_etl.py` — single public entry point
**Service:** `lol-pipeline-common`
**File:** `src/lol_pipeline/_opgg_etl.py`
**Functions:** `_normalize_participant`, `_normalize_team`, `normalize_game`
Move `_normalize_participant` and `_normalize_team` to `_helpers.py`. Keep `normalize_game` as the sole export of `_opgg_etl.py`.

---

### REFACTOR-4 · parser: Split extraction functions out of `main.py`
**Service:** `lol-pipeline-parser`
**File:** `src/lol_parser/main.py`
**Functions (10):** `_normalize_patch`, `_extract_perks`, `_extract_full_perks`, `_extract_team_objectives`, `_validate`, `_queue_participant`, `_extract_timeline_events`, `_extract_gold_timelines`, `_extract_kill_events`, `_warn_non_monotonic_gold`, `_queue_pid_json`
Move extraction functions (`_normalize_patch`, `_extract_*`) to a new `_extract.py` module. Move validation/queuing helpers (`_validate`, `_queue_participant`, `_queue_pid_json`, `_warn_non_monotonic_gold`) to `_helpers.py`. `main.py` should retain only the consumer handler and entry point.

---

### REFACTOR-6 · delay-scheduler: Extract helpers from `main.py`
**Service:** `lol-pipeline-delay-scheduler`
**File:** `src/lol_delay_scheduler/main.py`
**Functions:** `_maxlen_for_stream`, `_is_circuit_open`, `_record_failure`, `_record_success`, `_is_envelope_id`
Move `_maxlen_for_stream` and `_is_envelope_id` to `_helpers.py`. Move the circuit-breaker trio (`_is_circuit_open`, `_record_failure`, `_record_success`) to a dedicated `_circuit_breaker.py`.

---

### REFACTOR-7 · discovery: Extract helpers from `main.py`
**Service:** `lol-pipeline-discovery`
**File:** `lol-pipeline-discovery/src/lol_discovery/main.py` — `_parse_member`, `_should_skip_seeded`, `_xinfo_groups_safe`
Move all listed functions to `_helpers.py`.

---

### REFACTOR-8 · admin: Move dispatch functions to `_dispatch.py`
**Service:** `lol-pipeline-admin`
**Files:**
- `src/lol_admin/main.py` — `_dispatch`, `_dispatch_dlq`, `_dispatch_dlq_archive`
- `src/lol_admin/cmd_opgg.py` — `_data_dir_size_mb`
Move the three dispatch functions to a new `_dispatch.py`. Move `_data_dir_size_mb` to `_helpers.py`. `main.py` becomes a pure entry point calling `_build_parser()` and routing through `_dispatch.py`.

---

### REFACTOR-9 · UI: Move route helpers out of route files
**Service:** `lol-pipeline-ui`
**Files:**
- `src/lol_ui/routes/stats.py` — `_build_participant_list`, `_build_minimap_events`, `_group_participants`, `_has_timeline_data` → move to `stats_helpers.py`
- `src/lol_ui/routes/matchups.py` — `_champion_datalist`, `_role_options` → move to `_helpers.py`
- `src/lol_ui/routes/logs.py` — `_service_filter_html` → move to `log_helpers.py`

---

### REFACTOR-10 · UI: Split `language.py` and `themes.py`
**Service:** `lol-pipeline-ui`
**Files:**
- `src/lol_ui/language.py` — `get_lang`, `set_lang_cookie` (request utilities) → move to `_helpers.py`; keep `language_switcher_html` in `language.py`
- `src/lol_ui/themes.py` — `get_theme`, `set_theme_cookie` → move to `_helpers.py`; keep `get_theme_css` and `theme_switcher_html` in `themes.py`

---

### REFACTOR-11 · UI: Extract helpers from rendering modules
**Service:** `lol-pipeline-ui`
**Files:**
- `src/lol_ui/match_history.py` — move `_match_history_section` to `_helpers.py`; keep `_match_history_html`
- `src/lol_ui/recently_played.py` — move `_count_co_players` to `_helpers.py`
- `src/lol_ui/ddragon.py` — move `_validate_ddragon_version` to `_helpers.py`

---

## Principle Violations — Cross-Service

### PRIN-XS-1 · DRY: `consumer_id()` reimplemented in 4 services
**Services:** recovery, discovery, delay-scheduler, admin
`f"{socket.gethostname()}-{os.getpid()}"` appears inline at:
- `lol-pipeline-recovery/src/lol_recovery/main.py:249`
- `lol-pipeline-discovery/src/lol_discovery/main.py:38`
- `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py:191`
- `lol-pipeline-admin/src/lol_admin/main.py:37`
**Fix:** Replace all 4 with `from lol_pipeline.helpers import consumer_id`. Remove `import socket, os` from each.

---

### PRIN-XS-2 · DRY: `is_system_halted()` bypassed via raw `r.get("system:halted")` in 4 services
**Services:** recovery, discovery, delay-scheduler, admin
Direct `await r.get("system:halted")` calls at:
- `lol-pipeline-recovery/src/lol_recovery/main.py:219,263`
- `lol-pipeline-discovery/src/lol_discovery/main.py:37`
- `lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py:144`
- `lol-pipeline-admin/src/lol_admin/main.py:86`
**Fix:** Replace all with `await is_system_halted(r)` from `lol_pipeline.helpers`.

---

### PRIN-XS-3 · DRY: `"delayed:messages"` not in `lol_pipeline.constants`
**Services:** delay-scheduler, recovery, discovery each define their own local `_DELAYED_KEY = "delayed:messages"`.
**Fix:** Add `DELAYED_MESSAGES_KEY = "delayed:messages"` to `lol-pipeline-common/src/lol_pipeline/constants.py`. Replace all 3 local definitions.

---

### PRIN-XS-4 · DRY: Ranked queue ID `"420"` hardcoded in 3 services
**Instances:**
- `lol-pipeline-player-stats/src/lol_player_stats/main.py` — will carry this after STRUCT-2 (was `lol-pipeline-analyzer/src/lol_analyzer/main.py:145`)
- `lol-pipeline-parser/src/lol_parser/_data.py:14` — `_RANKED_QUEUE_ID = "420"`
- `lol-pipeline-admin/src/lol_admin/cmd_backfill.py:151`
**Fix:** Add `RANKED_SOLO_QUEUE_ID = "420"` to `lol-pipeline-common/src/lol_pipeline/constants.py`. Replace all 3 uses.
**Dependency:** Apply player-stats instance after STRUCT-2 lands.

---

### PRIN-XS-5 · Naming: `_data.py` violates `_constants.py` convention across 3 services
**Services:** crawler, delay-scheduler, recovery each use `_data.py` for constants/Lua scripts.
(seed and analyzer are being deleted via STRUCT-1/STRUCT-2; new services should use `_constants.py` from the start.)
**Principle:** "Constants/types go in `_types.py` or `_constants.py`."
**Fix:** Rename `_data.py` → `_constants.py` in all 3 services. Update all imports.

---

## Principle Violations — Common Library

### PRIN-COM-1 · 12-factor: 8+ config values bypass Pydantic `Config` via raw `os.getenv()`
**Service:** `lol-pipeline-common`
- `src/lol_pipeline/constants.py:33` — `CHAMPION_STATS_TTL_SECONDS` uses `os.getenv`, not Config
- `src/lol_pipeline/constants.py:30` — `PLAYER_DATA_TTL_SECONDS` hardcoded `30 * 24 * 3600`
- `src/lol_pipeline/redis_client.py:11-12` — `_REDIS_SOCKET_TIMEOUT`, `_REDIS_CONNECT_TIMEOUT`
- `src/lol_pipeline/log.py:12-13` — `_LOG_LEVEL`, `_LOG_DIR` (Config already has `log_dir` — parallel read)
- `src/lol_pipeline/raw_store.py:20` — `_TTL_SECONDS` reads `RAW_STORE_TTL_SECONDS`; naming discrepancy with Config's `match_data_ttl_seconds`
- `src/lol_pipeline/_service_data.py:7` — `_MAX_HANDLER_RETRIES`; `:13` — `_MAX_NACK_ATTEMPTS = 3`; `:16` — `_RETRY_KEY_TTL = 604800`
- `src/lol_pipeline/priority.py:19` — `PRIORITY_KEY_TTL_SECONDS`
**Fix:** Add each as a `Config` field with env-var binding. Resolve `RAW_STORE_TTL_SECONDS` / `match_data_ttl_seconds` naming discrepancy (one name).

---

### PRIN-COM-2 · DRY: `RiotClient` routing pattern duplicated across 5 methods
**Service:** `lol-pipeline-common`
**File:** `src/lol_pipeline/riot_api.py:309,328,336,340,344`
All 5 public API methods repeat:
```python
routing = PLATFORM_TO_REGION.get(region, "americas")
base = _API_BASE.format(routing=routing)
```
**Fix:** Extract to `_resolve_base(self, region: str) -> str` and call from all 5 methods.

---

### PRIN-COM-3 · DRY: Circuit-breaker increment duplicated in two branches of `RiotClient._get`
**Service:** `lol-pipeline-common`
**File:** `src/lol_pipeline/riot_api.py:254-261,265-271`
Increment `_consecutive_5xx` → compare threshold → set `_circuit_open_until` → log appears in both the `except httpx.RequestError` branch and the `resp.status_code >= 500` branch.
**Fix:** Extract to `_on_server_error(self) -> None`.

---

### PRIN-COM-4 · Layered composition: 4 functions exceed 40-line limit
**Service:** `lol-pipeline-common`
- `src/lol_pipeline/streams.py:131-192` — `consume_typed` — 62 lines (drain PEL / XAUTOCLAIM / block-wait separable)
- `src/lol_pipeline/service.py:57-121` — `_handle_with_retry` — 65 lines (nack-with-fallback vs retry-count logic separable)
- `src/lol_pipeline/service.py:153-218` — `run_consumer` — 67 lines (signal setup / halt-check / idle-log separable)
- `src/lol_pipeline/riot_api.py:240-303` — `RiotClient._get` — 64 lines (circuit-breaker / rate-limit header persistence / throttle-hint separable)
**Fix:** Decompose each into sub-functions called in sequence.

---

## Principle Violations — Per Service

### PRIN-CRL-1 · 12-factor + DRY: Crawler operational constants hardcoded; `publish()` bypassed
**Service:** `lol-pipeline-crawler`
- `src/lol_crawler/_data.py:8` — `_PAGE_SIZE = 100`; `:9` — `_RANK_HISTORY_MAX = 500`; `:12` — `_RANK_TTL = 86400`; `:15` — `_CURSOR_TTL = 600`; `:18-25` — 5 cooldown constants (HIGH/MID/LOW rate+hours) — none have Config counterparts
- `src/lol_crawler/main.py:130` — `pipe.xadd(_OUT_STREAM, ...)` bypasses `publish()` from `lol_pipeline.streams`
**Fix:** Add the 5 operational params to `Config`. Replace `pipe.xadd()` with `publish()`.

### PRIN-CRL-2 · Layered composition: 3 functions exceed 40-line limit
**Service:** `lol-pipeline-crawler`
- `src/lol_crawler/main.py:374-470` — `_crawl_player` — 89 lines (7+ concerns: known-match load, crawl, error handling, metadata update, rank fetch, activity rate, priority clear, ack)
- `src/lol_crawler/main.py:272-335` — `_fetch_rank` — 55 lines (summoner fetch/update, league fetch, rank store/trim — 3 separable layers)
- `src/lol_crawler/main.py:180-243` — `_fetch_match_ids_paginated` — 48 lines
**Fix:** Decompose. `_crawl_player` should delegate to `_post_crawl_update()` and `_update_rank()` sub-functions.

---

### PRIN-FET-1 · 12-factor + DRY: Fetcher TTL mismatch and duplicate publish block
**Service:** `lol-pipeline-fetcher`
- `src/lol_fetcher/main.py:71` — `8 * 86400` (691200s = 8 days) hardcoded; `Config.seen_matches_ttl_seconds` defaults to 604800 (7 days). **Value mismatch — production config drift.**
- `src/lol_fetcher/main.py:91-100` and `:158-167` — `MessageEnvelope` construction + `publish()` + `ack()` block duplicated verbatim; extract to `_publish_and_ack()` helper
- `src/lol_fetcher/main.py:29` vs `:216` — two logger instances: module-level `logging.getLogger("fetcher")` and local `get_logger("fetcher")`; use `get_logger` exclusively
- `src/lol_fetcher/main.py:118` — `except (OpggParseError, OpggRateLimitError, json.JSONDecodeError, Exception)` — base `Exception` subsumes all specific types; remove specific types or remove base `Exception`

### PRIN-FET-2 · Layered composition: 2 functions exceed 40-line limit
**Service:** `lol-pipeline-fetcher`
- `src/lol_fetcher/main.py:126-211` — `_fetch_match` — 86 lines (halt check, payload extract, idempotency, op.gg fallback, rate limit, 4 exception branches, store+publish)
- `src/lol_fetcher/main.py:32-101` — `_store_and_publish` — 70 lines (raw store, seen-match bookkeeping, TTL, optional timeline, publish, ack)
**Fix:** Extract `_write_seen_match()` and `_fetch_timeline_if_needed()` from `_store_and_publish`.

---

### PRIN-PAR-1 · DRY + Layered: Parser perk extraction duplication and oversized functions
**Service:** `lol-pipeline-parser`
- `src/lol_parser/main.py:41-71` — `_extract_perks` (lines 41-49) and `_extract_full_perks` (lines 52-71) duplicate `p.get("perks", {})` / `styles[0]` / `styles[1]` access; `_extract_perks` is a strict subset of `_extract_full_perks`; callers at lines 122-123 call both sequentially on the same dict. Merge into one function.
- `src/lol_parser/main.py:522-638` — `_parse_match` — 117 lines (10+ concerns)
- `src/lol_parser/main.py:110-180` — `_queue_participant` — 71 lines; the 40-field mapping dict (lines 128-169) should be `_participant_fields() -> dict[str, str]`
- `src/lol_parser/main.py:252-321` — `_write_matchups` — 70 lines; team grouping / shared-position finding / Redis writes are 3 separable layers

---

### PRIN-REC-1 · 12-factor + DRY: Recovery hardcoded constants and duplicated archive write
**Service:** `lol-pipeline-recovery`
- `src/lol_recovery/_data.py:15` — `_STATUS_TTL = 604800` duplicates `Config.match_data_ttl_seconds`; read from Config instead
- `src/lol_recovery/_data.py:9` — `_CLAIM_IDLE_MS = 60_000` hardcoded (no Config field)
- `src/lol_recovery/_data.py:12` — `_BACKOFF_MS = [5_000, 15_000, 60_000, 300_000]` hardcoded backoff schedule (no Config field)
- `src/lol_recovery/main.py:239` — `_HALT_SLEEP_S = 5.0` hardcoded
- `src/lol_recovery/main.py:68,84` — `maxlen=50_000` hardcoded in two archive write calls
- `src/lol_recovery/main.py:66-86` — archive write block (`xadd(_ARCHIVE_STREAM, ...)`) duplicated in match-id branch and else-branch; extract to `_write_archive(r, dlq)` helper

---

### PRIN-SCH-1 · 12-factor (stateless-processes): Delay-scheduler mutable module-level globals
**Service:** `lol-pipeline-delay-scheduler`
**File:** `src/lol_delay_scheduler/main.py:32-35`
`_member_failures: dict[str, int]` and `_circuit_open: dict[str, float]` are mutable module-level dicts. On restart or scale-out, in-memory circuit state is silently lost.
**Fix:** Pass state explicitly through function arguments or store circuit state in Redis with TTL (consistent with delay-scheduler's already-Redis-backed design).

### PRIN-SCH-2 · 12-factor + Service isolation: Delay-scheduler hardcoded constants and private import
**Service:** `lol-pipeline-delay-scheduler`
- `src/lol_delay_scheduler/_data.py:11` — `_BATCH_SIZE = 100`; `:12` — `_MAX_MEMBER_FAILURES = 10`; `:13` — `_CIRCUIT_OPEN_TTL_S = 300` — no Config counterparts
- `src/lol_delay_scheduler/main.py:21` — `_POLL_INTERVAL_S = 1.0` hardcoded
- `src/lol_delay_scheduler/main.py:18` — `from lol_pipeline.streams import _DEFAULT_MAXLEN` imports a private underscore-prefixed symbol; expose as a public constant in `lol_pipeline.constants` or `lol_pipeline.streams`

---

### PRIN-DIS-1 · 12-factor + DRY: Discovery hardcoded constants and stream name redefinitions
**Service:** `lol-pipeline-discovery`
- `src/lol_discovery/main.py:19` — `_POLL_INTERVAL_S = 5.0`; `:20` — `_BATCH_SIZE = 50`; `:21` — `_IDLE_CUTOFF_DAYS = 3` — none have Config counterparts; add all 3 to `Config`
- `src/lol_discovery/main.py:43,46` — `_parse_member` falls back to hardcoded `"na1"` for malformed member strings; not configurable — wrong region for non-NA deployments
- `src/lol_discovery/_data.py:7` — `_STREAM_PUUID = "stream:puuid"` redeclares `lol_pipeline.constants.STREAM_PUUID` (already imported from common on line 5)
- `src/lol_discovery/_data.py:10-16` — `_PIPELINE_STREAMS` tuple hardcodes all 5 stream names as raw strings instead of composing from `lol_pipeline.constants` (`STREAM_PUUID`, `STREAM_MATCH_ID`, `STREAM_PARSE`, `STREAM_ANALYZE`, `STREAM_DLQ`)

---

### PRIN-ADM-1 · DRY + Layered: Admin duplications, bypasses, and redundant wrappers
**Service:** `lol-pipeline-admin`
- `src/lol_admin/cmd_replay.py:26-37` and `src/lol_admin/cmd_backfill.py:57-68` — `_scan_parsed_matches` function body identical in both files; extract to `_helpers.py`
- `src/lol_admin/_constants.py:5-9` — redefines `STREAM_PUUID/MATCH_ID/PARSE/ANALYZE/DLQ` already canonical in `lol_pipeline.constants`; delete `_constants.py` and import from common
- `src/lol_admin/_helpers.py:33-35` — `_maxlen_for_stream` is a one-line passthrough of `lol_pipeline.streams.maxlen_for_stream`; delete and import directly
- `src/lol_admin/_helpers.py:57` — `_make_replay_envelope = make_replay_envelope` is a bare alias; delete and import directly
- `src/lol_admin/cmd_backfill.py:94,149` — `90 * 86400` magic literal; use `CHAMPION_STATS_TTL_SECONDS` from `lol_pipeline.constants`
- `src/lol_admin/cmd_replay.py:59-60,80` — `r.xadd()` called directly instead of `publish()` from `lol_pipeline.streams`
- `src/lol_admin/_helpers.py:21-30` — mutable global `_log: Any = None` with lazy `global _log` init; replace with module-level `_log = get_logger("admin")`

---

### PRIN-UI-1 · 12-factor + DRY: UI hardcoded URLs/TTLs and duplicated constants
**Service:** `lol-pipeline-ui`
- `src/lol_ui/ddragon.py:56,80` — DDragon HTTP client `timeout=5.0` hardcoded in two separate `httpx.AsyncClient` instantiations
- `src/lol_ui/routes/stats.py:84` — `_CACHE_TTL_S = 6 * 3600` fragment cache TTL hardcoded; no Config field
- `src/lol_ui/__main__.py:10,18` — port `8080` hardcoded twice; should come from Config/env var
- `src/lol_ui/health.py:17` and `src/lol_ui/streams_helpers.py:9` — `_STREAM_KEYS` list defined independently in both files; define once and import

### PRIN-UI-2 · DRY: Role list, win-rate formula, and KDA formula each duplicated across many files
**Service:** `lol-pipeline-ui`
- **Role set** defined 3 times: `stats_helpers.py:77` as `_VALID_ROLES`, `constants.py:17` as `_MATCHUP_ROLES`, and inline in `routes/matchups.py:35` — all identical `frozenset({"TOP","JUNGLE","MIDDLE","BOTTOM","UTILITY"})`; define once in `constants.py`
- **Win-rate formula** `(wins / games * 100) if games > 0 else 0.0` duplicated at `champions_helpers.py:282-283`, `:326`, `routes/matchups.py:120`, `rank.py:28` — extract to `_win_rate(wins, games) -> float` in `_helpers.py`
- **KDA formula** `(kills + assists) / max(deaths, 1)` duplicated at `champions_helpers.py:283`, `:330`, `stats_helpers.py:64`, `match_badges.py:45`, `tilt.py:61`, `scoring/ai_score.py:112` — 6 locations across 5 files; extract to `_kda(kills, deaths, assists) -> float` in `_helpers.py`

### PRIN-UI-3 · Service isolation: `httpx` used directly in UI but not declared in `pyproject.toml`
**Service:** `lol-pipeline-ui`
**File:** `src/lol_ui/ddragon.py:10` — `import httpx` used directly for DDragon HTTP calls, but `httpx` is not declared in `lol-pipeline-ui/pyproject.toml`. It is a transitive dependency via `lol-pipeline-common`. Explicit dependencies must be declared.
**Fix:** Add `httpx` to `lol-pipeline-ui/pyproject.toml` dependencies.
