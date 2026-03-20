# Phase 06 — Operations

**Role:** Product Manager
**Objective:** The system is operable without manual Redis commands. Admin CLI covers all incident recovery scenarios. The full integration test suite passes. This is the MVP complete gate.

**Value unlocked:** The backend MVP is done. A human operator can run, monitor, and recover the pipeline entirely through `just` commands and `admin` subcommands. The Redis data is ready for a future web layer.

**Complexity: MEDIUM** — Admin CLI is straightforward (thin wrappers over common lib operations). Integration tests are the complex part — they require careful fixture setup and timing. Plan for multiple iterations on the integration test suite.

---

## Dependencies

- All of Phases 01–05 complete

---

## Deliverables

1. `lol-pipeline-admin` CLI service — complete with unit tests and Docker image
2. `lol-pipeline-deploy` Justfile — all `just` commands working
3. Full integration test suite in `lol-pipeline-common/tests/integration/` (7 scenarios)
4. Phase 00 MVP success criteria verified

---

## Acceptance Criteria

### Admin CLI (`lol-pipeline-admin`)

All commands tested against fakeredis (unit) and verified manually against a running Redis (smoke test).

- AC-06-01: `admin dlq list` with empty `stream:dlq` → stdout: `(empty)\n`; exit code 0.
- AC-06-02: `admin dlq list` with 3 DLQ entries → stdout contains exactly 3 records; each record includes `id`, `failure_code`, `attempts`, `enqueued_at` fields on one line or consistently formatted; exit code 0.
- AC-06-03: `admin dlq replay --all` with 2 DLQ entries → both XADDed to their respective `source_stream`; `XLEN stream:dlq` = 0; exit code 0.
- AC-06-04: `admin dlq replay <id>` → only that entry's `source_stream` has 1 new message; other DLQ entries remain; exit code 0.
- AC-06-05: `admin dlq clear --all` → `XLEN stream:dlq` = 0; exit code 0.
- AC-06-06: `admin replay-parse --all` with 5 entries in `match:status:parsed` set → `XLEN stream:parse` += 5; each message contains the correct `match_id`; exit code 0.
- AC-06-07: `admin replay-fetch <match_id>` → `XLEN stream:match_id` += 1; message payload contains `match_id`; exit code 0.
- AC-06-08: `admin reseed "Faker#KR1"` → `HEXISTS player:{puuid} last_crawled_at` = 0 AND `HEXISTS player:{puuid} seeded_at` = 0 (both cooldown fields deleted so Seed proceeds immediately); `XLEN stream:puuid` += 1; exit code 0.
- AC-06-09: `admin stats "Faker#KR1"` with pre-populated `player:stats:{puuid}` → stdout prints all stat fields; includes `total_games`, `win_rate`, `kda`; exit code 0. PUUID is resolved by calling the Riot Account-v1 API (requires `RIOT_API_KEY` in env); if the player is not found in Redis after resolution, exits 1 with "player not found".
- AC-06-10: `admin system-resume` → `EXISTS system:halted` = 0; stdout contains confirmation message; exit code 0.
- AC-06-11: Unknown subcommand `admin frobnitz` → stderr contains error message; exit code 1.
- **Unit test count: 11 tests. All passing. Coverage ≥ 80%.**
- **Docker image `lol-pipeline/admin` builds successfully. No HEALTHCHECK in Dockerfile.**

### Justfile (`lol-pipeline-deploy`)

- AC-06-12: `just setup` → creates `.env` from `.env.example` if `.env` does not exist; prints instructions; exit code 0.
- AC-06-13: `just redis` → `docker compose up redis -d` executes; `redis-cli ping` returns PONG within 10 seconds.
- AC-06-14: `just build` → `docker build` succeeds for all 7 service images; exit code 0.
- AC-06-15: `just run-all` → all long-running services start (crawler, fetcher, parser, analyzer, recovery, delay-scheduler, ui, discovery); containers show as "Up" (9 services + redis).
- AC-06-16: `just stop` → `docker compose down`; `docker compose ps` shows 0 running containers.
- AC-06-17: `just seed "TestPlayer#NA1"` → seed container runs; exits with code 0 (with a valid API key) or exits non-zero with an error message (with placeholder key); never hangs.
- AC-06-18: `just scale service=fetcher n=3` → `docker compose ps | grep fetcher` shows 3 fetcher containers.
- AC-06-19: `just dlq` → `admin dlq list` output appears in terminal.
- AC-06-20: `just stats "Faker#KR1"` → `admin stats "Faker#KR1"` output appears in terminal.
- AC-06-21: `just resume` → `admin system-resume` output appears in terminal; `EXISTS system:halted` = 0.
- AC-06-22: `just logs fetcher` → `docker compose logs -f fetcher` streams to terminal (does not exit immediately).

