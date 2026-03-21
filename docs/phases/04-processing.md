# Phase 04 — Processing Pipeline

**Role:** Product Manager
**Objective:** Raw match JSON is parsed into structured Redis data and player statistics are computed. Two services (Parser, Analyzer) are complete, tested, and running in Docker.

**Value unlocked:** First queryable player statistics exist in Redis. This is the data the future web layer will read.

**Complexity: HIGH** — Parser handles variable schema (variable participant count, optional fields); Analyzer has distributed lock logic, cursor-based incremental processing, and safe lock release. Both require thorough code review before merging, minimum 2 review iterations.

---

## Dependencies

- Phase 02b complete
- Phase 03 complete (RawStore has match blobs; `stream:parse` messages exist)

---

## Deliverables

1. `lol-pipeline-parser` service — complete with unit tests and Docker image
2. `lol-pipeline-analyzer` service — complete with unit tests and Docker image
3. Fixtures: `match_normal.json`, `match_aram.json`, `match_remake.json`, `match_large.json` fully populated with realistic Riot Match-v5 response structures

---

## Acceptance Criteria

### Parser Service (`lol-pipeline-parser`)

- AC-04-01: Consume a `stream:parse` message; `RawStore.get()` returns a valid match JSON string → `HGETALL match:{match_id}` contains all 8 required fields: `queue_id`, `game_mode`, `game_type`, `game_version`, `game_duration`, `game_start`, `platform_id`, `region`, `status=parsed`; `XLEN stream:analyze` = N (participant count); ACK sent.
- AC-04-02: 4-participant ARAM fixture → `SCARD match:participants:{match_id}` = 4; `XLEN stream:analyze` += 4; `HGETALL participant:{match_id}:{puuid}` exists for all 4 PUUIDs.
- AC-04-03: 10-participant normal fixture → `SCARD match:participants:{match_id}` = 10; `XLEN stream:analyze` += 10.
- AC-04-04: `RawStore.get()` returns `None` → `nack_to_dlq` called with `failure_code="parse_error"`; zero Redis HSET/SADD/ZADD calls (verified by mock call count = 0); ACK sent.
- AC-04-05: Match JSON missing `info.participants` key → `nack_to_dlq` called with `failure_code="parse_error"`; no partial writes.
- AC-04-06: Match JSON missing `info.gameStartTimestamp` key → `nack_to_dlq` called with `failure_code="parse_error"`; no partial writes.
- AC-04-06b: `info.gameStartTimestamp` present but value is `0` → `nack_to_dlq` called with `failure_code="parse_error"` (zero timestamp is invalid; a cursor of 0 would never advance past it). No partial writes.
- AC-04-07: Participant with all-zero items → `HGET participant:{match_id}:{puuid} items` = `"[0,0,0,0,0,0,0]"` (string representation of list).
- AC-04-08: Participant `win=True` → stored as `"1"`; `win=False` → stored as `"0"`.
- AC-04-09: Re-parsing same match (call parser twice with same message) → all writes are idempotent; `XLEN stream:analyze` is N after both calls (not 2N); no errors raised.
- AC-04-10: After successful parse: `SISMEMBER match:status:parsed {match_id}` = 1.
- AC-04-11: `ZSCORE player:matches:{puuid} {match_id}` = `game_start` epoch ms from match JSON; verified for all participants.
- AC-04-12: Parser writes `player:matches:{puuid}` ZADD **before** publishing to `stream:analyze` (ordering guarantee for Analyzer); verified by mock call ordering assertions.
- AC-04-13: `SET system:halted "1"` before processing → no ACK; no Redis writes; worker exits loop.
- **Unit test count: 13 tests. All passing. Coverage ≥ 80%.**
- **Docker image `lol-pipeline/parser` builds successfully.**

### Analyzer Service (`lol-pipeline-analyzer`)

