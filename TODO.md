# TODO — Open Work Items

---

## LFS-1 — Git LFS: `.gitignore` + `.gitattributes` wiring
**Decisions**: D-5 (only `*.jsonl.zst` in LFS), D-6 (all files, no rotation), D-7 (exclude AOF)
**Fix**:
1. Remove from `.gitignore`: lines matching `pipeline-data/**/*.jsonl` (line 73), `pipeline-data/**/*.jsonl.zst` (line 74), `lol-pipeline-fetcher/match-data/**/*.jsonl.zst` (line 82); replace `redis-data/` (line 38) with `redis-data/appendonlydir/` + `redis-data/*.rdb` + `redis-data/*.rdb.tmp`
2. Add to `.gitattributes`: `pipeline-data/**/*.jsonl.zst filter=lfs diff=lfs merge=lfs -text`
3. Run `git lfs track "pipeline-data/**/*.jsonl.zst"` (idempotent)
- [ ] **Red:** Test that `git check-attr filter pipeline-data/riot-api/NA1/2026-02.jsonl.zst` returns `lfs`
- [ ] **Green:** Apply `.gitignore` + `.gitattributes` changes
- [ ] **Refactor:** Verify no other `.jsonl.zst` patterns conflict

---

## LFS-2 — Delete orphaned `lol-pipeline-fetcher/match-data/`
**Decisions**: D-4 (pipeline-data is canonical; match-data is orphaned duplicate)
**Fix**: Delete `lol-pipeline-fetcher/match-data/` directory. Update `.gitignore` line 82 (already covered in LFS-1). Check for any hardcoded references to `match-data/` in code/docs/Justfile.
- [ ] **Red:** Grep for `match-data` in all non-.gitignore files; confirm no live references
- [ ] **Green:** `rm -rf lol-pipeline-fetcher/match-data/`; update any stale references
- [ ] **Refactor:** Confirm `cfg.match_data_dir` in fetcher still points to `pipeline-data/riot-api` via env var

---

## LFS-3 — Initial LFS commit of all existing `.zst` files
**Decisions**: D-5, D-6
**Fix**: After LFS-1 `.gitattributes` is committed, stage all existing `pipeline-data/riot-api/NA1/*.jsonl.zst` files and commit. Git will detect LFS attribute and upload as LFS objects.
Note: requires `git-lfs` installed locally (`brew install git-lfs && git lfs install`).
- [ ] **Red:** `git lfs ls-files` shows no pipeline-data files before this task
- [ ] **Green:** `git add pipeline-data/riot-api/NA1/*.jsonl.zst && git commit`; verify `git lfs ls-files` shows them
- [ ] **Refactor:** `git lfs ls-files --size` confirms sizes match expected

---

## LFS-4 — `just compact-data` recipe (compress all `.jsonl` → `.zst` before pushing)
**Decisions**: D-5, D-8, D-9 (compress active month too, not just completed months)
**Fix**: Add Justfile recipe that:
1. Finds ALL `pipeline-data/riot-api/NA1/*.jsonl` files (including current active month)
2. Compresses each with `zstd -19 --rm` (removes original after compression succeeds)
3. Stages the new `.zst` files for LFS (`git add pipeline-data/`)
4. Prints: "Compacted N files. Stage and commit when ready."
Does NOT auto-commit (user reviews and commits manually).
Use-case: run before `git push` when sharing data updates.
- [ ] **Red:** Test: given a `.jsonl` file, `compact-data` produces a `.zst`, removes the `.jsonl`, and `zstd -d` can round-trip it
- [ ] **Green:** Implement recipe
- [ ] **Refactor:** Confirm active-month `.zst` can be decompressed back by `just up`

---

## LFS-5 — Internal seed-from-disk script (called by `just up`, not exposed)
**Decisions**: D-1, D-2, D-3, D-10 (internal, not top-level recipe)
**Fix**: New script `scripts/seed_from_disk.py` that:
1. Connects to Redis; checks if empty (`DBSIZE == 0`)
2. If not empty, exits immediately (no-op)
3. Reads `pipeline-data/riot-api/NA1/` files: `.jsonl.zst` sorted reverse-chronological (newest first), then active `*.jsonl` if present
4. For each file, streaming-decompresses and reads match IDs (tab-delimited field 0 per line)
5. Publishes each `match_id` to `stream:parse` as `MessageEnvelope(type="match", payload={"match_id": match_id})` with low priority
6. Logs: "Seeding N matches from disk (newest first)..."
NOT exposed as a top-level `just` recipe — called only from `just up`.
- [ ] **Red:** Unit test: empty Redis → publishes newest-first; non-empty → no-ops; streaming decompression doesn't OOM
- [ ] **Green:** Implement; test against real Redis container
- [ ] **Refactor:** Streaming decompression (pyzstd or subprocess `zstd -d -c`)

