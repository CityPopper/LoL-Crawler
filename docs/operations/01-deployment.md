# Deployment & Operations Runbook

## Prerequisites

| Requirement | Minimum Version | Check |
|-------------|----------------|-------|
| Podman | 4.x+ | `podman --version` (default runtime) |
| Podman Compose | 1.x+ | `podman-compose --version` |
| just | 1.x+ | `just --version` |
| Python | 3.12+ (for local dev/testing only) | `python3 --version` |
| Riot API Key | Development or Production | https://developer.riotgames.com |

> To use Docker instead of Podman: set `RUNTIME=docker` in your environment before any `just` command.

---

## Local Development Setup

### First-Time Setup

```bash
# 1. Clone the repository
git clone <repo-url> && cd LoL-Crawler

# 2. Create .env from template
just setup
# Output: "Created .env — set RIOT_API_KEY before running 'just build'."

# 3. Edit .env — set your Riot API key
# RIOT_API_KEY=RGAPI-your-key-here

# 4. Build all Docker images
just build

# 5. Start the full stack (Redis + all services)
just run

# 6. Seed your first player
just seed "Faker#KR1" kr

# 7. Open the Web UI
just ui
# Opens http://localhost:8080
```

### What `just up` Does

`just up` is a convenience alias that runs `setup`, `build`, and `run` in sequence. It is safe to re-run at any time.

---

## Docker Compose Deployment

### Architecture

All services are defined in `docker-compose.yml` at the repo root. The stack consists of:

| Service | Type | Startup | Restart Policy |
|---------|------|---------|----------------|
| redis | Infrastructure | Always | `unless-stopped` |
| crawler | Long-running worker | Auto (depends on Redis health) | `unless-stopped` |
| fetcher | Long-running worker | Auto | `unless-stopped` |
| parser | Long-running worker | Auto | `unless-stopped` |
| analyzer | Long-running worker | Auto | `unless-stopped` |
| recovery | Long-running worker | Auto | `unless-stopped` |
| delay-scheduler | Long-running worker | Auto | `unless-stopped` |
| discovery | Long-running worker | Auto | `unless-stopped` |
| ui | HTTP server (port 8080) | Auto | `unless-stopped` |
| seed | One-shot tool | On demand (`just seed`) | `no` |
| admin | One-shot tool | On demand (`just admin`) | `no` |

**Key design decisions:**

- Services mount source code as volumes — code changes take effect on `just restart <svc>` without rebuilding
- `lol-pipeline-common` is mounted at `/common` and installed in editable mode at container startup
- Redis data is persisted to `${REDIS_DATA_DIR:-./redis-data}` on the host
- Seed and admin use `profiles: ["tools"]` so they do not start with `docker compose up`
- All services share `.env` via `env_file: .env`
- All services wait for Redis healthcheck before starting (`depends_on: condition: service_healthy`)

### Volume Mounts

| Container Path | Host Path | Purpose |
|----------------|-----------|---------|
| `/common` | `./lol-pipeline-common` | Shared library (editable install) |
| `/svc` | `./lol-pipeline-{service}` | Service source (editable install) |
| `/data` (redis) | `${REDIS_DATA_DIR:-./redis-data}` | Redis RDB + AOF persistence |
| `/match-data` (fetcher) | `./lol-pipeline-fetcher/match-data` | Write-through raw match JSON |
| `/match-data` (parser) | `./lol-pipeline-fetcher/match-data` (read-only) | Parser reads match JSON from disk |
| `/logs` | `./logs` | Rotating JSON log files |

---

## Future: Bare-Metal Production

This section documents the production deployment strategy. It is not yet implemented.

### Target Architecture

- **Single bare-metal server** running Docker Compose
- Redis runs as a system service (not in Docker) or as a dedicated container with host networking
- Services run as Docker containers orchestrated by `docker-compose.prod.yml`
- No Kubernetes, no cloud — intentionally simple

### Production Differences from Dev

| Concern | Development | Production |
|---------|-------------|------------|
| Source mounting | Volume mounts for hot reload | Images contain baked-in code |
| Common library | Editable install from `/common` | Pinned version in image |
| Redis | Container, no auth, port 6379 | System service, auth + TLS, private interface |
| API key | `.env` file | Secrets manager or systemd `LoadCredential` |
| Logs | `./logs/` on host + stdout | Centralized log collection (journald, Loki, etc.) |
| Restart policy | `unless-stopped` | `always` with systemd watchdog |
| Resource limits | None | CPU + memory limits per container |

