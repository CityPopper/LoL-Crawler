# Troubleshooting Guide

> **Runtime note:** Commands below use `docker compose`. Replace with `podman compose` when using the default Podman runtime, or use `just` wrappers which auto-detect the runtime.

## Quick Diagnostic Commands

Run these first when something seems wrong:

```bash
# 1. Is everything running?
docker compose ps

# 2. Is the system halted?
docker compose exec redis redis-cli GET system:halted

# 3. What do the stream depths look like?
just streams

# 4. Any errors in recent logs?
docker compose logs --tail=20 2>&1 | grep -i error

# 5. Is Redis healthy?
docker compose exec redis redis-cli PING

# 6. How much memory is Redis using?
docker compose exec redis redis-cli INFO memory | grep used_memory_human
```

---

## Common Symptoms & Solutions

| Symptom | Likely Cause | Solution |
|---------|-------------|----------|
| All services restarting every few seconds | `system:halted` is set | Rotate API key, `just admin system-resume`, `docker compose restart` |
| `stream:match_id` growing, `stream:parse` empty | Fetcher stuck or crashed | `just logs fetcher`, check for 403/429/crash |
| `stream:parse` growing, `stream:analyze` empty | Parser stuck or crashed | `just logs parser`, check for parse errors |
| `stream:analyze` growing steadily | Analyzer lock contention or crash | `just logs analyzer`, check for lock warnings |
| DLQ count increasing | Repeated failures on specific messages | `just admin dlq list` to inspect failure codes |
| No stream activity at all | Nothing seeded, or all services halted | `just seed "Name#Tag" region` |
| `delayed:messages` growing | Rate limiting or Delay Scheduler down | Check Delay Scheduler: `just logs delay-scheduler` |
| Web UI returns 500 | Redis connection issue or config error | `just logs ui`, check `REDIS_URL` |
| `just seed` hangs | Stack not running | Wait for auto-start, or run `just up` first |
| Container exits immediately | Missing env var or import error | `docker compose logs <svc>` for the error |

---

## Tracing a Message Through the Pipeline

To follow a specific player or match through the entire pipeline:

### Trace by Riot ID

```bash
# 1. Seed the player
just seed "Faker#KR1" kr

# 2. Find the PUUID
docker compose exec redis redis-cli GET "player:name:faker#kr1"

# 3. Check if the PUUID was published to stream:puuid
docker compose exec redis redis-cli XRANGE stream:puuid - + COUNT 5

# 4. Check crawler output — match IDs for this PUUID
docker compose exec redis redis-cli ZRANGE "player:matches:<puuid>" 0 -1

# 5. Check a specific match
docker compose exec redis redis-cli HGETALL "match:<match_id>"

# 6. Check participant data
docker compose exec redis redis-cli HGETALL "participant:<match_id>:<puuid>"

# 7. Check final stats
docker compose exec redis redis-cli HGETALL "player:stats:<puuid>"
```

### Trace by Match ID

```bash
# 1. Check match status
docker compose exec redis redis-cli HGET "match:<match_id>" status

# 2. Check if raw blob exists
docker compose exec redis redis-cli EXISTS "raw:match:<match_id>"

# 3. Check if match was parsed
docker compose exec redis redis-cli SISMEMBER "match:status:parsed" "<match_id>"

# 4. Check if match failed
docker compose exec redis redis-cli SISMEMBER "match:status:failed" "<match_id>"

# 5. Check participants
docker compose exec redis redis-cli SMEMBERS "match:participants:<match_id>"
```

---

## Stream Debugging

### XINFO — Stream Metadata

```bash
# Stream info (length, groups, first/last entry)
docker compose exec redis redis-cli XINFO STREAM stream:match_id

# Consumer groups on a stream
docker compose exec redis redis-cli XINFO GROUPS stream:match_id

# Consumers within a group
docker compose exec redis redis-cli XINFO CONSUMERS stream:match_id fetchers
```

### XPENDING — Pending Entry List

Messages delivered to a consumer but not yet ACKed:

