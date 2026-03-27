# TODO — Open Work Items

---

## REV-1 — BUG: Overwrite guard missing DEL of champion/role sets (CRITICAL)

**Severity:** Critical — data correctness
**Found by:** Formal verifier

The overwrite guard in `handle_player_stats` (`lol-pipeline-player-stats/src/lol_player_stats/main.py`) DELs `player:stats:{puuid}` and `player:stats:cursor:{puuid}` when `source=opgg_prefetch` is detected, but does NOT DEL `player:champions:{puuid}` or `player:roles:{puuid}`.

Since `_PROCESS_MATCH_LUA` uses `ZINCRBY` for champions and roles, the pipeline accumulates on top of the op.gg-prefetch counts — causing double-counting.

**Execution trace showing the bug:**
```
T=0: compute_opgg_fast_stats writes player:champions:P = {Jinx: 5}
T=1: handle_player_stats fires, detects source=opgg_prefetch
T=2: DELs player:stats:P and player:stats:cursor:P
     (player:champions:P still = {Jinx: 5})
T=3: cursor = 0.0, reprocesses all 5 matches via ZINCRBY
T=4: player:champions:P = {Jinx: 10}  ← DOUBLED
```

**Fix:** Add two DEL calls to the overwrite guard block:
```python
if source == "opgg_prefetch":
    await r.delete(f"player:stats:{puuid}")
    await r.delete(f"player:stats:cursor:{puuid}")
    await r.delete(f"player:champions:{puuid}")   # ← add
    await r.delete(f"player:roles:{puuid}")        # ← add
```

**TDD checklist:**
- [ ] **Red:** Pre-populate `player:champions:P = {Jinx: 5}` with `source=opgg_prefetch`; process 5 matches all on Jinx → assert `ZSCORE player:champions:P Jinx == 5` (not 10)
- [ ] **Green:** Add the two DEL calls
- [ ] **Refactor:** Combine all 4 DELs into a single `r.delete(key1, key2, key3, key4)` call (saves 3 RTTs)

---

## REV-2 — BUG: Deferred messages have no cap — can loop indefinitely (HIGH)

**Severity:** High — operational risk
**Found by:** Architect

`defer_message` re-queues a message with a 30s delay but never increments `attempts` or tracks deferral count. If `has_priority_players()` stays True for an extended period (e.g., many UI searches in a row), messages cycle through `defer → re-inject → defer` indefinitely. When `delayed:envelope:{id}` TTL eventually expires (86400s default), the ZSET member becomes an orphan that the Delay Scheduler cannot dispatch — the message silently disappears.

**Fix:** Track deferrals in the envelope payload or add a `defer_count` field to MessageEnvelope. When `defer_count` exceeds a threshold (e.g., 100 deferrals = 50 minutes), route to DLQ with `failure_code=deferred_too_long`. Alternatively, add a per-message deferral counter in the `delayed:envelope:{id}` hash and check it before re-deferring.

**TDD checklist:**
- [ ] **Red:** Write test — message deferred 100 times accumulates a deferral count; on the 101st call routes to DLQ instead of re-deferring
- [ ] **Green:** Implement deferral cap (threshold configurable via env var, default 100)
- [ ] **Refactor:** Confirm normal messages (deferred 1-5 times) are unaffected

---

## REV-3 — OPTIMIZATION: Pass `envelope_ttl` to `defer_message` from fetcher

**Severity:** Medium — unnecessary CPU overhead
**Found by:** Optimizer

Every call to `defer_message` without an explicit `envelope_ttl` constructs a new `Config()` object (pydantic-settings validation, reads env vars). At 20 deferrals/second during a priority burst, this creates 20 Config objects/second.

**Fix:** In `lol-pipeline-fetcher/src/lol_fetcher/main.py`, pass `envelope_ttl=cfg.delay_envelope_ttl_seconds` to `defer_message`:
```python
await defer_message(r, msg_id, envelope, _IN_STREAM, _GROUP,
                    envelope_ttl=cfg.delay_envelope_ttl_seconds)
```
`cfg` is already available in `_fetch_match`.

