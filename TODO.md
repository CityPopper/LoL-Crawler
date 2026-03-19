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

Current state: **336 unit + 65 contract tests**. Gap analysis below organized by priority tier.

### TIER 1 — Critical gaps (untested service logic, zero-coverage services)

#### ~~UI: zero unit tests~~ — DONE
46 tests covering: `_load_lcu_data`, `_lcu_stats_section`, `_match_history_section`, `_page`, `_stats_form`, `_stats_table`, `_aggregate_by_mode`, `_match_history_html`, `_tail_file`, `_parse_log_line`, `_render_log_lines`, `_merged_log_lines`. Route handlers remain untested (need app state mocking).

#### ~~Common: `run_consumer()` main loop~~ — DONE
6 tests: halt exits, message processing + ack, consume error retry with sleep mock.

#### ~~Common: `wait_for_token()` polling~~ — DONE
3 tests: immediate acquire (no sleep), retry until acquired, 50ms poll interval.

#### ~~Recovery: `_consume_dlq()` internal loop~~ — DONE
Covered implicitly via 15 recovery tests that use `_setup_dlq_msg` → `xreadgroup`.

#### All services: `main()` / `__main__.py` entry points
Every service has an untested entry point. For each, test:
- Config loading from environment
- Redis connection initialization
- Consumer/loop bootstrap
- Graceful exit on KeyboardInterrupt

Specific per-service:
- **Seed** `__main__.py`: argparse routing (`seed <riot_id> <region>`)
- **Admin** `__main__.py`: subcommand dispatch (`stats`, `dlq list`, `dlq replay`, `system-resume`, `reseed`)
- **LCU** `__main__.py`: `--data-dir` default from env, `--poll-interval` type coercion

---

### TIER 2 — Error paths (exception handling, failure modes)

#### Common: Redis operation failures across all services
Every service assumes Redis calls succeed. Add error-path tests:
- **Streams** `publish()` — XADD failure (connection lost, stream deleted)
- **Streams** `consume()` — XREADGROUP failure, XAUTOCLAIM failure
- **Streams** `ack()` — XACK on non-existent message ID
- **Streams** `nack_to_dlq()` — XADD to DLQ fails, original message left in PEL

#### Common: RiotClient network edge cases
`lol-pipeline-common/src/lol_pipeline/riot_api.py` — Beyond existing 403/429/500 tests:
- `test_get__connection_timeout__raises_server_error` — httpx.TimeoutException
- `test_get__connection_reset__raises_server_error` — httpx.RemoteProtocolError
- `test_get__malformed_json_response__raises` — valid HTTP 200 but non-JSON body
- `test_get__empty_response_body__raises` — HTTP 200 with empty body
- `test_get_account__missing_puuid_in_response` — API returns 200 but schema changed

#### ~~LCU: `_get()` edge cases~~ — DONE
4 tests in TestLcuClientGetEdgeCases: JSON decode error, timeout, HTTP 500. Plus 7 in TestLcuClientApi.

#### ~~LCU: lockfile format validation~~ — DONE
3 tests in TestLockfileValidation + 6 in TestLockfileParsing covering all edge cases.

#### Fetcher: partial failure paths
`lol-pipeline-fetcher/src/lol_fetcher/main.py`:
- `test_fetch__raw_store_set_fails__does_not_publish` — store fails, no downstream publish
- `test_fetch__publish_fails_after_store__message_redeliverable` — idempotent on re-delivery

#### Parser: malformed data
`lol-pipeline-parser/src/lol_parser/main.py`:
- `test_parse__raw_blob_not_json__nacks_to_dlq` — corrupt raw store data
- `test_parse__participant_missing_puuid__skips_participant` — partial participant data
- `test_parse__participant_stats_none__uses_defaults` — stats dict is None vs missing