---

## LFS-6 — Extend `just up` with LFS pull + decompress + auto-seed
**Decisions**: D-9, D-10 (bake everything into `just up`; minimize top-level commands)
**Fix**: Extend the existing `just up` recipe in `Justfile` to:
1. Run `git lfs pull` if `.git` exists (no-op outside a git repo)
2. Decompress current month's `.zst` → `.jsonl` if `.jsonl` doesn't exist: `zstd -d pipeline-data/riot-api/NA1/$(date +%Y-%m).jsonl.zst 2>/dev/null || true`
3. Start services normally (`{{DC}} up -d`)
4. Wait for Redis ready; if Redis DBSIZE==0, run `python scripts/seed_from_disk.py` in background
New user UX: `git clone ... && just up` — everything works automatically.
- [ ] **Red:** Test: fresh clone simulation (no `.jsonl`, `.zst` present) → `just up` produces working `.jsonl` and queues seed
- [ ] **Green:** Implement; test end-to-end
- [ ] **Refactor:** Ensure `git lfs pull` failure (no LFS installed) degrades gracefully with a clear warning

---

## LFS-7 — Fix `/player/refresh` missing region validation
**Service**: lol-pipeline-ui
**Security**: MINOR-3 from R2 security review
**Fix**: In `lol-pipeline-ui/src/lol_ui/routes/stats.py`, add region validation to the `player_refresh` endpoint — same `_REGIONS_SET` check that `show_stats` uses at line 415.
- [ ] **Red:** Test that `POST /player/refresh` with `region="invalid"` returns 422
- [ ] **Green:** Add `if region not in _REGIONS_SET: return JSONResponse({"error": "invalid region"}, status_code=422)`
- [ ] **Refactor:** Confirm existing tests still pass (`just test-svc ui`)

---

## PRIN-CMN-01 — common: direct `os.environ.get()` bypasses Config in runtime-only modules
**Service**: lol-pipeline-common
**Principle**: 12-factor app
**Locations**: `constants.py:39,43-44`, `_service_data.py:11,17,20`, `priority.py:20,23`
**Fix**: Replace `os.environ.get()` with `Config` reads in these three modules — they only execute after `Config()` is constructed in `main()`. Do NOT convert `log.py` or `redis_client.py` — those set module-level constants at import time, before any `Config()` can exist; changing them would create an initialization-order failure. `riot_api.py:19-20` and `raw_store.py:19` need separate evaluation (see PRIN-CMN-07).

---

## PRIN-CMN-03 — common: `raw_store.py` mixes Redis ops, disk I/O, and migration compat
**Service**: lol-pipeline-common
**Principle**: One concern per module
**Locations**: `raw_store.py:30-179`
**Fix**: Extract bundle file search/decompression into a `BundleReader` class; separate legacy migration compat logic.

---

## PRIN-CMN-04 — common: `to_redis_fields` / `from_redis_fields` duplicated between envelopes
**Service**: lol-pipeline-common
**Principle**: DRY
**Locations**: `models.py:35-47` vs `models.py:87-106`; `models.py:49-62` vs `models.py:109-129`
**Fix**: Extract shared serialization/deserialization logic into a base class or module-level helper.

---

## PRIN-CMN-05 — common: `_parse_app_rate_limit` and `_parse_rate_limit_count` are near-identical
**Service**: lol-pipeline-common
**Principle**: DRY
**Locations**: `riot_api.py:75-119` vs `riot_api.py:122-148`
**Fix**: Unify into a single parameterized `_parse_rate_limit_header(header, field_name)` function.

---

## PRIN-CMN-06 — common: `service.py` mixes retry-key management with handler/dispatch orchestration
**Service**: lol-pipeline-common
**Principle**: Layered composition
**Locations**: `service.py:30-148`
**Fix**: Extract `_retry_key()`, `_incr_retry()`, `_clear_retry()` into a `RetryTracker` class; keep handler logic and dispatch orchestration above it.

---