**TDD checklist:**
- [ ] **Red:** Test that `defer_message` is called with explicit `envelope_ttl` matching `cfg.delay_envelope_ttl_seconds`
- [ ] **Green:** Pass `envelope_ttl` through from fetcher call site
- [ ] **Refactor:** Verify no Config() construction happens in `defer_message` when `envelope_ttl` is provided

---

## REV-4 — OPTIMIZATION: Combine DELs in player-stats overwrite guard

**Severity:** Low — minor RTT reduction
**Found by:** Optimizer

Lines 181-184 in `lol-pipeline-player-stats/src/lol_player_stats/main.py` issue 4 sequential `await r.delete(...)` calls. These can be combined into one:
```python
await r.delete(
    f"player:stats:{puuid}",
    f"player:stats:cursor:{puuid}",
    f"player:champions:{puuid}",
    f"player:roles:{puuid}",
)
```
Saves 3 RTTs per invocation (applies after REV-1 adds the champion/role DELs).

---

## REV-5 — OPTIMIZATION: SCARD fast-path in `has_priority_players`

**Severity:** Low — marginal Redis reduction
**Found by:** Optimizer

`has_priority_players()` does SMEMBERS + N EXISTS even when `priority:active` is empty (the most common case). A SCARD check first would short-circuit in 1 RTT:
```python
if await r.scard(PRIORITY_ACTIVE_SET) == 0:
    return False
# existing SMEMBERS + EXISTS logic...
```

---

## REV-6 — TEST GAP: Cache expiry boundary not tested (FP-2)

**Found by:** Tester

`_has_priority_cached` caches the result for 2s. No test verifies the cache is invalidated *after* 2s — a bug where the cache never expires would go undetected.

**Test to add** (`lol-pipeline-fetcher/tests/unit/test_priority_gating.py`):
- Mock `time.monotonic` to advance 3s past the cache write
- Assert `has_priority_players` is called again on the next invocation

---

## REV-7 — TEST GAP: `defer_message` args not verified in fetcher test (FP-2)

**Found by:** Tester

`test_low_priority__priority_active__defers` asserts `mock_defer.assert_called_once()` but does not verify the stream, group, or envelope arguments. A bug passing the wrong stream name would pass this test.

**Test to add:** Assert `mock_defer.call_args` includes `_IN_STREAM`, `_GROUP`, and the correct `msg_id`/`envelope`.

---

## REV-8 — TEST GAP: `compute_opgg_fast_stats` edge cases (FP-4.1)

**Found by:** Tester — 3 missing cases:

1. **Empty `raw_games`:** Pass `[]`, assert return=0 and no Redis keys written
2. **Participant not in game:** Mix of matched and non-matching games; count reflects only matched games
3. **`game_length_second=0`:** All games with zero duration → `avg_cs_per_min == "0.0000"`

---

## REV-9 — TEST GAP: Champion/role double-count not caught by existing tests (FP-4.3)

**Found by:** Tester + Formal verifier

Existing `TestOpggPrefetchOverwriteGuard` only checks `total_kills`. It does not test champion/role sorted sets, so REV-1 was not caught by tests.

**Test to add:** Pre-populate `player:champions:P = {Jinx: 5}` with `source=opgg_prefetch`; run `handle_player_stats` with 5 Jinx games → assert `ZSCORE player:champions:P Jinx == 5`.

---

## DB-1 — DATABASE: Flush anonymized seed data and restart clean

**Severity:** Critical — pipeline broken (4960 DLQ entries, 0 real players)

All 1240 seeded players use anonymized PUUIDs (`anon_XXXXXXXX`) and fake names (`Player_XXXXX / Anon`). The crawler fails with `http_5xx` for every one of them because these PUUIDs don't exist in the Riot API. The entire `stream:puuid` (4960 messages) is in the DLQ as a result.

**✅ DONE:** Database flushed and `dump.rdb` removed. Pipeline restarted clean.