#### Delay-scheduler: XADD failure
`lol-pipeline-delay-scheduler/src/lol_delay_scheduler/main.py`:
- `test_tick__xadd_fails__does_not_remove_from_sorted_set` — transient Redis error preserves message
- `test_tick__malformed_envelope__removes_and_logs` — corrupt JSON is cleaned up (already tested, verify)

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


## Bugs
Probably need some dependency ordering or something similar? Try to verify you can reproduce this error, write a test for it, then fix and verify.
```
wopr@WOPR3090:/mnt/c/Users/WOPR/Desktop/Scraper$ just lcu
WSL detected — running LCU collector via Docker (required to reach Windows localhost)...
Container scraper-lcu-run-e8fbd535cd7d Creating 
Container scraper-lcu-run-e8fbd535cd7d Created 
Traceback (most recent call last):
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/lcu_client.py", line 61, in _get
    resp.raise_for_status()
  File "/usr/local/lib/python3.12/site-packages/requests/models.py", line 1026, in raise_for_status
    raise HTTPError(http_error_msg, response=self)
requests.exceptions.HTTPError: 403 Client Error: Forbidden for url: https://host.docker.internal:59591/lol-summoner/v1/current-summoner

The above exception was the direct cause of the following exception:

Traceback (most recent call last):
  File "<frozen runpy>", line 198, in _run_module_as_main
  File "<frozen runpy>", line 88, in _run_code
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/__main__.py", line 29, in <module>
    main()
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/__main__.py", line 25, in main
    run(data_dir=args.data_dir, poll_interval_minutes=args.poll_interval)
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/main.py", line 134, in run
    collect_once(client, data_dir)
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/main.py", line 64, in collect_once
    summoner = client.current_summoner()
               ^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/lcu_client.py", line 70, in current_summoner
    return self._get("/lol-summoner/v1/current-summoner")  # type: ignore[no-any-return]
           ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
  File "/usr/local/lib/python3.12/site-packages/lol_lcu/lcu_client.py", line 64, in _get
    raise LcuNotRunningError(
lol_lcu.lcu_client.LcuNotRunningError: LCU API request failed (https://host.docker.internal:59591, LCU_HOST=host.docker.internal): 403 Client Error: Forbidden for url: https://host.docker.internal:59591/lol-summoner/v1/current-summoner

error: Recipe `lcu` failed with exit code 1
```

## Startup tests
Add tests to ensure that all services are able to start properly, and if they have dependencies they wait, etc etc.
If there are failures, add new tests that are needed in `TODO.md` to implement later.

## Update Todo with code improvements
review the codebase and add to the todo list: performance optimizations, code smells, anti-patterns, simplifications, improvements for readability, and improve robustness

## GitHub CI is failing, find out why
The GitHub CI is failing, look at the failures using the API key and find out why. Test some fixes and push them, ensure that the author email for the commits is set to reflect that changes are being made by Claude. Push to a new branch for new testing, then when things pass merge to main.

## Next Phase
Add new doc that shows what's needed in the next phase.
Afterwards, review with cold eyes.
Finally, break down the tasks and put them in relevant docs + in the TODO.md for implementation.

## Update README.md
Update it to ensure that everything is actually up to date.

## Data collection priority
Ensure that there is a weighted queue for fetching data. For example manually requested seeded data should be higher priority than automatically discovered players.
Example: I ask for information about Pwnerer#1337 -> Immediately get me this information ASAP. If I ask for second page of data and it's not there yet, ensure it's set to higher priority vs auto discovering other players.
Players that are manually seeded should have their entire history scraped before contining with auto-discovered ones.
Update all relevant documentation to reflect this afterwards.
Make a comprehensive implementation plan including testing, and implement using red/green TDD. Store the plan in Claude.md under the `Pending Work` and begin implementing, updating the status there as you go.
Once you are completed, remove from pending.

## Readibility
Review code for uses of `arg[]` etc and replace them if they are hard to read -- ie. assign them to proper variable names and perform validation. Add this as a coding standard.