- AC-04-14: Lock already held by another worker ID → `HGET player:stats:{puuid} total_games` not called (mock call count = 0); ACK sent immediately.
- AC-04-15: Lock acquired; cursor = 0; 3 matches in `player:matches:{puuid}` → all 3 processed; `HGET player:stats:{puuid} total_games` = `"3"`; `HGET player:stats:cursor:{puuid}` updated to highest `game_start`; `EXISTS player:stats:lock:{puuid}` = 0 (lock deleted); ACK sent.
- AC-04-16: Lock acquired; cursor = latest `game_start` → 0 new matches to process; `player:stats` unchanged; lock deleted; ACK sent.
- AC-04-17: Participant data has `total_deaths=0` → `kda = (total_kills + total_assists) / 1` (no division by zero); `HGET player:stats:{puuid} kda` ≥ 0.
- AC-04-18: Player with 0 total games (empty stats hash) → `win_rate=0`, `avg_kills=0`, `avg_deaths=0`, `avg_assists=0`, `kda=0`; no `ZeroDivisionError`.
- AC-04-19: Participant with `champion_name=None` or `champion_name=""` → that participant skipped for champion tracking; `player:champions:{puuid}` not updated for that entry; no crash; processing continues.
- AC-04-20: `ZSCORE player:champions:{puuid} {champion_name}` = number of games played on that champion; verified against known fixture data with multiple matches on same champion.
- AC-04-21: Lock is deleted after successful processing run (not just decremented or set to "0"); `EXISTS player:stats:lock:{puuid}` = 0.
- AC-04-22: Safe lock release scenario — simulate lock TTL expiry and theft: mock `GET player:stats:lock:{puuid}` to return a different worker ID → Lua returns 0; `DEL` not called; warning logged containing "lock stolen" or "lock expired"; no crash; original ACK still sent.
- AC-04-23: `SET system:halted "1"` before processing → no ACK; worker exits loop.
- AC-04-24: KDA computation: `kda = (total_kills + total_assists) / max(total_deaths, 1)` exactly; verified numerically against fixture: 10 kills, 2 deaths, 5 assists → kda = `7.5`.
- AC-04-25: All derived fields (`win_rate`, `avg_kills`, `avg_deaths`, `avg_assists`, `kda`) stored as floats rounded to 4 decimal places (e.g., `"0.6667"` for 2/3 wins; `"7.5000"` for KDA 7.5). Use `round(value, 4)` before HSET.
- **Unit test count: 12 tests. All passing. Coverage ≥ 80%.**
- **Docker image `lol-pipeline/analyzer` builds successfully.**

### Phase Gate: First Stats Visible

- AC-04-26: With all Phase 03 services having processed a fixture player's data, run Parser then Analyzer: `HGETALL player:stats:{puuid}` returns non-empty hash with correct `total_games`, `total_wins`, `kda` values matching manually computed expected values from fixture data.
- AC-04-27: `ZREVRANGE player:champions:{puuid} 0 4 WITHSCORES` returns champions ordered by games played (highest first); verified against fixture.

---

## Notes

- Analyzer uses distributed lock `player:stats:lock:{puuid}` with TTL = `ANALYZER_LOCK_TTL_SECONDS` (default 300s). The lock value is a worker ID (UUID generated at startup). Safe release uses Lua: `if redis.call("get", KEYS[1]) == ARGV[1] then return redis.call("del", KEYS[1]) else return 0 end`.
- Stats are stored as running totals (HINCRBY). The Analyzer does NOT recompute all matches on each run — it uses the cursor to process only new matches since last run. This means stats are append-only; corrupted stats require deleting the stats key and cursor, then replaying from `stream:analyze`.
- Parser idempotency for `stream:analyze`: the same PUUID-match pair published N times results in N Analyzer executions; but because Analyzer uses a cursor (ZRANGEBYSCORE with `min = cursor + 1`), the second execution finds 0 new matches and does nothing. This is correct behavior but the duplicate messages waste resources. The acceptable duplicate rate is low (only happens on re-parse via admin command).
- Role tracking: `player:roles:{puuid}` ZINCRBY updated per match per participant. `team_position` field from Riot API is used; may be empty string for non-standard modes (stored as `"UNKNOWN"` if empty).
