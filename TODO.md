# TODO

## Bugs

### Just lcu issues
`Just LCU` is still having issues. The league server is running right now, troubleshoot and retry fixes until you get it to work.

### ~~LCU 403 on startup~~ — FIXED
Added `LcuAuthError` exception for HTTP 401/403 (stale lockfile). `_collect_with_auth_retry` retries up to 3× with 2s delay before giving up. 5 new tests added (24 total LCU tests passing).

---

## Performance Optimizations
Benchmark performance before and after to ensure that these changes actually improve performance.


### ~~Crawler: O(n) zrange on every crawl~~ — DONE
Uses `ZRANGEBYSCORE` with 7-day window when `last_crawled_at` exists; falls back to `ZRANGE` on first crawl. 3 new tests added.

### ~~Analyzer: individual HINCRBY calls per match~~ — DONE
Batched 5-7 HINCRBY/ZINCRBY calls into `r.pipeline(transaction=False)`. 1 new test added.

### ~~Delay-scheduler: unbounded zrangebyscore~~ — DONE
Paginated with `start=0, num=100` in a loop. 2 new tests added.

### ~~RawStore: full decompression for bundle search~~ — DONE
Uses `dctx.stream_reader()` + `io.TextIOWrapper` for line-by-line scanning. 1 new test added.

### ~~Parser: per-participant item serialization~~ — DONE
Precomputed item key names as module-level `_ITEM_KEYS` constant.

---

## Code Smells

### ~~Recovery: monolithic \_process function~~ — DONE
Refactored to `_HANDLERS` dict with `_handle_transient`, `_handle_404`, `_handle_parse_error`.

### ~~Common: duplicated bundle search logic~~ — DONE
Extracted `_find_in_lines()` helper; both methods delegate to it.

### ~~UI: duplicated by\_mode statistics~~ — DONE
Extracted `_aggregate_by_mode()` helper used by both `_lcu_stats_section` and `show_lcu`.

### ~~Common: unbounded handler\_failures dict~~ — DONE
Capped at `_MAX_FAILURE_ENTRIES=10,000`; evicts oldest on overflow. 1 new test.

---

## Anti-Patterns

### ~~Common: broad exception catch in health\_check~~ — DONE
Narrowed to `(RedisConnectionError, RedisTimeoutError, OSError)`.

### ~~Common: broad exception catch in service.py~~ — DONE
`run_consumer`: narrowed to `(RedisError, OSError)`. `_handle_with_retry`: logs exception type.

### ~~Discovery: broad exception in \_is\_idle~~ — DONE
Narrowed to `ResponseError`.

### ~~Delay-scheduler: catch-all removes messages~~ — DONE
Parse/schema errors → remove. Redis errors → retry next tick.

---

## Simplifications

### ~~Admin: if/elif dispatch chain~~ — DONE
Refactored to `_CMD_DISPATCH` and `_DLQ_DISPATCH` dicts.

---

## Readability

### ~~Common: type ignores without explanation~~ — DONE
Added inline explanations (redis-py 7 signature/return type) to all 4 ignores in common.

### ~~Common: undocumented +1000ms offset in riot\_api~~ — DONE
Added comment explaining thundering-herd jitter.

---

## Robustness

### ~~LCU: lockfile parsing lacks format validation~~ — DONE
Added part count and numeric port validation. 3 new tests.

### ~~Common: RawStore TOCTOU race on bundle writes~~ — DONE
Redis SET NX return value used as atomic coordinator; only the NX winner writes to disk.

### ~~Common: RawStore silent disk write failure~~ — DONE
On disk write failure, Redis key is deleted so next attempt retries both.

### ~~Common: silent rate-limit header parse failure~~ — DONE
Added warnings on parse failure and missing windows, with header value in extras.

---

## Testing / CI

### ~~LCU unit tests not in CI matrix~~ — DONE
Added `lol-pipeline-lcu` to `.github/workflows/ci.yml` test matrix.

