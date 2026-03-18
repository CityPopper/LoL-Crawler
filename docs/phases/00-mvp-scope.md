# Phase 00 — MVP Scope

**Role:** Product Manager
**Purpose:** Defines what "done" means for the backend MVP. Every subsequent phase must serve these criteria.

---

## What We're Building

A backend pipeline that, given a League of Legends Riot ID (`GameName#TagLine`), fetches the player's complete match history from the Riot API, stores it durably, computes per-player statistics, and operates reliably under API rate limits and transient failures.

**This phase: backend only.** Output is queryable Redis data and a CLI for operations. A web interface will be layered on top later; the Redis data schema is the contract between this backend and that future layer.

---

## MVP Success Criteria

A successful MVP means ALL of the following pass.

### Data Completeness

- AC-D1: Given a valid Riot ID, all match IDs returned by Riot Match-v5 are stored in `player:matches:{puuid}` (verified by comparing ZCARD against a known fixture player's match count).
- AC-D2: `player:stats:{puuid}` contains correct aggregated totals (`total_games`, `total_wins`, `total_kills`, `total_deaths`, `total_assists`) verified by manual computation against fixture data.
- AC-D3: Derived fields (`win_rate`, `avg_kills`, `avg_deaths`, `avg_assists`, `kda`) are mathematically correct to 4 decimal places vs expected values.
- AC-D4: Re-seeding the same player after cooldown appends new matches only; existing `player:stats` values are unchanged; `total_games` count equals original + new matches only.
- AC-D5: `match:{match_id}.status` transitions `fetched` → `parsed` for 100% of successfully processed matches in the happy-path integration test.
- AC-D6: `match:participants:{match_id}` contains exactly N PUUIDs where N is the actual participant count in the match JSON (not assumed to be 10).

### Reliability

- AC-R1: Simulate API 429 (inject fake 429 response with `Retry-After: 30`): message appears in `delayed:messages` with score within ±100ms of `(now + 31) * 1000`; message is eventually processed; final stats are correct; zero messages lost.
- AC-R2: Simulate API 5xx (inject fake 500 response): message retried with backoff schedule (attempt 1→5s, 2→15s, 3→60s, 4→5min); eventually processed or archived to DLQ after max attempts.
- AC-R3: Simulate worker crash mid-processing (cancel coroutine after dequeue, before ACK): message redelivers within `STREAM_ACK_TIMEOUT + 5` seconds; final stats are correct; no duplicate data in Redis.
- AC-R4: Simulate API 403: `system:halted = "1"` set; all service workers stop within one processing cycle (≤ `STREAM_ACK_TIMEOUT` seconds); all unprocessed messages remain in stream pending lists with count unchanged.
- AC-R5: After `admin system-resume` + `docker compose restart`: pipeline resumes; messages that were in pending lists are redelivered and processed; zero messages lost; final stats are correct.
- AC-R6: Duplicate match_id processed twice (via manual re-enqueue): final Redis data is identical to single-process result; no errors raised.

### Rate Limiting

- AC-RL1: With 1 Fetcher worker: over any 10-second window, total Riot API calls ≤ 200 (≤ 20/s sustained); verified via `ZCARD ratelimit:short` sampled every 100ms.
- AC-RL2: With 3 Fetcher workers: combined Riot API call rate across all 3 workers ≤ 20/s and ≤ 100/2min; verified via shared Redis rate limit counters.
- AC-RL3: Rate limiter unit test: 20 sequential `acquire_token()` calls within 1s → all 20 succeed; 21st returns `(False, wait_seconds)` where `wait_seconds > 0`.
- AC-RL4: Rate limiter unit test: 20 concurrent `asyncio.gather()` calls to `acquire_token()` → exactly 20 granted, 0 denied (atomic Lua guarantees).

### Operations

- AC-O1: `admin dlq list` with 3 DLQ entries → stdout contains exactly 3 records each with `failure_code`, `attempts`, `enqueued_at`; exit code 0.
- AC-O2: `admin dlq replay --all` → all DLQ entries requeued to their `original_stream`; `stream:dlq` XLEN = 0; pipeline processes entries to completion; exit code 0.
- AC-O3: `admin stats "GameName#TagLine"` → prints all fields from `player:stats:{puuid}` for that player; exit code 0.
- AC-O4: `just seed "GameName#TagLine"` → executes successfully from the deploy repo; exit code 0.
- AC-O5: `just run` → Redis + all long-running services start (crawler, fetcher, parser, analyzer, recovery, delay-scheduler, ui, discovery, lcu); `docker compose ps` shows 10 containers in "Up" state within 30 seconds.

---

## Out of Scope (This Phase)

- S3 backend for RawStore (stub implemented, not tested)
- Multi-region or multi-node deployment
- Observability stack (Prometheus, Grafana, Jaeger) — structured JSON logs only
- Schema migration tooling
- Authentication, API gateway, or access control

**Note:** Web UI (`lol-pipeline-ui`) and Discovery Service (`lol-pipeline-discovery`) were added post-MVP and are now part of the stack. Discovery implements automatic recursive co-player fan-out (idle-only, lowest priority). Web UI is port 8080. See README for details.

---

## Future Web Layer Contract

The Redis schema defined in `docs/architecture/04-storage.md` is the read contract for the future web layer. Key read patterns expected:

| Query | Redis operation |
|-------|----------------|
| Player stats | `HGETALL player:stats:{puuid}` |
| Top champions | `ZREVRANGE player:champions:{puuid} 0 9 WITHSCORES` |
| Match history | `ZREVRANGEBYSCORE player:matches:{puuid} +inf -inf LIMIT 0 20` |
| Match detail | `HGETALL match:{match_id}` + `SMEMBERS match:participants:{match_id}` |
| Participant stats | `HGETALL participant:{match_id}:{puuid}` |

No Redis commands are exposed directly to the web layer. A thin API service (FastAPI) will sit between Redis and the web interface, reading these keys. That API service is a future phase.

---

## Definition of Done for Each Phase

A phase is complete when:
1. All ACs in the phase doc pass (automated assertions where possible)
2. `pytest tests/unit` exits 0, 0 failures
3. `pytest tests/integration` exits 0, 0 failures (where applicable)
4. Coverage: lol-pipeline-common ≥ 90%; each service ≥ 80%
5. `docker build` succeeds for all images in the phase
6. No unresolved `TODO`/`FIXME` in delivered code unless tracked in a phase AC