### Production docker-compose.prod.yml (Template)

```yaml
# Extends docker-compose.yml with production overrides
services:
  redis:
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --requirepass ${REDIS_PASSWORD}
      --bind 127.0.0.1
      --tls-port 6380
      --port 0
      --tls-cert-file /tls/redis.crt
      --tls-key-file /tls/redis.key
      --tls-ca-cert-file /tls/ca.crt
    ports: []  # no exposed ports

  crawler:
    volumes: []  # no source mounts
    build:
      args:
        COMMON_VERSION: "1.2.3"  # pinned
    command: "python -m lol_crawler"
    deploy:
      resources:
        limits:
          cpus: "1.0"
          memory: 512M
```

---

## Scaling Guide

### Horizontal Scaling

Services are stateless. Scale by running more replicas:

```bash
# Scale fetcher to 3 workers
just scale fetcher 3

# Or directly
docker compose up --scale fetcher=3 -d
```

**Safe to scale (stateless workers):**
- `crawler` — each instance processes different PUUIDs from the consumer group
- `fetcher` — each instance fetches different matches; rate limiter is shared via Redis
- `parser` — each instance parses different matches

**Scale with caution:**
- `analyzer` — uses per-PUUID distributed locks; scaling is safe but offers limited benefit (lock contention means only one worker processes a given PUUID at a time)

**Do NOT scale (singletons):**
- `delay-scheduler` — single instance moves messages from `delayed:messages`; multiple instances cause harmless duplicate deliveries but waste resources
- `recovery` — single instance processes `stream:dlq`; multiple instances are safe but unnecessary
- `discovery` — single instance manages idle-state promotion; multiple instances cause duplicate PUUID promotions

### Rate Limiter Sharing

The Lua-based sliding window rate limiter uses Redis Sorted Sets shared across all workers. Scaling fetcher/crawler replicas does not bypass the rate limit — the global counter is atomic.

```bash
# Verify current rate limit state
docker compose exec redis redis-cli ZCARD "ratelimit:short"
docker compose exec redis redis-cli ZCARD "ratelimit:long"
```

---

## Environment Variable Reference

> **Source of truth:** `.env.example` at the repo root.

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `RIOT_API_KEY` | Yes | — | Riot Games API key |
| `REDIS_URL` | Yes | `redis://redis:6379/0` | Redis connection string |
| `REDIS_DATA_DIR` | No | `./redis-data` | Host path for Redis persistence |
| `MATCH_DATA_DIR` | No | (empty) | Write-through raw match JSON directory; empty disables |
| `RAW_STORE_BACKEND` | No | `redis` | Raw blob backend: `redis` or `s3` |
| `RAW_STORE_URL` | No | — | S3 URL (only if backend=s3) |
| `SEED_COOLDOWN_MINUTES` | No | `30` | Minimum minutes between re-seeding the same player |
| `STREAM_ACK_TIMEOUT` | No | `60` | Seconds before unACKed messages can be reclaimed |
| `MAX_ATTEMPTS` | No | `5` | Delivery attempts before routing to DLQ |
| `DLQ_MAX_ATTEMPTS` | No | `3` | Recovery attempts before archiving from DLQ |
| `DELAY_SCHEDULER_INTERVAL_MS` | No | `500` | Delay Scheduler poll interval (ms) |
| `ANALYZER_LOCK_TTL_SECONDS` | No | `300` | Per-PUUID Analyzer lock TTL (seconds) |
| `API_RATE_LIMIT_PER_SECOND` | No | `20` | Riot API per-second request cap |
| `DISCOVERY_POLL_INTERVAL_MS` | No | `5000` | Discovery idle-check poll interval (ms) |
| `DISCOVERY_BATCH_SIZE` | No | `10` | Players promoted per Discovery poll cycle |
| `LOG_DIR` | No | `/logs` | Directory for rotating JSON log files |
| `LOG_LEVEL` | No | `INFO` | Log level: DEBUG, INFO, WARNING, ERROR, CRITICAL |

---

## Health Checks

### Redis Health Check

Defined in `docker-compose.yml`:

```yaml
healthcheck:
  test: ["CMD", "redis-cli", "ping"]
  interval: 5s
  timeout: 3s
  retries: 5
```

### Service Health (Indirect)