```bash
# Summary: total pending, min/max IDs, consumer counts
docker compose exec redis redis-cli XPENDING stream:match_id fetchers

# Detailed: show pending entries with idle time
docker compose exec redis redis-cli XPENDING stream:match_id fetchers - + 10

# Output columns: entry-id, consumer-name, idle-time-ms, delivery-count
```

**High idle time (> STREAM_ACK_TIMEOUT * 1000 ms)** means messages should have been reclaimed by XAUTOCLAIM but were not. This indicates the service's consume loop is not running.

**High delivery count** means a message keeps failing and being redelivered. After `MAX_ATTEMPTS` (default 5), it should go to the DLQ.

### XRANGE — Read Stream Entries

```bash
# First 5 entries
docker compose exec redis redis-cli XRANGE stream:dlq - + COUNT 5

# Last 5 entries
docker compose exec redis redis-cli XREVRANGE stream:dlq + - COUNT 5

# Entries in a time range (millisecond timestamps)
docker compose exec redis redis-cli XRANGE stream:match_id 1705000000000 1705100000000
```

### XLEN — Stream Length

```bash
docker compose exec redis redis-cli XLEN stream:match_id
```

**If a stream is unexpectedly long:**

1. Check if the downstream consumer is running: `docker compose ps`
2. Check if the consumer group exists: `XINFO GROUPS stream:match_id`
3. Check consumer logs: `just logs fetcher`
4. Check if `system:halted` is set: consuming returns empty when halted

---

## DLQ Investigation

### List DLQ Entries

```bash
# Via admin CLI
just admin dlq list

# Via Redis directly
docker compose exec redis redis-cli XRANGE stream:dlq - + COUNT 20
```

### Understand Failure Codes

| Code | Meaning | Recovery |
|------|---------|----------|
| `http_403` | API key invalid/revoked | Rotate key, resume, replay |
| `http_429` | Rate limit exceeded | Automatic via Delay Scheduler; check if excessive |
| `http_5xx` | Riot API server error | Automatic retry with exponential backoff |
| `http_404` | Match not found | Permanent; ACKed and discarded |
| `parse_error` | Parser failed on match JSON | Check raw blob; fix parser if schema changed |
| `unknown` | Unexpected error | Check logs for stack trace |

### Replay DLQ

```bash
# Replay all entries (re-publish to original streams)
just admin dlq replay --all

# Replay a specific entry
just admin dlq replay <entry-id>

# Clear all entries (discard without replaying)
just admin dlq clear --all
```

### Check DLQ Archive

```bash
# Entries that exhausted all retry attempts
docker compose exec redis redis-cli XLEN stream:dlq:archive
docker compose exec redis redis-cli XRANGE stream:dlq:archive - + COUNT 10
```

---

## Rate Limiter Debugging

The rate limiter uses two Redis Sorted Sets for sliding windows:

```bash
# Short window (1 second, default 20 requests)
docker compose exec redis redis-cli ZCARD "ratelimit:short"

# Long window (2 minutes, default 100 requests)
docker compose exec redis redis-cli ZCARD "ratelimit:long"

# Actual limits (auto-detected from Riot API X-App-Rate-Limit header)
docker compose exec redis redis-cli MGET "ratelimit:limits:short" "ratelimit:limits:long"
# Returns the real limits for your key type (dev: 20/1s, 100/2min; prod: varies)
```

### Rate Limiter Not Working

If you see more 429s than expected:

```bash
# Check if limits were detected
docker compose exec redis redis-cli MGET "ratelimit:limits:short" "ratelimit:limits:long"
# If nil, the first API call hasn't succeeded yet (limits are persisted on first 200 response)

# Check how many workers are competing
docker compose ps | grep -c fetcher
docker compose ps | grep -c crawler

# Reduce workers
just scale fetcher 1
just scale crawler 1
```

### Rate Limiter State Reset

If the rate limiter is in a bad state (e.g., after a Redis restart):

```bash
# The limiter self-heals — old entries expire from the sliding window automatically.
# To force a clean slate:
docker compose exec redis redis-cli DEL "ratelimit:short" "ratelimit:long"
```

---