## PRIN-CMN-07 — common: rate-limit window constants diverge between `riot_api.py` and `_rate_limiter_data.py`
**Service**: lol-pipeline-common
**Principle**: DRY + 12-factor app
**Locations**: `riot_api.py:19-20` (`RATE_LIMIT_SHORT_WINDOW_S`, `RATE_LIMIT_LONG_WINDOW_S` env-configurable); `_rate_limiter_data.py:63-66` (`_SHORT_WINDOW_MS = 1_000`, `_LONG_WINDOW_MS = 120_000` hardcoded)
**Fix**: The Lua rate limiter must read window values from the same source as the header parser. Either remove the env-var overrides on `riot_api.py:19-20` (locking both sides at 1s/120s) or make `_rate_limiter_data.py` read the same env vars so the two sides cannot silently diverge.

---

## PRIN-CRW-01 — crawler: Redis key patterns with 2+ usages constructed inline; no `_helpers.py`
**Service**: lol-pipeline-crawler
**Principle**: DRY
**Locations**: `main.py:156,212` (`crawl:cursor:{puuid}` — 2 usages), `main.py:276,377,392,419` (`player:{puuid}` — 4 usages), `main.py:357,398,405` (`player:matches:{puuid}` — 3 usages)
**Fix**: Create `_helpers.py` for crawler; add key-builder functions there (e.g., `_key_crawl_cursor(puuid)`, `_key_player(puuid)`, `_key_player_matches(puuid)`). Only extract patterns used 2+ times — do not extract single-use keys.

---

## PRIN-CRW-02 — crawler: `_crawl_player()` at 59 lines exceeds 40-line guideline
**Service**: lol-pipeline-crawler
**Principle**: Layered composition
**Locations**: `main.py:430-488`
**Fix**: Extract the 4-step crawl workflow into a `_run_crawl()` helper; reduce handler to thin orchestration.

---

## PRIN-CRW-03 — crawler: `_RANK_HISTORY_MAX` is dead backward-compat constant
**Service**: lol-pipeline-crawler
**Principle**: DRY
**Locations**: `main.py:38`
**Fix**: Remove `_RANK_HISTORY_MAX`; runtime already reads `cfg.crawler_rank_history_max`. Update any tests that still import from `lol_crawler.main`.

---

## PRIN-FET-01 — fetcher: match-status set+expire duplicated; stream constants not in `_constants.py`
**Service**: lol-pipeline-fetcher
**Principle**: DRY + One concern per module
**Locations**: `main.py:67-68` vs `main.py:215-216`; `main.py:25-27` (`_IN_STREAM`, `_OUT_STREAM`, `_GROUP` inline)
**Fix**: Extract `_set_match_status(r, match_id, status, ttl)` helper; create `_constants.py` and move stream/group constants there.

---

## PRIN-PAR-01 — parser: Redis key patterns with 2+ usages constructed inline
**Service**: lol-pipeline-parser
**Principle**: DRY
**Locations**: `main.py:83,108,187,212,221,280`; `_helpers.py:87,113,181,186,191,194`
**Fix**: Add key-builder functions to existing `_helpers.py` (e.g., `_key_player_matches(puuid)`, `_key_match(match_id)`, `_key_participant(match_id, puuid)`); replace only patterns used 2+ times. Static field-name constants (team IDs, participant fields) belong in `_constants.py`.

---

## PRIN-PAR-02 — parser: magic team IDs 100/200 and participant field map are inline literals
**Service**: lol-pipeline-parser
**Principle**: DRY + One concern per module
**Locations**: `_extract.py:92,96` (team IDs); `_helpers.py:27-72` (45 hardcoded field mappings)
**Fix**: Extract `_TEAM_ID_BLUE = 100`, `_TEAM_ID_RED = 200`, `_TEAM_ID_MAP` to `_constants.py`; move `_PARTICIPANT_FIELD_MAP` dict there too.

---

## PRIN-PAR-03 — parser: `_store_timeline_data()` and `_write_participants()` inline mixed concerns
**Service**: lol-pipeline-parser
**Principle**: Layered composition
**Locations**: `main.py:158-176` (pid mapping inline), `main.py:62-89` (trim logic inline)
**Fix**: Extract `_build_pid_mappings(participants)` helper; extract `_queue_player_matches_trim(pipe, puuids, cfg)` helper.

---

## PRIN-ANZ-04 — common: stale docstring in `consumer_id()` references non-existent analyzer
**Service**: lol-pipeline-common
**Principle**: One concern per module (doc accuracy)
**Locations**: `_helpers.py:79`
**Fix**: Remove "except the analyzer, which appends a UUID for lock deduplication" — the analyzer service no longer exists; player-stats and champion-stats both use plain `consumer_id()` without uuid appending.

