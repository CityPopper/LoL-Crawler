# Phase 07 — IRONCLAD

**Codename:** Ironclad
**Purpose:** Harden the pipeline for production readiness — fix bugs, expand test coverage, add security hardening, then implement the weighted priority queue.

**Status:** In progress
**Revision:** 7 (Rev 6 + Round 2 two-phase doc+code audit by optimizer/formal-verifier/database specialists)

---

## Delivery Order (RICE-prioritized)

| Sprint | Priority | Area | Est. Tests | Est. Effort |
|--------|----------|------|------------|-------------|
| 1 | P0 | Doc/CLI fixes, placeholder cleanup | 0 | Small-Medium |
| 2 | P1 | Security fixes + code quality (26 fixes) | ~26 | Small-Medium |
| 3 | P2 | Tier 3 edge-case tests | ~0 (verify) | In Progress |
| 4 | P3 | Tier 4 structural tests + Docker hardening | ~6 | Medium |
| 5 | P4 | Weighted queue (priority system) | ~38 unit + ~8 contract | Large |

---

## Sprint 1 — P0: Documentation, CLI Fixes & Cleanup

Blockers found by QA agent — docs say commands exist but they don't.

| ID | Fix | Status |
|----|-----|--------|
| P0-1 | Implement `system-halt` admin command (or remove from docs) | |
| P0-2 | Implement `streams` admin command (or remove from docs — `just streams` exists) | |
| P0-3 | Document `replay-parse` and `replay-fetch` commands in admin README | |
| P0-4 | Add `dlq replay` to admin README | |
| P0-5 | Update README test count to actual (currently 401 unit + 44 contract) | |
| P0-6 | Update TODO.md to reflect Tier 2 + Tier 3 completion | |
| P0-7 | Delete 8 `test_placeholder.py` files (inflate count, zero value) | |
| P0-8 | Fix broken link to `07-architect-review.md` in `docs/phases/README.md` | |
| P0-9 | Fix `docs/architecture/03-streams.md` — remove "XAUTOCLAIM not implemented" claim (it IS implemented in `streams.py`) | |
| P0-10 | Rewrite `docs/architecture/05-rate-limiting.md` Lua script/wrapper sections to match actual code (dynamic limits via `ratelimit:limits:short/long`, returns 0/1 not tuple, `acquire_token` returns bool) | |
| P0-11 | Fix `docs/architecture/04-storage.md` RawStore disk format (JSONL bundles `{YYYY-MM}.jsonl`, not individual `{match_id}.json`) | |
| P0-12 | Fix `docs/architecture/04-storage.md` timestamp format (`seeded_at`/`last_crawled_at` are ISO 8601, not epoch ms) | |
| P0-13 | Fix consumer group names in `docs/operations/02-monitoring.md` and `docs/guides/02-troubleshooting.md` (`crawlers` not `crawler-group`, `fetchers` not `fetcher-group`, etc.) | |
| P0-14 | Fix `raw:match:` key prefix in `docs/guides/02-troubleshooting.md` (`raw:match:{match_id}` not `raw:{match_id}`) | |
| P0-15 | Add `logs/` to `.gitignore` | |
| P0-16 | Remove `--fix` from `just lint`; create separate `just fix` recipe | |
| P0-17 | Fix `01-overview.md` "Polyrepo" → "Monorepo" | |
| P0-18 | Add missing Redis keys to `04-storage.md` (`player:name:`, `ratelimit:limits:short`, `ratelimit:limits:long`) | |
| P0-19 | Document UI `/players` and `/logs` routes in `02-services.md`; add `LOG_DIR` to env var table | |
| P0-20 | Fix service count in `01-overview.md` (8 → 11) | |
| P0-21 | Correct Tier 3 status — tests are in working tree but need verification via test run. Mark as "In Progress" not "COMPLETE" until verified. | |
| P0-22 | Standardize `lol-pipeline-ui/pyproject.toml` — add dev deps, full ruff rules (C90, S, ANN, SIM, PLR), pytest config, remove mypy `ignore_errors=true` | |
| P0-23 | Add Python 3.12+ to README prerequisites (currently lists only Docker and just) | |
| P0-24 | Add `just test-svc <name>` recipe for single-service testing | |
| P0-25 | Add `just format` and `just coverage` recipes | |
| P0-26 | Fix P0-7 count: 9 placeholder files, not 8 (common also has one) | |
| P0-27 | Fix pre-commit hooks: scope to changed service only (currently runs ALL services on any file change, 30s+ per commit) | |
| P0-28 | Fix CI dep install: remove `\|\| true` fallback that hides broken pyproject.toml | |
| P0-29 | Fix deployment doc: `HDEL player:stats cursor` → `DEL player:stats:cursor:{puuid}` (line 493) | |
| P0-30 | Fix deployment + troubleshooting docs: `HGETALL player:name:` → `GET` (it's a String, not Hash) | |
| P0-31 | Fix `02-monitoring.md` and `04-storage.md` capacity planning: `raw:match:` blobs are 15-30 KB, not 100-200 KB. Current estimates inflated 5-10x. | |
| P0-32 | Fix `02-monitoring.md` line 115: `discovered:players` → `discover:players` | |
| P0-33 | Fix `02-troubleshooting.md` line 380: `SCARD "discovered:players"` → `ZCARD "discover:players"` (it's a Sorted Set) | |
| P0-34 | Fix `06-failure-resilience.md` DLQ lifecycle: add in-process retry phase (service.py `_handle_with_retry` retries up to 3 handler crashes before nack to DLQ) — diagram is missing this step | |
| P0-35 | Add `<meta name="viewport">` to `_page()` in Web UI (mobile completely broken without it) | |
| P0-36 | Add `stream:dlq:archive` and `system:halted` check to `just streams` recipe | |

---

## Sprint 2 — P1: Security Fixes + Code Quality

### Security Vulnerabilities (fix now)

| ID | Fix | File | Severity |
|----|-----|------|----------|
| SEC-1 | Validate PUUID format at model layer (`MessageEnvelope`/`DLQEnvelope` deserialization) AND UI endpoint | `models.py` + `lol_ui/main.py` | Medium |
| SEC-2 | URL-encode `game_name`/`tag_line` in Riot API URLs (`urllib.parse.quote()`) | `lol-pipeline-common/src/lol_pipeline/riot_api.py` | Medium |
| SEC-3 | Add `.dockerignore` to all 11 services | All service dirs | High |
| SEC-4 | Bind Redis to `127.0.0.1` in dev compose | `docker-compose.yml` | High |
| SEC-5 | Discovery must handle AuthError (403) → set `system:halted`, break loop | `lol-pipeline-discovery/src/lol_discovery/main.py` | High |
| SEC-6 | Redact LCU lockfile password in error messages | `lol-pipeline-lcu/src/lol_lcu/lcu_client.py` | Medium |

### Code Quality (remaining items)

| ID | Fix | File | Status |
|----|-----|------|--------|
| CQ-1 | UI: bounded `_merged_log_lines` (use `heapq.merge`) | `lol-pipeline-ui/src/lol_ui/main.py` | Pending |
| CQ-2 | Admin CLI: use `print()` for user-facing errors, not JSON logger | `lol-pipeline-admin/src/lol_admin/main.py` | Pending |
| CQ-3 | LCU: use `print()` for terminal output, not JSON logger | `lol-pipeline-lcu/src/lol_lcu/main.py` | Pending |
| CQ-4 | Fix match history JS error: `e` → `(e.message || e)` | `lol-pipeline-ui/src/lol_ui/main.py` | Pending |
| CQ-5 | Seed: move `hset(seeded_at)` after `publish()` (ordering bug) | `lol-pipeline-seed/src/lol_seed/main.py` | Pending |
| CQ-6 | Config: add range constraints (`ge=1`) to numeric fields | `lol-pipeline-common/src/lol_pipeline/config.py` | Pending |
| CQ-7 | Graceful shutdown: SIGTERM handler in `run_consumer()` | `lol-pipeline-common/src/lol_pipeline/service.py` | Pending |
| CQ-8 | Fix `dlq_attempts` schema drift: add to `envelope.json` (exists on dataclass, missing from schema) | `lol-pipeline-common/contracts/schemas/envelope.json` | Pending |
| CQ-9 | Recovery main loop: add `(RedisError, OSError)` retry with 1s sleep | `lol-pipeline-recovery/src/lol_recovery/main.py` | Pending |
| CQ-10 | Add `"run_consumer"` to DLQ schema `failed_by` enum (or pass service name) | `lol-pipeline-common/contracts/schemas/dlq_envelope.json` | Pending |
| CQ-11 | Add `"handler_crash"` to DLQ schema `failure_code` enum | `lol-pipeline-common/contracts/schemas/dlq_envelope.json` | Pending |
| CQ-12 | Expand CQ-5 scope: fix hset-before-publish ordering in Seed, UI auto-seed, AND Discovery | Multiple service `main.py` files | Pending |
| CQ-13 | Expand CQ-7 scope: graceful shutdown for all long-running services (Recovery, Discovery, Delay Scheduler have own `while True` loops, not just `run_consumer`) | Multiple service `main.py` files | Pending |
| CQ-14 | Add corrupt message deserialization handling in `consume()` and `_consume_dlq()` — catch `KeyError`/`JSONDecodeError` per message, log + ack corrupt entries | `lol-pipeline-common/src/lol_pipeline/streams.py` | Pending |
| CQ-15 | Parser: pipeline per-participant writes (HSET + SADD + ZADD = 3 calls x 10 participants = 30 sequential Redis calls; should be 1 pipeline round-trip) | `lol-pipeline-parser/src/lol_parser/main.py` | Pending |
| CQ-16 | Analyzer: pipeline HGETALL calls for new matches (N+1 pattern: 1 HGETALL per match; should batch via `r.pipeline()`) | `lol-pipeline-analyzer/src/lol_analyzer/main.py` | Pending |
| CQ-17 | UI `/stats/matches`: pipeline the 2x HGETALL per match (N+1 pattern: 40 Redis calls for 20 matches; should be 1 pipeline) | `lol-pipeline-ui/src/lol_ui/main.py` | Pending |
| CQ-18 | RawStore `_search_bundle_file`: stream lines instead of `read_text().splitlines()` (loads entire file into memory; monthly bundles can exceed 100 MB) | `lol-pipeline-common/src/lol_pipeline/raw_store.py` | Pending |
| CQ-19 | Discovery `_resolve_names`: 2 sequential HGET calls per player (game_name, tag_line) — use HMGET for 1 round-trip instead of 2 | `lol-pipeline-discovery/src/lol_discovery/main.py:62-63` | Pending |
| CQ-20 | Parser `_write_participant`: 3-5 sequential Redis calls (HSET + SADD + ZADD + optional HSETNX x2) per participant — pipeline all writes per participant into 1 round-trip | `lol-pipeline-parser/src/lol_parser/main.py:45-72` | Pending |
| CQ-21 | Seed `_within_cooldown`: safe but worth noting — HMGET is already used correctly. No change needed. | N/A | Skip |
| CQ-22 | `nack_to_dlq` does not propagate `dlq_attempts` from source envelope — Recovery increments `dlq_attempts`, but if a message is nacked multiple times by _different_ services (handler_crash), the counter resets to 0 each time because `nack_to_dlq` uses `envelope.attempts` (not `envelope.dlq_attempts`) for `attempts` and hardcodes nothing for `dlq_attempts`. Currently DLQEnvelope's `dlq_attempts` defaults to 0 which is correct for first-time DLQ entries. **Verified correct — no fix needed.** | N/A | Skip |
| CQ-23 | `nack_to_dlq` does not preserve `enqueued_at` from the original envelope — DLQEnvelope default factory creates a new timestamp. Original enqueue time is lost, making end-to-end latency tracking impossible. Pass `enqueued_at=envelope.enqueued_at` to DLQEnvelope constructor. | `lol-pipeline-common/src/lol_pipeline/streams.py:101-114` | Pending |

**Definition of Done:** All fixes merged (21 actionable CQ items + 6 SEC items = 27 fixes), ≥1 new test per fix, all existing tests still passing. All new doc fixes verified by running documented Redis commands.

---

## Sprint 3 — P2: Tier 3 Edge-Case Tests

**Status: IN PROGRESS** — All 50 Tier 3 tests are in the working tree but have not been verified via a full test run. PM agent found tests exist but need confirmation they pass.

**Action required:** Run full test suite to verify Tier 3 tests pass. Mark COMPLETE only after successful run.

---

## Sprint 4 — P3: Tier 4 Structural Tests + Docker Hardening

### Tier 4 Tests (~6 remaining)

| ID | Scope | Tests |
|----|-------|-------|
| T4-1 | Admin CLI dispatch isolation (verify overlap with existing 21 tests) | ~3 |
| T4-2 | Streams `_ensure_group` unexpected error propagation | 1 |
| T4-3 | Service handler failure tracking (verify overlap) | ~2 |

### Docker Hardening

| ID | Fix | Priority |
|----|-----|----------|
| DK-1 | Fix Discovery Dockerfile (broken for prod — no COPY, no pip install) | P0 |
| DK-2 | Fix LCU Dockerfile (single-stage, no HEALTHCHECK) | P0 |
| DK-3 | Add HEALTHCHECK to UI Dockerfile | P0 |
| DK-4 | Run all containers as non-root (`USER appuser`), add `STOPSIGNAL SIGTERM` | P1 |
| DK-5 | Add `security_opt: [no-new-privileges:true]` + `stop_grace_period: 30s` to compose defaults | P1 |
| DK-6 | Recreate `docker-compose.prod.yml` (was deleted) — must include: no volume mounts, baked images, Redis auth via `--requirepass`, `bind 127.0.0.1`, resource limits, log driver config | P1 |
| DK-7 | Add resource limits (`mem_limit`) to dev compose | P2 |
| DK-8 | CI: remove mypy `|| true`, make it a hard gate | P1 |
| DK-9 | Add `lol-pipeline-ui` to CI unit test matrix | P1 |
| DK-10 | Fix CI contract test matrix to match actual `tests/contract/` directories | P1 |
| DK-11 | Add Docker build job to CI (catches broken Dockerfiles like Discovery) | P1 |
| DK-12 | Fix `wheels/` dirs + placeholder GitHub URL in Dockerfiles (broken for non-local builds) | P0 |
| DK-13 | Add integration test CI job for existing IT-01 through IT-07 | P2 |

---

## Sprint 5 — P4: Weighted Queue (Priority System)

### Prerequisites (HARD BLOCKERS — must complete before any service changes)

1. **Schema update**: Add `priority` (optional, enum `["high","normal"]`, default `"normal"`) to both `envelope.json` and `dlq_envelope.json`. Also fix pre-existing `dlq_attempts` drift in `envelope.json`. These schemas have `"additionalProperties": false` — any service code adding `priority` before this update will break all 44 contract tests.
2. **Pact file update**: Add `priority` field to all 6 consumer pact files.
3. **CQ-5 must be done first**: Sprint 2 fixes seed ordering (`hset` after `publish`). Sprint 5 adds more lines to the same function — must build on the corrected ordering.

### Design (Architect recommendation — 7/7 consensus)

**Approach:** Keep single `stream:puuid`. Gate Discovery with a priority counter.

1. Add optional `priority: str = "normal"` field to `MessageEnvelope` and `DLQEnvelope` (values: `"high"`, `"normal"`)
2. Add `player:priority:{puuid}` Redis key (String, value `"high"`, TTL 24h) — set by Seed/UI at publish time
3. Add `system:priority_count` counter (INCR on seed, DECR via atomic Lua on completion)
4. Discovery checks `GET system:priority_count` — if >0, stays paused (don't promote)
5. Crawler: on zero-match crawl, atomic DEL+DECR via Lua
6. Analyzer: after stats computed, atomic DEL+DECR via Lua

**Atomic SET+INCR Lua script** (prevents counter drift on crash between SET and INCR):
```lua
-- KEYS[1] = player:priority:{puuid}, KEYS[2] = system:priority_count
-- ARGV[1] = "high", ARGV[2] = TTL in seconds (86400)
redis.call("set", KEYS[1], ARGV[1], "EX", ARGV[2])
redis.call("incr", KEYS[2])
return redis.call("get", KEYS[2])
```
This ensures: the priority key and counter are always in sync. If the Lua script fails, neither is set. No crash window between SET and INCR.

**Atomic DEL+DECR Lua script** (prevents counter underflow/double-DECR):
```lua
-- Only DECR if the priority key existed (DEL returns 1)
if redis.call("del", KEYS[1]) == 1 then
    redis.call("decr", KEYS[2])
end
return redis.call("get", KEYS[2])
```
This ensures: if TTL already expired the key, DECR does not fire. If Crawler DELs on zero-match path, Analyzer's DEL returns 0 and skips DECR. No double-decrement possible.

**Counter semantics:**
- **Crawler only DECRs on the zero-match path** (no matches found → player pipeline ends at Crawler)
- **Analyzer only DECRs on the normal path** (matches found → player pipeline ends at Analyzer)
- These are mutually exclusive — a player either has matches or doesn't, never both paths

**Why this approach:**
- No new streams, no new consumer patterns, no stream reordering
- Discovery is already gated by idle-check — this extends the gate with O(1) counter check
- `player:priority:{puuid}` TTL (24h) is a self-healing safety net
- Fully backward-compatible (priority field defaults to "normal")

### Contract Changes

**Schema files:**
- `lol-pipeline-common/contracts/schemas/envelope.json`: Add optional `priority` property + fix `dlq_attempts` drift
- `lol-pipeline-common/contracts/schemas/dlq_envelope.json`: Add optional `priority` property

**Model files:**
- `lol-pipeline-common/src/lol_pipeline/models.py`: Add `priority: str = "normal"` to both `MessageEnvelope` and `DLQEnvelope`, update `to_redis_fields()` and `from_redis_fields()`

**Stream files:**
- `lol-pipeline-common/src/lol_pipeline/streams.py`: `nack_to_dlq()` must propagate `priority` from source envelope to DLQ envelope

**Pact files (all 6):**
- `lol-pipeline-crawler/pacts/crawler-seed.json`
- `lol-pipeline-fetcher/pacts/fetcher-crawler.json`
- `lol-pipeline-parser/pacts/parser-fetcher.json`
- `lol-pipeline-analyzer/pacts/analyzer-parser.json`
- `lol-pipeline-recovery/pacts/recovery-common.json`
- `lol-pipeline-delay-scheduler/pacts/delay-scheduler-common.json`

### New Redis Keys

```
player:priority:{puuid}    String   "high"   TTL 86400s (24h)
system:priority_count       String   int      No TTL (maintained by atomic Lua INCR/DECR)
```

**TTL is hardcoded** at 86400s (not configurable). If configurability is needed later, add `PRIORITY_TTL_SECONDS` to `config.py`.

### Service Changes (complete file list)

| Service | File | Change |
|---------|------|--------|
| Common | `models.py` | Add `priority` field to `MessageEnvelope` + `DLQEnvelope`, update serialization |
| Common | `streams.py` | `nack_to_dlq()` propagates `priority` to DLQ envelope |
| Common | `contracts/schemas/envelope.json` | Add `priority` property, fix `dlq_attempts` |
| Common | `contracts/schemas/dlq_envelope.json` | Add `priority` property |
| Seed | `lol_seed/main.py` | Set `priority="high"`, atomic Lua SET+INCR for `player:priority:{puuid}` with TTL + `system:priority_count`. Order: (1) publish, (2) atomic Lua SET+INCR, (3) hset seeded_at |
| UI | `lol_ui/main.py` | Same as Seed on auto-seed path (atomic Lua SET+INCR). Display `system:priority_count` on `/streams`. Show priority indicator on `/stats` |
| Discovery | `lol_discovery/main.py` | Add `GET system:priority_count` check to `_is_idle()` — if >0, return False |
| Crawler | `lol_crawler/main.py` | On zero-match crawl: atomic Lua DEL+DECR |
| Analyzer | `lol_analyzer/main.py` | After stats: atomic Lua DEL+DECR |
| Admin | `lol_admin/main.py` | New `recalc-priority` command: SCAN `player:priority:*`, count keys, SET `system:priority_count` to that count |
| Recovery | No code change | Priority propagation handled by `nack_to_dlq()` in common streams.py |
| Delay Scheduler | No code change | Dispatches by `env.source_stream` (unchanged) |

### Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| SCAN performance for priority check | Use counter key (`system:priority_count`) — O(1) |
| Priority key leak (player has 0 matches) | Crawler atomic DEL+DECR on zero-match + 24h TTL safety net |
| Counter drift (crash between SET + INCR) | **FIXED**: Use atomic Lua SET+INCR script (both operations in single EVAL). TTL self-heals (24h). `admin recalc-priority` for manual recovery. |
| Counter goes negative | Impossible — Lua script only DECRs if DEL returns 1 (key existed) |
| Double-DECR (Crawler + Analyzer both fire) | Impossible — Lua DEL is atomic; second DEL returns 0, skips DECR |
| Discovery starvation (continuous manual seeding) | Desired behavior per requirements; document clearly |
| Backward compatibility during rolling deploy | `priority` defaults to "normal" if absent via `fields.get("priority", "normal")` |
| DLQ round-trip loses priority | `nack_to_dlq()` in streams.py propagates `priority` to DLQ envelope |

### Acceptance Criteria

| ID | Criteria |
|----|---------|
| WQ-1 | Manual seed (`just seed`) publishes with `priority: "high"` — verified by unit test |
| WQ-2 | UI auto-seed publishes with `priority: "high"` — verified by unit test |
| WQ-3 | Discovery publishes with `priority: "normal"` — verified by unit test |
| WQ-4 | Discovery pauses while `system:priority_count > 0` — checked on every poll iteration, verified by unit test |
| WQ-5 | Analyzer atomic DEL+DECR after processing — verified by unit test (DEL returns 1 → DECR fires) |
| WQ-6 | Zero-match crawl atomic DEL+DECR — verified by unit test |
| WQ-7 | Old messages without `priority` field default to "normal" — verified by contract test |
| WQ-8 | Priority key TTL is 24h — verified by unit test checking TTL via fakeredis |
| WQ-9 | All existing tests still pass after changes |
| WQ-10 | Documentation updated (streams, services, storage docs, README) |
| WQ-11 | Web UI displays `system:priority_count` on `/streams` page + priority indicator on `/stats` when player has active priority key |
| WQ-12 | `admin recalc-priority` command exists: SCANs `player:priority:*`, SETs `system:priority_count` to correct value — verified by unit test |
| WQ-13 | DLQ round-trip preserves priority: Seed publishes high → nack_to_dlq → Recovery requeues → priority still "high" — verified by unit test |
| WQ-14 | Counter cannot go negative: test DEL+DECR Lua when key already expired — DECR does not fire |
| WQ-15 | Discovery `_is_idle()` returns False when counter >0 even if stream has no pending/lag — verified by unit test |

---

## Phase 7 Definition of Done

Phase 7 is **complete** when ALL of the following are true:

1. All P0 doc/CLI fixes merged (P0-1 through P0-34), placeholder tests deleted
2. All P1 security + code quality fixes merged (27 fixes: SEC-1 through SEC-6, CQ-1 through CQ-20, CQ-23; ≥27 new tests)
3. Tier 3 tests verified via full test run (50 tests)
4. Tier 4 structural tests complete (~6 tests)
5. Docker hardening applied (non-root, STOPSIGNAL, .dockerignore, prod compose, HEALTHCHECKs)
6. CI unit test matrix covers all 12 services (including UI)
7. Weighted queue implemented (WQ-1 through WQ-15)
8. Total unit test count ≥ 467 (baseline: 393 after placeholder deletion + ~27 P1 + ~6 T4 + ~40 WQ)
9. All 52+ contract tests passing (44 existing + ~8 new priority pacts)
10. CI green — all jobs pass, mypy is a hard gate (no `|| true`)
11. Coverage maintained: common ≥90%, services ≥80%
12. All documentation updated (README, ARCHITECTURE.md, phase docs, agent definitions, admin README)
13. All new doc fixes verified by running documented Redis commands
14. TODO.md cleaned up — Phase 7 items marked DONE or deferred to Phase 8

### Coverage Baseline (for regression tracking)

| Service | Current Coverage |
|---------|-----------------|
| lol-pipeline-common | ≥90% |
| All other services | ≥80% |

*(Exact per-service numbers to be measured at Sprint 1 start and recorded here)*

---

## Explicitly Deferred to Phase 8

| Item | Reason |
|------|--------|
| LCU troubleshooting | Requires running League client — unknown scope |
| Integration tests (IT-08 through IT-11 for priority) | Phase 8 after unit tests validate the feature |
| Prometheus + Grafana | Scoped tightly for Phase 8; Redis hash metrics sufficient for now |
| S3 backend for RawStore | Out of MVP scope |
| Multi-region deployment | Out of scope for local/bare-metal |
| Authentication / API gateway | Not needed for solo deployment |
| Redis ACLs (per-service users) | Phase 8 production hardening |
| TLS reverse proxy for Web UI | Phase 8 when deploying to bare metal |
| Image scanning (Trivy/Grype) | Phase 8 production hardening |
| Configurable priority TTL | Only if 24h hardcoded value proves insufficient |
| Player index set (`players:all`) to replace SCAN | Optimizer/DB: O(N) SCAN in UI `/players` is fine at <10K players; add index when scaling |
| Raw blob TTL / eviction to disk-only | DB: `raw:match:*` dominates memory (~60 GB at 100K players); needs eviction strategy at scale |
| Stream MAXLEN trimming policy | DB: Streams grow unbounded; add `MAXLEN ~10000` to XADD calls when stream depths become a concern |
| `discover:players` bounded growth | DB: Sorted set grows unbounded when pipeline is busy; add ZREMRANGEBYRANK cap or periodic cleanup |
| Delay Scheduler atomic XADD+ZREM | Formal-verifier: non-atomic dispatch can cause duplicate delivery on crash; wrap in Lua or accept at-least-once |
| Discovery batch pipelining | Optimizer: HEXISTS + HSET + XADD + ZREM per member are sequential; pipeline when batch_size > 10 |
| RawStore: sorted bundles + binary search | Optimizer: JSONL bundles are scanned linearly O(N); at 100K+ matches per month, consider sorted format with binary search or an in-memory index. Current linear scan is acceptable at <50K matches/month. |
| Analyzer: batch HGETALL for new matches | Optimizer: CQ-16 already covers this — N+1 HGETALL per match. At 20 matches per player this is 20 round-trips. Pipeline to 1 round-trip. **Already in Sprint 2 as CQ-16.** |
| Recovery: add RedisError retry to main loop | Formal-verifier: Recovery `main()` has bare `while True` with no `(RedisError, OSError)` catch — a transient Redis error in `_consume_dlq` crashes the service. **Already in Sprint 2 as CQ-9.** |
| `nack_to_dlq` priority propagation | Formal-verifier: Sprint 5 streams.py change must propagate `priority` field from `MessageEnvelope` to `DLQEnvelope`. **Already tracked in Sprint 5 service changes table.** |
| Delay Scheduler: ZRANGEBYSCORE returns stale members | Formal-verifier: Between ZRANGEBYSCORE and ZREM, another scheduler instance could dispatch the same member. Single-instance deployment makes this safe; document the assumption. At scale, needs Lua or ZPOPMIN. |
| Connection pool sizing | Database: `redis.asyncio.from_url` default pool is 10 connections. With 8+ concurrent services sharing one Redis, this is fine (each service is single-connection in practice). No change needed. |
| Redis `maxmemory` policy not configured | Database: no `maxmemory` or eviction policy set in `docker-compose.yml`. If Redis exceeds host memory, OOM killer intervenes. Add `maxmemory` + `noeviction` policy to dev compose and document in prod compose. Phase 8 production hardening. |
| `delayed:messages` member size | Database: members are full JSON envelopes (~500B). At 1000 concurrent delayed messages this is ~500KB — negligible. No change needed. |

---

## Summary

| Metric | Current | End of Phase 7 |
|--------|---------|---------------|
| Unit tests | 393 (401 minus 8 placeholders — needs verification) | ≥466 |
| Contract tests | 44 | ~52 |
| Security vulns fixed | 0 | 6 (SEC-1 through SEC-6) + 1 formal correctness fix (atomic SET+INCR) |
| Docker/CI items | 0 | 13 (DK-1 through DK-13) |
| Code quality fixes | 0 | 21 (CQ-1 through CQ-20 + CQ-23, incl. schema drift fix + pipeline batching; CQ-21/22 verified no-ops) |
| Doc/CLI/DevEx fixes | 0 | 34 (P0-1 through P0-34) |
| New Redis keys | 0 | 2 (player:priority, system:priority_count) |
| New envelope fields | 0 | 1 (priority) |
| New admin commands | 0 | 1 (recalc-priority) |
| New Lua scripts | 0 | 2 (atomic SET+INCR, atomic DEL+DECR) |