Services do not expose HTTP health endpoints (except the UI on port 8080). Health is determined by:

1. **Container status**: `docker compose ps` shows `Up` and `healthy`/`running`
2. **Stream activity**: Streams should drain over time; stalled streams indicate problems
3. **Log output**: Services emit structured JSON logs; absence of logs for an extended period indicates a stalled worker
4. **`system:halted` flag**: If set, all services are in a halt-exit-restart loop

```bash
# Quick health check
docker compose ps
just streams
docker compose exec redis redis-cli GET system:halted
```

---

## Operational Procedures

### Start the Full Stack

```bash
just run
# Or: docker compose up -d
```

### Stop Without Data Loss

```bash
just stop
# Pauses containers; Redis data and volumes preserved

just run
# Resume from where you left off
```

### Full Teardown (Preserve Data)

```bash
just down
# Removes containers; volumes (including Redis data) preserved

just run
# Rebuilds containers; Redis data is intact
```

### Full Reset (Wipe Everything)

```bash
just reset
# Removes containers AND volumes; Redis data is deleted
# match-data/ on disk is NOT deleted
```

### Seed a Player

```bash
just seed "Faker#KR1" kr
# Auto-starts the stack if not running
# Region defaults to na1 if omitted

# Or via the Web UI at http://localhost:8080/stats
# Enter "Faker#KR1" and select region — auto-seeds if no stats exist
```

### Force Re-Seed (Bypass Cooldown)

```bash
just admin reseed "Faker#KR1"
```

### Restart a Single Service After Code Change

```bash
just restart crawler
# Source is volume-mounted; no rebuild needed
```

### Scale a Service

```bash
just scale fetcher 3
```

### View Logs

```bash
just logs fetcher
# Or: docker compose logs -f fetcher

# Web UI logs page (merged, all services):
# http://localhost:8080/logs
```

### Inspect Stream Depths

```bash
just streams
# Output:
# stream:puuid:          0
# stream:match_id:       12
# stream:parse:          3
# stream:analyze:        0
# stream:dlq:            0
# delayed:messages:      2
```

---

## Incident Response Runbook

### HTTP 403 — API Key Invalid

**Detection:** CRITICAL log from fetcher or recovery; `system:halted = "1"`.

```bash
# 1. Confirm halt
docker compose exec redis redis-cli GET system:halted

# 2. Regenerate key at https://developer.riotgames.com
# 3. Update .env with new key

# 4. Resume
just admin system-resume

# 5. Force-restart all services (bypass Docker restart backoff)
docker compose restart

# 6. Replay DLQ
just admin dlq replay --all

# 7. Verify recovery
just streams
just logs fetcher
```

### HTTP 429 — Excessive Rate Limiting

**Detection:** Many entries in `delayed:messages`; DLQ entries with `failure_code = http_429`.

```bash
# Check delayed queue depth
docker compose exec redis redis-cli ZCARD "delayed:messages"

# Check DLQ
just admin dlq list

# If overwhelmed, reduce worker count
just scale fetcher 1
just scale crawler 1

# Check actual rate limits persisted from Riot API headers
docker compose exec redis redis-cli MGET "ratelimit:limits:short" "ratelimit:limits:long"
```

The pipeline self-heals from 429s automatically — Delay Scheduler moves delayed messages back when their wait time expires. No manual intervention is usually needed.

### Crash Loop

**Detection:** Container repeatedly restarts; `docker compose ps` shows short uptimes.

```bash
# Check if system is halted (causes intentional crash loop)
docker compose exec redis redis-cli GET system:halted

# If halted, resolve the root cause (usually 403), then:
just admin system-resume
docker compose restart

# If not halted, check logs for the crash reason
docker compose logs --tail=50 <service>

# Common causes:
# - REDIS_URL misconfigured (connection refused)
# - Missing required env var (ValidationError from pydantic-settings)
# - Python import error (missing dependency — rebuild: just build)
```

### Redis Issues

**Connection refused:**

```bash
# Is Redis running?
docker compose ps redis

# Can you connect?
docker compose exec redis redis-cli ping

# Check Redis logs
docker compose logs redis
```

**Out of memory:**

```bash
# Check memory usage
docker compose exec redis redis-cli INFO memory | grep used_memory_human

# Check key count
docker compose exec redis redis-cli DBSIZE

# If raw match blobs are filling Redis:
# Enable MATCH_DATA_DIR for disk write-through, then clear raw:* keys
docker compose exec redis redis-cli --scan --pattern "raw:*" | head -20
```