### ~~Pre-existing lint issues in LCU tests~~ — DONE
Removed unused `json`/`Path` imports. Renamed `MockClient` → `mock_cls` (N803). Added S105 to test per-file-ignores.

---

## Comprehensive Unit Testing Plan

Current state: **383 unit + 44 contract tests**. Gap analysis below organized by priority tier.

### TIER 1 — Critical gaps (untested service logic, zero-coverage services)

#### ~~UI: zero unit tests~~ — DONE
46 tests covering: `_load_lcu_data`, `_lcu_stats_section`, `_match_history_section`, `_page`, `_stats_form`, `_stats_table`, `_aggregate_by_mode`, `_match_history_html`, `_tail_file`, `_parse_log_line`, `_render_log_lines`, `_merged_log_lines`. Route handlers remain untested (need app state mocking).

#### ~~Common: `run_consumer()` main loop~~ — DONE
6 tests: halt exits, message processing + ack, consume error retry with sleep mock.

#### ~~Common: `wait_for_token()` polling~~ — DONE
3 tests: immediate acquire (no sleep), retry until acquired, 50ms poll interval.

#### ~~Recovery: `_consume_dlq()` internal loop~~ — DONE
Covered implicitly via 15 recovery tests that use `_setup_dlq_msg` → `xreadgroup`.

#### ~~All services: `main()` / `__main__.py` entry points~~ — DONE
29 tests added across all services:
- **Seed** (5): missing args, invalid riot ID, valid args, default region na1, custom region
- **Admin** (5): stats/system-resume/dlq list/dlq replay/reseed dispatch
- **LCU** (5): data-dir from env, fallback, poll-interval from env, default zero, CLI override
- **Consumer services** (2 each × 5 = 10): crawler, fetcher, parser, analyzer, recovery — bootstrap + KeyboardInterrupt cleanup
- **Polling services** (2 each × 2 = 4): delay-scheduler, discovery — loop start + KeyboardInterrupt cleanup
- **UI** (1): uvicorn.run called with correct host/port

---

### ~~TIER 2 — Error paths (exception handling, failure modes)~~ — DONE
14 tests added across 5 services:
- **Streams** (5): publish XADD failure, consume XREADGROUP failure, consume XAUTOCLAIM failure, ack non-existent message, nack_to_dlq XADD failure
- **RiotClient** (3): malformed JSON response, empty response body, missing puuid in response (timeout/connection reset already covered)
- **Fetcher** (2): raw_store.set failure prevents publish, publish failure + idempotent redelivery
- **Parser** (3): non-JSON raw blob → DLQ, missing puuid → skip participant (code fix), missing stats → defaults
- **Delay-scheduler** (1): XADD failure preserves sorted set member (fixed infinite retry bug)

Code fixes:
- Parser: wrap `_write_participant` in try/except to skip participants with missing puuid
- Delay-scheduler: break pagination loop when no progress made (prevents infinite retry on XADD failures)

---

### TIER 3 — Edge cases and boundary conditions

#### LCU: collect\_once pagination
`lol-pipeline-lcu/src/lol_lcu/main.py`:
- `test_collect_once__exactly_page_size_results__fetches_next_page` — 20 games → requests page 2
- `test_collect_once__less_than_page_size__stops` — 15 games → no page 2
- `test_collect_once__empty_first_page__returns_zero`
- `test_collect_once__all_games_known__stops_early` — full page but all deduplicated
- `test_collect_once__player_not_in_participants__skips_game` — _extract_player_stats returns None
- `test_collect_once__file_write_failure__raises` — disk full / permission denied

#### LCU: `_extract_player_stats` direct tests
`lol-pipeline-lcu/src/lol_lcu/main.py`:
- `test_extract_player_stats__happy_path__returns_stats`
- `test_extract_player_stats__player_not_found__returns_none`
- `test_extract_player_stats__empty_participants__returns_none`
- `test_extract_player_stats__missing_stats_dict__uses_defaults` — participant has no "stats" key
- `test_extract_player_stats__partial_stats__fills_defaults` — some stat keys missing