Real match/participant/stats data was preserved via the pipeline — the anonymized player *seeds* are what needed removal.

**Follow-up:** Seed real players via `just admin track <RiotID> --region <region>` to populate the pipeline with valid data.

---

## DB-2 — DATABASE: champion-stats consumer stalled (1444 lag, 56min idle)

**Severity:** High — champion stats not being computed

`stream:analyze` `champion-stats-workers` group has 1444 messages of lag and the consumer has been idle for 56+ minutes. The `player-stats-workers` group is caught up (lag=0).

**Investigate:** Check `podman logs lol-crawler_champion-stats_1` for errors. Possible causes: crash loop, OOM, config issue.

---

## DB-3 — DATABASE: Update `download_seed.py` docstring

**Severity:** Low — misleading documentation

`scripts/download_seed.py` docstring says "Download **anonymized** seed data" — this is now stale since anonymization was removed. Update to reflect current state.

---

## FP-1 — UI Fast Path: `defer_message()` in common ✅ IMPLEMENTED

**Status:** Done — 6 tests passing. See `lol_pipeline/streams.py`.

---

## FP-2 — UI Fast Path: Fetcher priority gating ✅ IMPLEMENTED

**Status:** Done — 5 tests passing. See `lol_fetcher/main.py`.

---

## FP-3 — UI Fast Path: Fix `clear_priority` in crawler for `pages_fetched == 0` ✅ IMPLEMENTED

**Status:** Done — tests passing. See `lol_crawler/main.py`.

---

## FP-4.0 — Fix missing `await` on `blob_store.write` ✅ IMPLEMENTED

**Status:** Done — `opgg_client.py` fixed.

---

## FP-4.1 — `compute_opgg_fast_stats()` ✅ IMPLEMENTED

**Status:** Done — 7 tests passing. See `lol_pipeline/opgg_fast_stats.py`.

---

## FP-4.2 — Wire fast-path ETL into `_opgg_prefetch_bg` ✅ IMPLEMENTED

**Status:** Done — 2 tests passing. See `lol_ui/routes/stats.py`.

---

## FP-4.3 — Overwrite guard in `handle_player_stats` ✅ IMPLEMENTED (incomplete — see REV-1)

**Status:** Core logic done. Champion/role DELs missing — tracked in REV-1.

---

## FP-4 (follow-up) — Immediate visible stats from op.gg prefetch ✅ IMPLEMENTED via FP-4.0–4.3

---

## UI-SYS-1 — FEATURE: Replace "Streams" tab with "System" tab

**Severity:** Medium — operator visibility

**Request:** Rename the existing "Streams" page (live log viewer) to "System" and expand it into a consolidated system information panel. The new page should include everything currently in Streams PLUS:

1. **Request metrics dashboard:**
   - Total requests made across all domains (Riot API, op.gg, rate-limiter service)
   - Per-domain request breakdown:
     - `riot` (all Riot API regions combined)
     - `opgg` / `opgg:ui`
     - Any future sources
   - Average requests per minute over the last 1 / 10 / 30 / 60 minutes (rolling windows)

2. **Rate limiter live status:** current token bucket usage per source (short + long window counts vs limits), cooling-off active flag per source

3. **Stream health summary:** existing streams panel (stays)

**Data sources:**
- Rate-limiter service already tracks per-source buckets in Redis: `ratelimit:{source}:short` (ZSET cardinality = requests in window), `ratelimit:{source}:long`
- For historical request-per-minute data, consider a Redis time-series counter: `INCR stats:requests:{source}:{minute_bucket}` with 2-hour TTL, where `minute_bucket = floor(unix_ts / 60)`. This lets the UI compute rolling averages by summing N buckets.
- The rate-limiter `/status` endpoint already exposes current bucket counts.

**Implementation steps (TDD):**
- [ ] **Red:** Tests for new `/system/fragment` route returning request metrics + rate-limiter status
- [ ] **Green:** Implement `system` route in `lol-pipeline-ui/src/lol_ui/routes/` using existing `streams.py` as reference; add request counter increments in `rate_limiter_client.py` or a new middleware
- [ ] **Refactor:** Rename nav item from "Streams" to "System", redirect `/streams` → `/system`