**Data corruption (rare):**

```bash
# Analyzer stats are fully recomputable
# 1. Delete corrupted stats
docker compose exec redis redis-cli --scan --pattern "player:stats:*" \
  | xargs -I{} docker compose exec -T redis redis-cli DEL "{}"

# 2. Replay analyze stream for affected PUUIDs
just admin dlq replay --all

# If parse data is corrupted:
# 1. Re-parse from raw blobs
just admin replay-parse --all
```

### DLQ Overflow

**Detection:** `stream:dlq` length is growing; `just streams` shows high DLQ count.

```bash
# List DLQ entries
just admin dlq list

# Check failure codes
docker compose exec redis redis-cli XRANGE stream:dlq - + COUNT 10

# Replay all (if root cause is resolved)
just admin dlq replay --all

# Or clear all (discard permanently)
just admin dlq clear --all

# Check archive
docker compose exec redis redis-cli XLEN stream:dlq:archive
```

### Data Corruption / Incorrect Stats

```bash
# Analyzer stats are incrementally computed and fully recomputable.
# To recompute a single player:

# 1. Get the PUUID
docker compose exec redis redis-cli GET "player:name:faker#kr1"

# 2. Delete their stats
docker compose exec redis redis-cli DEL "player:stats:<puuid>"

# 3. Reset their cursor so Analyzer reprocesses all matches
docker compose exec redis redis-cli DEL "player:stats:cursor:<puuid>"

# 4. Publish an analyze message
just admin reseed "Faker#KR1"

# To recompute ALL stats:
docker compose exec redis redis-cli --scan --pattern "player:stats:*" \
  | xargs -I{} docker compose exec -T redis redis-cli DEL "{}"
just admin replay-parse --all
```

---

## Backup & Recovery

### Redis Persistence

Redis is configured with both RDB snapshots and AOF (append-only file):

```
--appendonly yes
--appendfsync everysec
--save 900 1      # snapshot every 15 min if >= 1 key changed
--save 300 10     # snapshot every 5 min if >= 10 keys changed
--save 60 10000   # snapshot every 1 min if >= 10000 keys changed
```

Data files are stored in `${REDIS_DATA_DIR:-./redis-data}` on the host.

### Manual Backup

```bash
# Trigger an RDB snapshot
docker compose exec redis redis-cli BGSAVE

# Copy the backup
cp redis-data/dump.rdb redis-data/dump.rdb.backup.$(date +%Y%m%d)
```

### Restore from Backup

```bash
# Stop Redis
just stop

# Replace the data files
cp redis-data/dump.rdb.backup redis-data/dump.rdb

# Restart
just run
```

### Match Data on Disk

If `MATCH_DATA_DIR` is set, raw match JSON is persisted to disk independently of Redis. On a Redis reset, the parser auto-repopulates from disk.

---

## Justfile Reference

| Command | Description |
|---------|-------------|
| `just setup` | Copy `.env.example` to `.env` if missing; install pre-commit hooks |
| `just build` | Build all Docker images |
| `just run` | Start all services (`docker compose up -d`) |
| `just up` | `setup` + `build` + `run` in one step |
| `just seed "ID#TAG" region` | Seed a player (auto-starts stack) |
| `just stop` | Pause all containers (data preserved) |
| `just down` | Remove containers (data preserved) |
| `just reset` | Remove containers AND wipe Redis data |
| `just logs <svc>` | Tail logs for a service |
| `just restart <svc>` | Restart a single service |
| `just scale <svc> <N>` | Scale a service to N replicas |
| `just streams` | Show Redis stream depths |
| `just admin <args>` | Run an admin command (auto-starts stack) |
| `just ui` | Open Web UI in browser |
| `just lint` | Ruff check + format check on all services |
| `just typecheck` | Mypy on all services |
| `just check` | `lint` + `typecheck` |
| `just test` | Run all unit tests (parallel per service) |
| `just contract` | Run PACT contract tests |
| `just integration` | Run integration tests (requires Docker) |
| `just e2e` | Run end-to-end tests (requires running stack) |
| `just test-all` | Unit + contract tests |
| `just update-mocks` | Refresh test fixtures from live Riot API |
| `just consolidate` | Bundle match JSON files into JSONL+zstd archives |
