# Phase 03 — Ingestion Pipeline

**Role:** Product Manager
**Objective:** Given a Riot ID, the system fetches the player's complete match history and stores raw match JSON in RawStore. Three services (Seed, Crawler, Fetcher) are complete, tested, and running in Docker.

**Complexity: MEDIUM** — three independent services; Crawler early-exit pagination logic and Fetcher idempotency need care.

**Value unlocked:** A real player's match data can be ingested end-to-end. The RawStore fills up. Parser (Phase 04) can begin.

---

## Dependencies

- Phase 02b complete (`lol-pipeline-common` v1.0.0 tagged and installable)

---

## Deliverables

1. `lol-pipeline-seed` service — complete with unit tests and Docker image
2. `lol-pipeline-crawler` service — complete with unit tests and Docker image
3. `lol-pipeline-fetcher` service — complete with unit tests and Docker image
4. Fixtures: `tests/fixtures/account.json` and `tests/fixtures/account_unicode.json` populated with realistic Riot Account-v1 response structure

> **Development order within this phase:** Services are developed in parallel. Each is tested independently with fakeredis + respx mocks. Integration of all three is verified by the Phase 06 end-to-end test.

---

## Acceptance Criteria

### Seed Service (`lol-pipeline-seed`)

- AC-03-01: `seed_player("Faker#KR1")` with mocked 200 Riot response: `HGETALL player:{puuid}` contains `game_name=Faker`, `tag_line=KR1`, `region=kr`, `seeded_at=<epoch_ms_within_1s>`; `XLEN stream:puuid` = 1; process exits 0.
- AC-03-02: Input `"FakerKR1"` (no `#`) → `ValueError` raised before any HTTP call; `mock_acquire_token.call_count == 0`.
- AC-03-03: `seeded_at` set 10 minutes ago with `SEED_COOLDOWN_MINUTES=30` → no publish; log output contains `"skip"`; `XLEN stream:puuid` = 0.
- AC-03-04: `last_crawled_at` set 10 minutes ago (no `seeded_at`) with `SEED_COOLDOWN_MINUTES=30` → no publish; log contains `"skip"`.
- AC-03-05: `seeded_at` = 60 minutes ago, `last_crawled_at` = 10 minutes ago (both present; `last_crawled_at` is newer and within cooldown) → skips.
- AC-03-06: `seeded_at` = 10 minutes ago, `last_crawled_at` = 60 minutes ago (both present; `seeded_at` is newer and within cooldown) → skips.
- AC-03-07: `last_crawled_at` set exactly `SEED_COOLDOWN_MINUTES * 60` seconds ago (at boundary) → proceeds; publish occurs; `XLEN stream:puuid` = 1.
- AC-03-08: Neither `seeded_at` nor `last_crawled_at` present → proceeds (new player); `XLEN stream:puuid` = 1.
- AC-03-09: Mocked Riot 404 → process exits 1; `EXISTS player:{puuid}` = 0 (no partial write).
- AC-03-10: Mocked Riot 403 → `GET system:halted` = `"1"`; process exits 1.
- AC-03-11: `SET system:halted "1"` before invocation → process exits 0 immediately; `mock_http_client.call_count == 0`.
- **Unit test count: 11 tests. All passing. Coverage ≥ 80%.**
- **Docker image `lol-pipeline/seed` builds successfully. No HEALTHCHECK in Dockerfile.**

### Crawler Service (`lol-pipeline-crawler`)