---

## PRIN-REC-01 — recovery: hardcoded backoff list and consumer count/block defaults
**Service**: lol-pipeline-recovery
**Principle**: 12-factor app
**Locations**: `main.py:138` (`_DEFAULT_BACKOFF_MS`), `main.py:37-38` (`count=10`, `block=5000`)
**Fix**: Remove `_DEFAULT_BACKOFF_MS` — Config is the single source; expose `count`/`block` via Config fields.

---

## PRIN-REC-02 — recovery: `_archive()` inlines multi-step Redis pipeline
**Service**: lol-pipeline-recovery
**Principle**: Layered composition
**Locations**: `main.py:75-95`
**Fix**: Extract `_archive_with_match_status(pipe, dlq, cfg)` helper to separate pipeline construction from the handler.

---

## PRIN-DLY-01 — delay-scheduler: hardcoded `_BATCH_SIZE` and circuit-breaker thresholds
**Service**: lol-pipeline-delay-scheduler
**Principle**: 12-factor app
**Locations**: `main.py:38`, `_circuit_breaker.py:14-15`
**Fix**: Source both values exclusively from Config; remove module-level hardcoded fallbacks.

---

## PRIN-DLY-02 — delay-scheduler: mutable module-level state in `_circuit_breaker.py`
**Service**: lol-pipeline-delay-scheduler
**Principle**: One concern per module
**Locations**: `_circuit_breaker.py:8-15`
**Fix**: Encapsulate state in a `CircuitBreakerState` class; replace bare module globals with an instance.

---

## PRIN-DLY-03 — delay-scheduler: `_dispatch_member()` inlines Redis ops and field flattening
**Service**: lol-pipeline-delay-scheduler
**Principle**: Layered composition
**Locations**: `main.py:98-131` (r.eval() inline at 111-117, r.delete() at 121, flat_args construction at 105-110)
**Fix**: Extract `_execute_dispatch_lua(r, ...)` and `_build_dispatch_args(member, ml, fields)` helpers.

---

## PRIN-DLY-04 — delay-scheduler: late import of `_CIRCUIT_OPEN_TTL_S` inside loop body
**Service**: lol-pipeline-delay-scheduler
**Principle**: One concern per module
**Locations**: `main.py:151`
**Fix**: Move import to module-level with other circuit-breaker imports.

---

## PRIN-DSC-01 — discovery: `"system:halted"` and `"players:all"` hardcoded as string literals
**Service**: lol-pipeline-discovery
**Principle**: DRY
**Locations**: `main.py:200`, `main.py:230`
**Fix**: Import `SYSTEM_HALTED_KEY` from `lol_pipeline.constants`; define `PLAYERS_ALL_KEY` constant in common or `_constants.py`.

---

## PRIN-DSC-02 — discovery: `_DEFAULT_REGION` is mutable module global mutated at startup
**Service**: lol-pipeline-discovery
**Principle**: 12-factor app
**Locations**: `_helpers.py:11,14-17`
**Fix**: Pass `default_region: str` as an explicit parameter to `_parse_member()` (the only function that reads it — 2 call sites in `main.py:168,188`); remove `init_default_region()` and the module global. Note: tests call `_parse_member` without calling `init_default_region()` first, implicitly relying on module-state default `"na1"` — parameterizing the function will surface and fix this latent test-isolation bug.

---

## PRIN-ADM-01 — admin: `cmd_player.py` mixes player-targeted and global-scan subcommands
**Service**: lol-pipeline-admin
**Principle**: One concern per module
**Locations**: `cmd_player.py:1-151`
**Fix**: Split into 2 files along the natural cohesion boundary: `cmd_player_ops.py` (cmd_reseed, cmd_reset_stats, cmd_clear_priority — all take a Riot ID) and `cmd_player_scans.py` (cmd_recalc_priority, cmd_recalc_players — both do global key scans). Update `main.py:56-62` (re-export block) and `_dispatch.py:21-27` (import list) accordingly.

---

## PRIN-ADM-02 — admin: `r.scan_iter("player:priority:*")` duplicated in two subcommands
**Service**: lol-pipeline-admin
**Principle**: DRY
**Locations**: `cmd_player.py:95`, `cmd_player.py:119`
**Fix**: Extract `_scan_priority_keys(r)` helper to `_helpers.py`.