## Analyzer Lock Debugging

The Analyzer uses a per-PUUID distributed lock (Redis `SETNX` with TTL) to prevent duplicate stat computation.

```bash
# Check if a lock exists for a PUUID
docker compose exec redis redis-cli EXISTS "player:stats:lock:<puuid>"

# Check lock TTL
docker compose exec redis redis-cli TTL "player:stats:lock:<puuid>"

# If a lock is stuck (TTL shows a large value or -1):
# The lock has ANALYZER_LOCK_TTL_SECONDS (default 300) TTL.
# Wait for it to expire, or manually delete it:
docker compose exec redis redis-cli DEL "player:stats:lock:<puuid>"
```

### Lock Contention

If `stream:analyze` is growing and logs show "lock contention" warnings:

1. This is normal when multiple analyze messages arrive for the same PUUID
2. The lock holder processes all pending matches; others ACK and discard
3. No data is lost — it is processed by the lock holder
4. If the lock holder crashes, the lock expires after `ANALYZER_LOCK_TTL_SECONDS` and another worker takes over

---

## Redis State Inspection Recipes

### Player Overview

```bash
PUUID="<puuid>"

# Basic player info
docker compose exec redis redis-cli HGETALL "player:$PUUID"

# Stats
docker compose exec redis redis-cli HGETALL "player:stats:$PUUID"

# Match count
docker compose exec redis redis-cli ZCARD "player:matches:$PUUID"

# Most recent matches
docker compose exec redis redis-cli ZREVRANGE "player:matches:$PUUID" 0 4 WITHSCORES

# Top champions
docker compose exec redis redis-cli ZREVRANGE "player:champions:$PUUID" 0 4 WITHSCORES

# Roles
docker compose exec redis redis-cli ZREVRANGE "player:roles:$PUUID" 0 -1 WITHSCORES
```

### Match Overview

```bash
MATCH_ID="NA1_12345"

# Match metadata
docker compose exec redis redis-cli HGETALL "match:$MATCH_ID"

# Raw blob exists?
docker compose exec redis redis-cli EXISTS "raw:match:$MATCH_ID"

# Raw blob size
docker compose exec redis redis-cli STRLEN "raw:match:$MATCH_ID"

# Participants
docker compose exec redis redis-cli SMEMBERS "match:participants:$MATCH_ID"

# A specific participant
docker compose exec redis redis-cli HGETALL "participant:$MATCH_ID:<puuid>"
```

### System State

```bash
# System halted?
docker compose exec redis redis-cli GET system:halted

# Total players
docker compose exec redis redis-cli --scan --pattern "player:stats:*" | wc -l

# Total parsed matches
docker compose exec redis redis-cli SCARD "match:status:parsed"

# Total failed matches
docker compose exec redis redis-cli SCARD "match:status:failed"

# Discovered players waiting
docker compose exec redis redis-cli ZCARD "discover:players"

# All stream depths
for s in stream:puuid stream:match_id stream:parse stream:analyze stream:dlq stream:dlq:archive; do
  echo "$s: $(docker compose exec -T redis redis-cli XLEN $s)"
done
echo "delayed:messages: $(docker compose exec -T redis redis-cli ZCARD delayed:messages)"
```

### Key Count by Prefix

```bash
# Count keys by prefix (useful for capacity planning)
for prefix in "raw:" "match:" "participant:" "player:" "stream:" "ratelimit:"; do
  count=$(docker compose exec -T redis redis-cli --scan --pattern "${prefix}*" | wc -l)
  echo "$prefix* = $count"
done
```

---

## Test Failure Debugging

### Unit Test Failures

```bash
# Run with verbose output
cd lol-pipeline-crawler
source .venv/bin/activate
python -m pytest tests/unit -v --tb=long

# Run a single failing test
python -m pytest tests/unit/test_main.py::test_name -v --tb=long -s

# Run with print statements visible (-s disables output capture)
python -m pytest tests/unit -v -s
```

### Common Unit Test Issues