- AC-03-12: Consume a `stream:puuid` message; mocked Riot response returns 0 match IDs → `XLEN stream:match_id` = 0; `HGET player:{puuid} last_crawled_at` is set (non-null); ACK sent.
- AC-03-13: Mocked response returns 100 match IDs (1 API page) → `XLEN stream:match_id` = 100; `ZCARD player:matches:{puuid}` = 100.
- AC-03-14: Mocked 3 paginated responses (100 + 100 + 50 = 250 match IDs) → `XLEN stream:match_id` = 250.
- AC-03-15: All match IDs on first page already in `player:matches:{puuid}` ZSET → pagination stops after page 1; `XLEN stream:match_id` = 0; `ZCARD player:matches:{puuid}` unchanged.
- AC-03-16: First page has 60 known IDs + 40 new → `XLEN stream:match_id` = 40; pagination stops (early-exit on first fully-known page).
- AC-03-17: `ZRANGE player:matches:{puuid}` called exactly 1 time per crawl cycle (not once per match ID); verified by mock call count.
- AC-03-18: `last_crawled_at` set only after all pages processed; verified by injecting a failure on page 2 and asserting `last_crawled_at` is not written.
- AC-03-19: Mocked Riot 403 → `GET system:halted` = `"1"`; `HGET player:{puuid} last_crawled_at` = null (not updated); no ACK sent.
- AC-03-20: `SET system:halted "1"` before message processing → no ACK sent; message remains in pending list; worker exits its loop.
- **Unit test count: 9 tests. All passing. Coverage ≥ 80%.**
- **Docker image `lol-pipeline/crawler` builds successfully.**

### Fetcher Service (`lol-pipeline-fetcher`)

- AC-03-21: Consume `stream:match_id` message; `raw:{match_id}` already exists in RawStore → `mock_http_client.call_count == 0`; 1 message published to `stream:parse`; ACK sent.
- AC-03-22: `raw:{match_id}` does not exist; mocked Riot 200 → `raw:{match_id}` written; `HGET match:{match_id} status` = `"fetched"`; 1 message published to `stream:parse`; ACK sent.
- AC-03-23: Mocked Riot 404 → `HGET match:{match_id} status` = `"not_found"`; ACK sent; `XLEN stream:parse` = 0.
- AC-03-24: Mocked Riot 429 with `Retry-After: 30` header → entry in `delayed:messages` with score = `(now_ms + 31000)` ±100ms; `source_stream = "stream:match_id"` in entry; ACK sent; `XLEN stream:parse` = 0.
- AC-03-25: Mocked Riot 500 → `nack_to_dlq` called with `failure_code="http_5xx"`; entry in `delayed:messages` (attempts < max) or `stream:dlq` (attempts >= max); ACK sent.
- AC-03-26: `RawStore.set()` raises `Exception` → `XLEN stream:parse` = 0; `nack_to_dlq` called; ACK sent.
- AC-03-27: Mocked Riot 403 → `GET system:halted` = `"1"`; no ACK; worker exits; `XLEN stream:parse` = 0.
- AC-03-28: Message with `attempts = MAX_ATTEMPTS` (at limit) → entry in `stream:dlq` with all DLQ envelope fields: `failure_code`, `dlq_attempts=0`, `original_stream="stream:match_id"`, `source_stream="stream:match_id"`, `original_message_id`, `payload`.
- **Unit test count: 8 tests. All passing. Coverage ≥ 80%.**
- **Docker image `lol-pipeline/fetcher` builds successfully.**

### Phase Gate: Ingestion Smoke Test

- AC-03-29: With Redis running and all three service images available, manually run: `just seed "TestPlayer#NA1"` (mocked HTTP via environment-injected respx, or against real Riot API with a valid key) → `EXISTS player:{puuid}` = 1; `XLEN stream:puuid` ≥ 1.
- AC-03-30: Start Crawler container against Redis; after processing `stream:puuid` message → `XLEN stream:match_id` ≥ 0 (may be 0 for new players with no history); `HGET player:{puuid} last_crawled_at` is non-null.

---

## Notes

- Seed is a one-shot service (`python -m lol_seed "GameName#TagLine"`). It is tested as a function (`seed_player(riot_id, redis, http_client)`) with dependency injection — not as a subprocess. The Docker entrypoint calls this function.
- Crawler and Fetcher are long-running worker loops. Tests invoke the worker function once (processing one message) with injected dependencies.
- Crawler reads `player:matches:{puuid}` (a ZSET written solely by Parser) for deduplication: match IDs already present are not re-published to `stream:match_id`. Crawler does NOT write to this key. A re-crawl window exists where a match has been fetched but Parser has not yet run; on the next crawl the same match ID may be re-published. Fetcher's `RawStore.exists()` check and Parser's idempotent writes make this safe.