---

## PRIN-ADM-03 — admin: `cmd_backfill.py` and `cmd_dlq.py` inline Lua eval and Redis pipeline in handlers
**Service**: lol-pipeline-admin
**Principle**: Layered composition
**Locations**: `cmd_backfill.py:86-157`, `cmd_dlq.py:92-109`
**Fix**: Extract Lua eval + participant-matching into `_helpers.py`; handlers should orchestrate, not implement.

---

## PRIN-ADM-04 — admin: `cmd_opgg.py` inlines path fallback logic instead of using Config
**Service**: lol-pipeline-admin
**Principle**: 12-factor app
**Locations**: `cmd_opgg.py:24-29`
**Fix**: Move `opgg_match_data_dir` fallback derivation into Config; handler reads one resolved value.

---

## PRIN-AUI-01 — admin-ui: auth + Redis boilerplate duplicated in every route (5×)
**Service**: lol-pipeline-admin-ui
**Principle**: DRY
**Locations**: `main.py:52-54, 73-75, 107-109, 125-127, 142-144`
**Fix**: Replace with a FastAPI `Depends` dependency that handles auth check and Redis injection once.

---

## PRIN-AUI-02 — admin-ui: route handlers mix HTTP with business logic
**Service**: lol-pipeline-admin-ui
**Principle**: One concern per module
**Locations**: `main.py:49-157` (list_dlq, replay_dlq_entry, clear_dlq, system_halt, system_resume)
**Fix**: Extract business logic into helpers (e.g., `_replay_entry()`, `_clear_dlq()`); routes should delegate, not implement.

---

## PRIN-AUI-03 — admin-ui: `ADMIN_UI_SECRET` defaults to empty string
**Service**: lol-pipeline-admin-ui
**Principle**: 12-factor app (security)
**Locations**: `main.py:17`
**Fix**: Remove empty-string default; service must refuse to start if `ADMIN_UI_SECRET` is unset or empty.

---

## PRIN-UI-01 — ui: route handlers generate HTML/JS inline; inline CSS styles pervasive
**Service**: lol-pipeline-ui
**Principle**: One concern per module
**Locations**: `routes/dashboard.py:63-138`, `routes/players.py:101-188`, `routes/dlq.py:68-103`, `routes/streams.py:29-77`, `routes/logs.py:85-134`; inline `style=` attributes across `dlq_helpers.py`, `rank.py`, `rendering.py`
**Fix**: Extract inline JS/sort-link/region-select helpers to rendering modules; replace inline `style=` with CSS utility classes in the shared stylesheet.

---

## PRIN-UI-02 — ui: `__main__.py` hardcodes host `0.0.0.0` and reload path `/svc/src`
**Service**: lol-pipeline-ui
**Principle**: 12-factor app
**Locations**: `__main__.py:11,15,20`
**Fix**: Source `UI_HOST` from env var; remove or guard the `reload_dirs` path behind a `DEBUG` env flag.

---

## PRIN-CHS-01 — champion-stats: participant/match validation block duplicated
**Service**: lol-pipeline-champion-stats
**Principle**: DRY
**Locations**: `main.py:66-70` vs `main.py:165-169`
**Fix**: Extract `_extract_ranked_context(match_data, puuid) -> RankedContext | None` helper to `_helpers.py`.

---

## PRIN-CHS-02 — champion-stats: Redis key patterns inline across 4 locations
**Service**: lol-pipeline-champion-stats
**Principle**: DRY
**Locations**: `main.py:79,124,145,183`
**Fix**: Extract parameterized key-builder functions to `_helpers.py` (e.g., `_stats_key(champion, patch, position)`, `_builds_key(...)`, `_matchup_key(...)`). Static string constants stay in `_constants.py`.

---

## PRIN-CHS-03 — champion-stats: `handle_champion_stats()` over-orchestrates; missing middle layer
**Service**: lol-pipeline-champion-stats
**Principle**: Layered composition
**Locations**: `main.py:194-228`
**Fix**: Extract `_analyze_player_matches(r, puuid, matches)` middle layer; handler becomes fetch → delegate → ack.

---

## PRIN-CHS-04 — champion-stats: missing `__main__.py` entry point
**Service**: lol-pipeline-champion-stats
**Principle**: Service layout
**Locations**: `src/lol_champion_stats/` (absent)
**Fix**: Add `__main__.py` following the standard pattern (`asyncio.run(main())`).
