# TODO — Open Work Items

---

## SEED-1 — Fix `anonymize_and_upload.py` + run (one-time data migration)
**Decisions**: D-11, D-12. Script at `scripts/anonymize_and_upload.py` exists but has prod review issues to fix first.
**Dependency**: SEED-2 (compact-data) must run first so active `.jsonl` is converted to `.zst` before this script sees it.
**Fixes to apply to `scripts/anonymize_and_upload.py` before running**:
1. Add `api.create_repo(repo_id, repo_type="dataset", exist_ok=True)` once in `main()` before first upload (otherwise 404 on fresh HF repo)
2. Reverse order: `os.replace` (overwrite local) before `api.upload_file` — if killed mid-run, re-run re-uploads (deterministic SHA256); current order uploads anonymized version but leaves local as raw PII if `os.replace` fails
3. Change compression `level=3` → `level=19` (D-8 mandates max compression; script runs once, slow is fine)
4. Load token + resolve `repo_id` once in `main()`, pass to `_upload_file()` — no per-file `whoami()` calls
5. Use `python-dotenv` (`load_dotenv()`) instead of hand-rolled `.env` parser — handles inline comments
6. Fix `_is_already_anonymized`: check `any(p.get("puuid","").startswith("anon_") for p in participants)` instead of only first participant
7. Delete `1970-01.jsonl.zst` (anomalous bad-date file) before running — do not upload garbage-dated records to public dataset
8. Do NOT strip `riotIdGameName`/`riotIdTagline` — replace with `Player_{hash[:8]}` instead of removing entirely (parser writes these as display names; empty → no name shown in UI)
9. Add `HF_DATASET_REPO` env var support: prefer env var over `whoami()` derivation (download script also needs it for unauthenticated contributors)
Also clean up `.gitignore`: remove stale line 82 (`lol-pipeline-fetcher/match-data/**/*.jsonl.zst`); update pipeline-data block comment.
- [ ] **Red:** Unit test anonymize: PUUID replaced, riotIdGameName replaced with `Player_*` (not removed), consistent across records
- [ ] **Green:** Apply fixes 1-9; run `python scripts/anonymize_and_upload.py`; verify 25 files on HF
- [ ] **Refactor:** Spot-check uploaded file on HF: no raw PUUIDs, display name = `Player_*`

---

## SEED-2 — `just compact-data` recipe (compress active `.jsonl` → `.zst` before uploading)
**Decisions**: D-5, D-8, D-9. **Must run before SEED-1.**
**Fix**: Add Justfile recipe that:
1. Finds ALL `pipeline-data/riot-api/NA1/*.jsonl` files (including active month)
2. Compresses each with `python3 -c "import zstandard, pathlib; ..."` (use Python zstandard, not `zstd` CLI — not guaranteed on all machines)
   Alternatively use `zstd` CLI if available, fallback to Python
3. Removes original after compression succeeds
4. Prints: "Compacted N files. Run `python scripts/anonymize_and_upload.py` to anonymize and push to HF."
Does NOT auto-upload (user runs anonymize_and_upload.py separately).
- [ ] **Red:** Given a `.jsonl`, `compact-data` produces `.zst`, removes `.jsonl`, zstd round-trips it
- [ ] **Green:** Implement recipe with Python-based compression fallback
- [ ] **Refactor:** Confirm active-month `.zst` decompresses back by `just up`

---