---

## RL-PROXY-1 — FEATURE: Rate-limiter as HTTP proxy (cosmic-radiance model)

**Severity:** High — architectural — prevents cascading 429s at the root

**Motivation:** Today the fetcher calls `wait_for_token()` before making Riot API calls directly. If the rate-limiter HTTP service is briefly unreachable (e.g. on restart), `wait_for_token` **fails open** — all N fetcher workers fire simultaneously → Riot rate-limits all of them → thousands of DLQ entries. The cooling-off mechanism added in recent work mitigates cascades *after* the first 429, but does not prevent the initial burst from a fail-open startup race.

The root fix is to make the rate-limiter the **sole caller** of the Riot API. Fetchers never call Riot directly; they send fetch requests to the rate-limiter service, which queues them, throttles, fires, and returns the response. A down rate-limiter returns 503 → fetchers defer → no direct Riot traffic is possible without the rate-limiter being healthy.

**Reference implementation:** [cosmic-radiance](https://github.com/DarkIntaqt/cosmic-radiance) — Go HTTP proxy, single-goroutine main loop, per-(platform × endpoint) ring-buffer queues, proactive time-spread, multi-key rotation, priority lanes.

**Key design decisions from research:**

| Feature | Current pipeline | Proxy model |
|---|---|---|
| Fetcher calls Riot directly | Yes | No — proxy calls Riot |
| Fail-open risk | Yes | No — 503 → fetcher defers |
| Per-endpoint (method) buckets | No — app-level only | Yes — `(platform × endpoint)` |
| In-flight accounting | No | Implicit (single dequeue loop) |
| Proactive burst spreading | No — hard cap only | Yes — `count > elapsed/window * limit` |
| Window padding (latency buffer) | No | +125 ms per window |
| Priority queues | No | Yes — high-priority bypasses spread check |
| Multi-API-key rotation | No | Yes — cycles keys per endpoint |
| 429 reaction | Cooling-off key blocks all tokens | `LockedUntil` per (region × endpoint × key) |
| Horizontal scaling | Yes (Redis Lua) | Needs sticky routing or shared queue |

**Proposed architecture:**

```
Fetcher → POST http://rate-limiter/proxy/fetch
              { region, path, priority?, correlation_id }
          ← blocks (long-poll, up to 90s)
          ← { status_code, body, headers }

Rate-limiter /proxy/fetch:
  - Enqueues into per-(region × endpoint) priority queue
  - Main loop: time-spread dequeue → fire Riot → return response
  - On 429: set LockedUntil for (region × endpoint), return 429
  - On success: extract X-App-Rate-Limit headers, update stored limits inline
  - /token/acquire, /headers, /cooling-off become internal — removed from public API
```

**Incremental path before full overhaul:**

- **RL-PROXY-1a:** Add method-level buckets — `ratelimit:{source}:{endpoint}:short/long`. Prevents match-v5 flood from starving summoner-v4.
- **RL-PROXY-1b:** Add proactive time-spreading to Lua: only grant if `count <= elapsed_ms / window_ms * limit`.
- **RL-PROXY-1c (quick win):** Change `wait_for_token` to **fail closed** on service-unreachable — retry N times (default 3, 500 ms apart) before failing open. Prevents the startup race that caused 17k DLQ entries. `try_token` stays fail-open (it's already non-blocking).
- **RL-PROXY-1d:** Full proxy endpoint + migrate `RiotClient` to route through it.

**TDD checklist (RL-PROXY-1c — do this first):**
- [ ] **Red:** Test that `wait_for_token` retries on service-unreachable (does NOT return on first failure)
- [ ] **Green:** Add retry loop in `wait_for_token`; add `RATE_LIMITER_CONNECT_RETRIES` env var (default 3, sleep 500 ms between)
- [ ] **Refactor:** Confirm `try_token` still fails open on first error (no change)

---