| Issue | Cause | Fix |
|-------|-------|-----|
| `ModuleNotFoundError: lol_pipeline` | Common library not installed | `pip install -e ../lol-pipeline-common` |
| `fakeredis` errors | Wrong version or missing async support | `pip install "fakeredis[aioredis]"` |
| `respx` mock not matching | URL mismatch | Check exact URL including query params |
| `freezegun` not freezing | Using `time.time()` instead of `datetime.now()` | Use `datetime.now(tz=UTC)` everywhere |
| Async test not running | Missing pytest-asyncio | Ensure `asyncio_mode = auto` in `pyproject.toml` |

### Contract Test Failures

```bash
# Run contract tests with verbose output
cd lol-pipeline-crawler
python -m pytest tests/contract -v --tb=long

# Common cause: schema changed but pact file not updated
# Fix: update the pact JSON in pacts/ to match the new schema
```

### Integration Test Failures

```bash
# Integration tests use testcontainers — requires Podman or Docker
just integration

# Common issue: container runtime not running
podman info   # or: docker info

# Common issue: port conflict (another Redis on 6379)
podman ps | grep 6379   # or: docker ps | grep 6379
```

### Lint Failures

```bash
# See exactly what ruff found
cd lol-pipeline-crawler
ruff check . --no-fix

# Auto-fix safe issues
ruff check --fix .

# Format
ruff format .
```

### Type Check Failures

```bash
cd lol-pipeline-crawler
MYPYPATH="../lol-pipeline-common/src" mypy src/ --show-error-codes

# Common issue: missing type stubs
pip install types-redis
```

---

## Log Analysis

### Find Errors Across All Services

```bash
# Search log files for errors
grep '"level": "ERROR"' logs/*.log

# Search for CRITICAL (system halt)
grep '"level": "CRITICAL"' logs/*.log

# Search for a specific match ID
grep "NA1_12345" logs/*.log

# Search for a specific PUUID
grep "<puuid>" logs/*.log
```

### Parse JSON Logs

```bash
# If jq is installed:

# All errors from fetcher
cat logs/fetcher.log | jq -r 'select(.level == "ERROR") | "\(.timestamp) \(.message)"'

# All DLQ entries
cat logs/recovery.log | jq -r 'select(.message | contains("dlq")) | "\(.timestamp) \(.message)"'

# Rate limit events
cat logs/riot_api.log | jq -r 'select(.message | contains("rate")) | "\(.timestamp) \(.message)"'
```

### Web UI Log Viewer

Navigate to `http://localhost:8080/logs` for a merged view of all service logs with:
- Auto-refresh every 2 seconds
- Pause/resume button
- Color-coded log levels
- Last 50 lines from all services, sorted by timestamp

---

## Nuclear Options

These commands are destructive. Use them only when you understand the consequences.

### Wipe All Redis Data

```bash
# WARNING: Destroys ALL pipeline state — stats, matches, streams, everything.
# match-data/ on disk is NOT affected.
just reset
```

### Flush a Single Stream

```bash
# WARNING: Destroys all messages in the stream. Pending entries are lost.
docker compose exec redis redis-cli DEL stream:dlq
```

### Delete All Player Stats (Recomputable)

```bash
# Player stats are fully recomputable from match data.
# This forces the Analyzer to recompute everything.
docker compose exec redis redis-cli --scan --pattern "player:stats:*" \
  | xargs -I{} docker compose exec -T redis redis-cli DEL "{}"
```

### Force-Clear system:halted

```bash
# Prefer: just admin system-resume
# Direct: only if admin CLI is broken
docker compose exec redis redis-cli DEL system:halted
docker compose restart
```

### Delete a Stuck Lock

```bash
# Only if you're sure the lock holder is dead and the lock hasn't expired
docker compose exec redis redis-cli DEL "player:stats:lock:<puuid>"
```

### Force Remove All Containers and Rebuild

```bash
# WARNING: Wipes all containers but preserves data volumes
docker compose down
just build
just run
```

### Complete Factory Reset

```bash
# WARNING: Destroys everything — containers, volumes, built images, Redis data
podman compose down -v --rmi all
# match-data/ on disk is NOT removed
just up
```