## SEED-3 — `just download` + `scripts/download_seed.py` (restore JSONL.ZST + dump.rdb)
**Decisions**: D-1 (reversed — include dump.rdb), D-3, D-10.
**Fix**: New script `scripts/download_seed.py` + top-level Justfile recipe `download`:
- `just download` calls `python scripts/download_seed.py` — explicitly exposed to users
- `just up` also auto-calls it if `pipeline-data/riot-api/NA1/` has no `.jsonl.zst` files AND `redis-data/dump.rdb` doesn't exist
Script:
1. Reads `HF_DATASET_REPO` from env (required — set to actual owner's repo ID, e.g. `abhiregmi/lol-pipeline-seed`; public dataset so no token required for download)
2. Downloads `dump.rdb` → `redis-data/dump.rdb` (skip if already exists)
3. Downloads all `NA1/*.jsonl.zst` → `pipeline-data/riot-api/NA1/` (skip files that already exist)
4. Uses `huggingface_hub.snapshot_download(allow_patterns=["NA1/*.jsonl.zst", "dump.rdb"])`
5. Logs: `Downloading {filename}...`; `Done — Redis dump + {N} data files ready.`
Token optional (`HUGGINGFACE_TOKEN` env var avoids rate limits). Download is unauthenticated for public datasets.
Add `HF_DATASET_REPO=` (required) and `# HUGGINGFACE_TOKEN=` (optional) to `.env.example`.
- [ ] **Red:** Empty dirs → both populated; existing files → skipped (idempotent)
- [ ] **Green:** Implement; expose as `just download`; test against real HF repo (requires SEED-1 + SEED-7)
- [ ] **Refactor:** Handle partial download (verify zstd magic bytes after download; re-download if corrupt)

---

## SEED-4 — `scripts/seed_from_disk.py` (pipeline rebuild fallback — only if no dump.rdb)
**Decisions**: D-2, D-3. Prod fixes: region lookup, throttled batching, raw_store sort fix.
**Note**: With dump.rdb available (D-1 reversed), this script is the fallback path for when dump.rdb is unavailable or stale. `just up` prefers dump.rdb restore; runs this only if Redis is still empty after dump load.
**Fix**: New script `scripts/seed_from_disk.py` that:
1. Validates each `.zst` file starts with zstd magic bytes `0x28 0xB5 0x2F 0xFD`; if corrupt, abort ("run `python scripts/download_seed.py` first")
2. Connects to Redis via `get_redis()` from `lol_pipeline.redis_client`; if ZCARD players:all > 0, exit (no-op — use `players:all` not DBSIZE, which counts infra keys)
3. Reads `.jsonl.zst` files sorted reverse-chronological (newest first), active `*.jsonl` last
4. Streaming-decompresses; extracts `match_id` and platform prefix
5. Platform → routing region: `NA1/BR1/LA1/LA2 → americas`, `EUW1/EUN1/TR1/RU → europe`, `KR/JP1 → asia`, `OC1 → sea`
6. Publishes throttled batches of 200 to `stream:parse`; pause if `XLEN > DEFAULT_STREAM_MAXLEN // 2`
7. `MessageEnvelope(type="parse", payload={"match_id": match_id, "region": region}, priority=PRIORITY_AUTO_NEW)` — NOT `type="match"`, NOT `PRIORITY_LOW`
8. Logs stderr to `./logs/seed.log`; print "Seeding in background. Logs: ./logs/seed.log"
Also fix `raw_store.py:_search_bundles()`: wrap both glob calls in `sorted(..., reverse=True)`.
NOT a top-level `just` recipe.
- [ ] **Red:** Unit tests: platform→region; corrupt file detected; `players:all` > 0 → no-op; batch throttle; newest-first order
- [ ] **Green:** Implement; test against real Redis container
- [ ] **Refactor:** Confirm `type="parse"` and `PRIORITY_AUTO_NEW` match what parser expects

---

## SEED-4b — `just upload` recipe + fix `anonymize_and_upload.py` entry point
**Decisions**: D-10 (expose `just upload` as top-level command).
**Fix**: Add top-level Justfile recipe `upload` that:
1. Runs `just compact-data` (compress any active `.jsonl` → `.zst`)
2. Runs `python scripts/anonymize_and_upload.py` (anonymize + upload all `.zst` to HF)
3. Prints instructions to run SEED-7 manually for dump.rdb update
Also ensure `anonymize_and_upload.py` is runnable standalone (`python scripts/anonymize_and_upload.py`) as well as via `just upload`.
- [ ] **Red:** `just upload` runs both steps in order; errors in either step halt the recipe
- [ ] **Green:** Implement; test dry-run
- [ ] **Refactor:** Confirm `HF_DATASET_REPO` and `HUGGINGFACE_TOKEN` are validated early with clear errors

---

## SEED-5 — Extend `just up` with download + dump restore + AOF cleanup + fallback seed
**Decisions**: D-9, D-10. **Prod fixes**: AOF conflict; zstd CLI dependency; background seed logging.
**Fix**: Extend existing `just up` recipe in `Justfile` to (in order):
1. Download seed data: `python scripts/download_seed.py` (no-op if files exist; downloads dump.rdb + JSONL.ZST on fresh clone)
2. Delete stale AOF: `rm -rf "${REDIS_DATA_DIR:-./redis-data}/appendonlydir"` (bind mount survives down -v)
3. Decompress current month `.zst` → `.jsonl` if `.jsonl` doesn't exist (use Python, not `zstd` CLI — not installed by default):
   `python3 -c "import zstandard, pathlib, datetime; ..."`
4. Start services: `{{DC}} up -d`
5. Wait for Redis ready
6. If `players:all` cardinality is 0, fall back to: `python scripts/seed_from_disk.py >> ./logs/seed.log 2>&1 &`; print log path
**dump.rdb path**: Redis starts with dump.rdb already populated (downloaded by step 1) → `players:all` > 0 → step 6 is a no-op. Contributors get instant stats.
New UX: `git clone ... && just up` → download dump+data → AOF clean → decompress → start → done (no pipeline wait).
- [ ] **Red:** Empty repo → files downloaded; stale AOF → deleted; `.jsonl` missing → decompressed; dump.rdb present → Redis populated; `players:all` > 0 → step 6 skipped
- [ ] **Green:** Implement; test on simulated fresh clone
- [ ] **Refactor:** AOF deletion validates path doesn't escape project dir

---

## SEED-6 — Fix `/player/refresh` missing region validation
**Service**: lol-pipeline-ui
**Security**: MINOR-3 from R2 security review
**Fix**: In `lol-pipeline-ui/src/lol_ui/routes/stats.py`, add region validation to the `player_refresh` endpoint — same `_REGIONS_SET` check that `show_stats` uses at line 415.
- [ ] **Red:** Test that `POST /player/refresh` with `region="invalid"` returns 422
- [ ] **Green:** Add `if region not in _REGIONS_SET: return JSONResponse({"error": "invalid region"}, status_code=422)`
- [ ] **Refactor:** Confirm existing tests still pass (`just test-svc ui`)

---

## SEED-7 — Generate anonymized dump.rdb + upload to HF Datasets
**Decisions**: D-1 (reversed), D-7. One-time workflow to create the anonymized Redis snapshot.
**Dependency**: SEED-1 must complete first (local JSONL.ZST files must be anonymized before pipeline processes them).
**Fix**: After SEED-1 completes:
1. Start a fresh Redis with no data: `docker compose down -v && just up --no-seed`
2. Run seed_from_disk.py with anonymized JSONL.ZST → pipeline processes all matches
3. Wait for pipeline to drain (check `LLEN stream:parse == 0`, `LLEN stream:analyze == 0`)
4. Take snapshot: `docker compose exec redis redis-cli BGSAVE && sleep 5`
5. Copy dump: `cp redis-data/dump.rdb /tmp/seed-dump.rdb`
6. Upload to HF: `HfApi().upload_file(path_or_fileobj="/tmp/seed-dump.rdb", path_in_repo="dump.rdb", repo_id=repo_id, repo_type="dataset")`
7. Verify: check HF shows `dump.rdb` with expected size
This is a manual/one-shot operation, not a script. Document steps in `workspace/design-seed-data.md`.
- [ ] **Green:** Execute steps 1-7; HF Datasets contains both JSONL.ZST files + dump.rdb
- [ ] **Refactor:** Confirm dump loads correctly: fresh Redis start + `DBSIZE` > 0 after mount

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

---

## RL-1 — New `lol-pipeline-rate-limiter` service scaffold
**Decisions**: D-3, D-6 (rate-limiter questions file)
**Fix**: New service directory `lol-pipeline-rate-limiter/` with:
- `pyproject.toml` / `Dockerfile` following existing service pattern
- FastAPI app on port 8079 (internal Docker network, not exposed externally)
- `POST /token/acquire` — `{source, endpoint}` → `{granted: bool, retry_after_ms: int|null}`
- `POST /headers` — `{source, rate_limit, rate_limit_count}` → `{updated: bool, throttle: bool}` (raw Riot header strings)
- `GET /health` — `{status: "ok"}`
- `GET /status` — observability: current bucket state
- Bucket config via env vars (RATELIMIT_RIOT_SHORT_LIMIT, RATELIMIT_RIOT_SHORT_WINDOW_MS, etc.)
- Unknown `(source, endpoint)` returns 404
- Fail open if Redis unreachable (log warning, return `{granted: true}`)
- `docker-compose.yml` service entry with `depends_on: redis`
- [ ] **Red:** Unit tests for `/token/acquire`, `/headers`, bucket config parsing, unknown-source 404
- [ ] **Green:** Implement scaffold + FastAPI routes
- [ ] **Refactor:** `GET /status` shows live bucket ZSET cardinalities

---

## RL-2 — Port Lua dual-window logic into the rate limiter service
**Decisions**: D-1 (delete `_rate_limiter_data.py`); Lua script moves to service internals
**Fix**: Move the Lua sliding-window ZSET script from `lol-pipeline-common/src/lol_pipeline/_rate_limiter_data.py` into the rate limiter service's internal implementation. Service invokes it against the shared Redis container. Window constants become env var config (defaulting to short=1000ms/20, long=120000ms/100 for Riot). Delete `_rate_limiter_data.py` after migration.
- [ ] **Red:** Service integration test: 21 concurrent `/token/acquire` calls; 20 granted, 1 denied with `retry_after_ms > 0`
- [ ] **Green:** Implement + delete source file
- [ ] **Refactor:** `_parse_rate_limit_header` from `riot_api.py` also moves to service (used by `POST /headers`)

---

## RL-3 — Thin client `rate_limiter_client.py` in common; update all call sites
**Decisions**: D-4, D-9, D-11, D-12
**Fix**: In `lol-pipeline-common/src/lol_pipeline/`:
1. Add `rate_limiter_client.py` with:
   - `async def wait_for_token(source: str, endpoint: str, *, max_wait_s: float = 60.0) -> None` — calls `POST /token/acquire`, sleeps `retry_after_ms` with jitter, retries until granted or timeout
   - `async def try_token(source: str, endpoint: str) -> bool` — calls `POST /token/acquire` once, returns `granted`
   - `httpx.AsyncClient` with persistent connection pooling; service URL from `RATE_LIMITER_URL` env var
   - Fail open: if service unreachable, log warning and return (don't raise)
2. Delete `rate_limiter.py` and `_rate_limiter_data.py`
3. Update all 6 call sites (fetcher ×2, crawler ×3, discovery ×1, UI ×1, opgg_client ×1): change import + drop `r` param + add `source`/`endpoint`
4. Update fetcher pattern-A tests (lupa/fakeredis) to use `patch("...wait_for_token", AsyncMock)` (pattern B)
5. Delete `lol-pipeline-common/tests/unit/test_rate_limiter.py`
- [ ] **Red:** Each service's unit tests pass with mocked `wait_for_token`; `test_rate_limiter.py` deleted
- [ ] **Green:** Implement thin client; migrate call sites
- [ ] **Refactor:** Add `RATE_LIMITER_URL` to `.env.example` and `.env`

---

## RL-4 — Update `RiotClient._persist_rate_limits` to call `POST /headers`
**Decisions**: D-5
**Fix**: In `lol-pipeline-common/src/lol_pipeline/riot_api.py`, change `_persist_rate_limits` to call `POST /headers` on the rate limiter service instead of writing `ratelimit:limits:short/long` keys to Redis directly. If `throttle: true` returned, apply the 200ms proactive sleep (replaces `ratelimit:throttle` Redis key check). Also remove direct Redis writes for `ratelimit:limits:*` and `ratelimit:throttle`. PRIN-CMN-05 (duplicate parse functions) and PRIN-CMN-07 (constant divergence) are automatically resolved — move parsing to service.
- [ ] **Red:** `RiotClient` unit test: after a 200 response with rate-limit headers, verify `POST /headers` called (not Redis SET)
- [ ] **Green:** Implement; delete Redis writes for limit keys
- [ ] **Refactor:** Confirm PRIN-CMN-07 task can be removed (divergence gone)

---

## RL-5 — Update integration tests IT-07 and IT-12 for HTTP rate limiter
**Decisions**: D-10
**Fix**: In `tests/integration/`:
1. Add `rate_limiter` fixture in `conftest.py`: `GenericContainer` running `lol-pipeline-rate-limiter` image, pointing at the session-scoped Redis container; exposes URL via env var
2. Update `test_it07_rate_limit.py`: rate monitor still samples Redis ZSET cardinality directly; concurrency assertions unchanged; fetcher now calls HTTP service
3. Update `test_it12_concurrent_rate_limit.py`: 20 concurrent `wait_for_token()` calls go through HTTP; correctness assertions unchanged
- [ ] **Red:** Run IT-07 and IT-12 before fix; confirm they fail (service not found)
- [ ] **Green:** Add container fixture; update tests
- [ ] **Refactor:** Confirm session-scoped container teardown works with pytest-xdist