#### LCU: `_build_participants` direct tests
`lol-pipeline-lcu/src/lol_lcu/main.py`:
- `test_build_participants__happy_path__returns_list`
- `test_build_participants__empty_participants__returns_empty`
- `test_build_participants__missing_fields__uses_defaults`

#### LCU: `_show_summary`
`lol-pipeline-lcu/src/lol_lcu/main.py`:
- `test_show_summary__no_directory__logs_info`
- `test_show_summary__empty_directory__no_output`
- `test_show_summary__multiple_jsonl_files__logs_each`

#### Crawler: pagination edge cases
`lol-pipeline-crawler/src/lol_crawler/main.py`:
- `test_crawl__rate_limit_without_retry_after__uses_default_backoff`
- `test_crawl__empty_puuid_in_payload__logs_error`

#### Seed: edge cases
`lol-pipeline-seed/src/lol_seed/main.py`:
- `test_seed__unknown_region__uses_americas_default`
- `test_seed__riot_api_unknown_error__propagates`
- `test_seed__publish_failure__does_not_update_cooldown`

#### Analyzer: edge cases
`lol-pipeline-analyzer/src/lol_analyzer/main.py`:
- `test_analyze__lock_acquisition_redis_error__propagates`
- `test_analyze__empty_match_history__updates_cursor_only`
- `test_analyze__very_large_cursor__handles_float_precision`
- `test_analyze__lock_expires_during_iteration__logs_warning`

#### Recovery: edge cases
`lol-pipeline-recovery/src/lol_recovery/main.py`:
- `test_backoff_ms__attempts_beyond_array__clamps_to_max`
- `test_process__retry_after_zero__uses_backoff_instead`
- `test_process__retry_after_negative__uses_backoff_instead`
- `test_archive__xadd_failure__logs_error`

#### Discovery: edge cases
`lol-pipeline-discovery/src/lol_discovery/main.py`:
- `test_promote__auth_error__halts_system`
- `test_promote__empty_batch__no_api_calls`
- `test_promote__player_seeded_between_check_and_publish__skips`
- `test_is_idle__redis_error__returns_true` (already behavior, but verify)

#### Common: rate limiter boundaries
`lol-pipeline-common/src/lol_pipeline/rate_limiter.py`:
- `test_acquire_token__exactly_at_limit__returns_false` — 20th request in window
- `test_acquire_token__just_after_window_expires__returns_true`
- `test_acquire_token__lua_script_error__propagates`

#### Common: RawStore bundle search edge cases
`lol-pipeline-common/src/lol_pipeline/raw_store.py`:
- `test_search_bundle__empty_jsonl_file__returns_none`
- `test_search_bundle__malformed_json_line__skips`
- `test_search_compressed_bundle__corrupt_zst__returns_none`

#### Common: models boundary conditions
`lol-pipeline-common/src/lol_pipeline/models.py`:
- `test_envelope__max_attempts_zero__immediate_exhaustion`
- `test_envelope__negative_attempts__handled`
- `test_dlq_envelope__empty_failure_reason__accepted`
- `test_dlq_envelope__missing_optional_fields__uses_defaults`

#### Common: log.py file handler
`lol-pipeline-common/src/lol_pipeline/log.py`:
- `test_get_logger__with_log_dir__creates_file_handler`
- `test_get_logger__log_dir_not_writable__falls_back_to_stderr`
- `test_get_logger__respects_log_level_env`

---

### TIER 4 — Structural / integration-adjacent

#### Admin: full CLI subcommand tests
`lol-pipeline-admin/src/lol_admin/main.py`:
- `test_dispatch__unknown_command__prints_usage`
- `test_dispatch__stats__calls_cmd_stats`
- `test_dispatch__dlq_list__calls_cmd_dlq_list`
- `test_dispatch__dlq_replay_all__calls_cmd_dlq_replay`
- `test_dispatch__system_resume__calls_cmd_system_resume`
- `test_dispatch__reseed__calls_cmd_reseed`