### Integration Tests (7 Scenarios)

All integration tests use testcontainers (real Redis), respx (mocked HTTP), and fixture JSON files. No real Riot API calls.

- **IT-01 — Happy path:**
  - Setup: Load `match_normal.json` fixture. Mock Riot account API, match list API, and match detail API with fixture data.
  - Execute: Seed → Crawler → Fetcher → Parser → Analyzer (each as a function call with injected deps).
  - Assert: `HGETALL player:stats:{puuid}` matches manually precomputed values for the fixture (`total_games=N`, `total_wins=W`, `kda=K`); `ZCARD match:status:parsed` = N; `HGET match:{match_id} status` = `"parsed"` for all N matches; `ZCARD player:matches:{puuid}` = N.

- **IT-02 — Idempotency (re-seed same player):**
  - Setup: Run IT-01 to completion.
  - Execute: Wait for cooldown to expire (monkeypatch time); seed same player again; run crawler; run fetcher (raw blobs exist → skip API); run parser; run analyzer.
  - Assert: `HGET player:stats:{puuid} total_games` unchanged; `ZCARD player:matches:{puuid}` unchanged; 0 errors.

- **IT-03 — 429 end-to-end recovery:**
  - Setup: Mock first fetch attempt to return 429 with `Retry-After: 5`; second attempt returns 200 with match data.
  - Execute: Fetcher → nack_to_dlq → Recovery → delayed:messages → Delay Scheduler → Fetcher retry → Parser → Analyzer.
  - Assert: Message not in `stream:dlq` after retry; `HGET match:{match_id} status` = `"parsed"`; `player:stats` correct; 0 messages in `delayed:messages` at end.

- **IT-04 — Worker crash and redelivery:**
  - Setup: fakeredis + `STREAM_ACK_TIMEOUT=2` (2 seconds for test speed).
  - Execute: Dequeue parser message; cancel coroutine before ACK; wait 3 seconds; run parser again.
  - Assert: Message redelivered; `HGET match:{match_id} status` = `"parsed"`; `SCARD match:participants:{match_id}` = expected count; 0 duplicates in `stream:analyze`.

- **IT-05 — system:halted propagation:**
  - Setup: Mock Riot 403 on fetcher's second message.
  - Execute: Fetcher processes first message (success); fetcher hits 403 on second message → sets `system:halted=1`; all service `consume()` calls return empty; pending message count stays constant.
  - Assert: `GET system:halted` = `"1"`; `XPENDING stream:match_id consumer-group - + 10` count ≥ 1 (message in pending); `XPENDING stream:parse` count unchanged.

- **IT-06 — Concurrent workers (no corruption):**
  - Setup: 2 parser instances, 2 analyzer instances (different consumer names); 10 match fixtures.
  - Execute: All 4 workers run concurrently via `asyncio.gather()`.
  - Assert: `HGET player:stats:{puuid} total_games` = 10 (not > 10, not < 10); `ZCARD match:status:parsed` = 10; `ZCARD player:matches:{puuid}` = 10.

- **IT-07 — Rate limit enforcement (3 workers):**
  - Setup: 3 fetcher instances sharing the same Redis rate limit counters; 200 `stream:match_id` messages queued; all mocked to return 200.
  - Execute: All 3 fetchers run concurrently; measure `ZCARD ratelimit:short` at 100ms intervals over 10 seconds.
  - Assert: `ZCARD ratelimit:short` never exceeds 20 at any sampled point; all 200 messages eventually processed.

### MVP Gate

- AC-06-23: All 7 integration tests pass (IT-01 through IT-07).
- AC-06-24: Total test count across all repos ≥ 120 unit tests + 7 integration tests.
- AC-06-25: Coverage: `lol-pipeline-common` ≥ 90% branch coverage; each service repo ≥ 80% branch coverage.
- AC-06-26: `docker compose build` with no cache exits 0 for all 7 service images.
- AC-06-27: All Phase 00 success criteria (AC-D1 through AC-O5) verified.

---

## Known Limitations (Documented, Not Fixed in MVP)

- No schema migration tooling: adding a field to `MessageEnvelope` requires draining all streams before deploying.
- Historical messages missed by new consumer groups: a service added after stream traffic begins will not process historical messages.
- `delayed:messages` may contain duplicate entries for the same logical message if a crash occurs between ZADD and ACK. Downstream idempotency handles this.
- S3RawStore is a stub — raises `NotImplementedError`. Switching to S3 requires implementing the stub in a future phase.
- No Prometheus/Grafana metrics. Observability is JSON logs only.