#### Streams: `_ensure_group()` isolation
`lol-pipeline-common/src/lol_pipeline/streams.py`:
- `test_ensure_group__group_exists__suppresses_busygroup`
- `test_ensure_group__stream_not_exists__creates_both`
- `test_ensure_group__unexpected_error__propagates`

#### Service: handler failure tracking
`lol-pipeline-common/src/lol_pipeline/service.py`:
- `test_handle_with_retry__first_failure__increments_count`
- `test_handle_with_retry__max_retries_reached__nacks_permanently`
- `test_handle_with_retry__success_after_failure__resets_count`

---

### Summary

| Tier | Description | Est. tests |
|------|-------------|------------|
| 1 | Critical gaps (UI zero-coverage, consumer loops, entry points) | ~40 |
| 2 | Error paths (Redis failures, network errors, corrupt data) | ~25 |
| 3 | Edge cases (boundaries, partial data, race conditions) | ~50 |
| 4 | Structural (CLI dispatch, group creation, failure tracking) | ~15 |
| **Total** | | **~130** |

Target: **192 → ~320 unit tests** (current → with all tiers complete).


## ~~Bugs~~ — FIXED
~~LCU 403 raising LcuNotRunningError instead of LcuAuthError.~~ Fixed: `_get()` now checks `resp.status_code in (401, 403)` before the general HTTPError handler. `_collect_with_auth_retry` retries up to 3x with 2s delay. 5 tests cover this.

## ~~Startup tests~~ — DONE
Covered by Tier 1 entry point tests: 29 tests across all services verify Config loading, Redis initialization, consumer/loop bootstrap, and graceful KeyboardInterrupt cleanup. No new failures found.

## ~~Update Todo with code improvements~~ — DONE
Reviewed all service source code. Findings added below.

---

## ~~Code Improvements (from review)~~ — DONE
7 fixes implemented: LCU narrow exception catch + port range validation, Discovery empty PUUID validation, UI corrupt JSON logging + magic number constant + bounded memory in merged logs, Analyzer transaction=True. 3 new tests added (port out of range, port zero, empty PUUID).

## ~~GitHub CI is failing, find out why~~ — FIXED
`ruff format` was failing on 13 files across all services. Applied formatting, fixed lint issues (SIM117, I001, E501, PLR0913 noqa, unused imports, N803). Added LCU to test matrix. Pushed to `fix/ci-lint-tests-336` branch, all 21 CI jobs passed (including new LCU job), merged to main via PR #1.

## ~~Next Phase~~ — DONE
Created `docs/phases/07-next-phase.md` with 4 priority areas: data collection priority (weighted queue), code quality improvements (7 fixes), test coverage expansion (~90 tests), LCU troubleshooting. Updated phase index. Findings already broken down in TODO.md above.

## ~~Update README.md~~ — DONE
Added Discovery service to pipeline diagram and table. Added Players and Logs pages to Web UI section. Added `just up`, `just scale`, `just lcu-watch`, `just test`, `just integration`, `just test-all`, `just lint`, `just typecheck`, `just check`, `just consolidate` commands. Added test counts (330 unit + 44 contract). Added Data Management section.

## Data collection priority
Ensure that there is a weighted queue for fetching data. For example manually requested seeded data should be higher priority than automatically discovered players.
Example: I ask for information about Pwnerer#1337 -> Immediately get me this information ASAP. If I ask for second page of data and it's not there yet, ensure it's set to higher priority vs auto discovering other players.
Players that are manually seeded should have their entire history scraped before contining with auto-discovered ones.
Update all relevant documentation to reflect this afterwards.
Make a comprehensive implementation plan including testing, and implement using red/green TDD. Store the plan in Claude.md under the `Pending Work` and begin implementing, updating the status there as you go.
Once you are completed, remove from pending.

## ~~Readibility~~ — DONE
Reviewed all raw index access patterns. Fixed: seed `hmget` fields to named variables, streams `xautoclaim` result to `claimed_entries`. Other uses (lockfile `parts[2]`/`parts[3]`, admin `split("_")[0]`) already have clear context.