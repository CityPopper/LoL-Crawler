# Monitoring & Observability

> **Runtime note:** Commands below use `docker compose`. Replace with `podman compose` when using the default Podman runtime, or use `just` wrappers which auto-detect the runtime.

## Current Observability Stack

The pipeline uses a simple, file-based observability approach suitable for single-developer local operation.

| Layer | Tool | Access |
|-------|------|--------|
| Structured logs | JSON to stdout + rotating log files | `just logs <svc>`, `./logs/*.log`, Web UI `/logs` |
| Stream metrics | Redis CLI (`XLEN`, `XINFO`, `ZCARD`) | `just streams`, Web UI `/streams` |
| System status | `system:halted` flag in Redis | Web UI `/streams`, `redis-cli GET system:halted` |
| Container status | Docker Compose | `docker compose ps` |
| Match data | Web UI | `http://localhost:8080` |

---

## Key Metrics

### Stream Depths

The primary health indicator. Streams should drain to near-zero during idle periods.

```bash
# Quick view
just streams

# Detailed per-stream
docker compose exec redis redis-cli XLEN stream:puuid
docker compose exec redis redis-cli XLEN stream:match_id
docker compose exec redis redis-cli XLEN stream:parse
docker compose exec redis redis-cli XLEN stream:analyze
docker compose exec redis redis-cli XLEN stream:dlq
docker compose exec redis redis-cli XLEN stream:dlq:archive
docker compose exec redis redis-cli ZCARD delayed:messages
```

**What to watch for:**

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| `stream:puuid` | 0-5 | >20 (crawler not keeping up) | Growing continuously |
| `stream:match_id` | 0-50 | >200 (fetcher bottleneck) | >1000 |
| `stream:parse` | 0-10 | >50 (parser not keeping up) | Growing continuously |
| `stream:analyze` | 0-20 | >100 (analyzer lock contention) | Growing continuously |
| `stream:dlq` | 0 | >5 (failures accumulating) | >20 |
| `delayed:messages` | 0-10 | >50 (rate limiting or errors) | >200 |

### Pending Entry Counts

Messages delivered to a consumer but not yet ACKed. High counts indicate slow processing or dead workers.

```bash
# Per-stream pending counts
docker compose exec redis redis-cli XPENDING stream:puuid crawlers - + 10
docker compose exec redis redis-cli XPENDING stream:match_id fetchers - + 10
docker compose exec redis redis-cli XPENDING stream:parse parsers - + 10
docker compose exec redis redis-cli XPENDING stream:analyze analyzers - + 10
docker compose exec redis redis-cli XPENDING stream:dlq recovery - + 10
```

### DLQ Metrics

```bash
# Active DLQ entries
docker compose exec redis redis-cli XLEN stream:dlq

# Archived (exhausted) entries
docker compose exec redis redis-cli XLEN stream:dlq:archive

# DLQ entries by failure code (inspect a sample)
docker compose exec redis redis-cli XRANGE stream:dlq - + COUNT 20
```

### Delayed Messages

```bash
# Total delayed
docker compose exec redis redis-cli ZCARD delayed:messages

# Messages due now but not yet moved (Delay Scheduler lag)
docker compose exec redis redis-cli ZRANGEBYSCORE delayed:messages -inf $(date +%s%3N) LIMIT 0 10
```

### Rate Limiter State

```bash
# Current sliding window counts
docker compose exec redis redis-cli ZCARD "ratelimit:short"
docker compose exec redis redis-cli ZCARD "ratelimit:long"

# Actual limits (persisted from Riot API X-App-Rate-Limit header)
docker compose exec redis redis-cli MGET "ratelimit:limits:short" "ratelimit:limits:long"
```

### system:halted Flag

```bash
docker compose exec redis redis-cli GET system:halted
# Returns "1" if halted, nil if running normally
```

---

## Health Check Matrix

| Service | Healthy Indicators | Unhealthy Indicators |
|---------|-------------------|---------------------|
| **redis** | `docker compose ps` shows healthy; `redis-cli PING` returns PONG | Container restarting; PING fails; OOM errors in logs |
| **crawler** | `stream:puuid` drains; `stream:match_id` grows during active crawl | `stream:puuid` grows; no match_id output; CRITICAL in logs |
| **fetcher** | `stream:match_id` drains; `stream:parse` grows | `stream:match_id` grows; excessive DLQ entries; 403/429 logs |
| **parser** | `stream:parse` drains; `stream:analyze` grows | `stream:parse` grows; `parse_error` in DLQ |
| **analyzer** | `stream:analyze` drains; `player:stats:*` keys updated | `stream:analyze` grows; lock contention warnings |
| **recovery** | `stream:dlq` drains or stable; delayed messages requeued | `stream:dlq` grows; `stream:dlq:archive` growing rapidly |
| **delay-scheduler** | `delayed:messages` count stays low; past-due messages moved promptly | `delayed:messages` grows with past-due entries |
| **discovery** | `discover:players` sorted set shrinks during idle periods | Discovery log shows repeated failures |
| **ui** | HTTP 200 on `http://localhost:8080`; `/streams` page loads | Connection refused on port 8080 |

---

## Log Analysis

### Log Format

All services emit structured JSON to stdout (and optionally to rotating files in `LOG_DIR`):

```json
{
  "timestamp": "2025-01-15T12:34:56.789012+00:00",
  "level": "INFO",
  "logger": "crawler",
  "message": "crawl complete",
  "puuid": "abc123...",
  "new_matches": 42,
  "total_pages": 3
}
```

### Log File Locations

| Source | Location |
|--------|----------|
| Container stdout | `docker compose logs <svc>` |
| Rotating files | `./logs/<service>.log` (150MB max, 3 backups) |
| Web UI | `http://localhost:8080/logs` (merged, last 50 lines, auto-refresh) |

### Filtering Logs

```bash
# Tail a specific service
just logs fetcher

# Filter for errors (Docker logs)
docker compose logs fetcher 2>&1 | grep '"level": "ERROR"'

# Filter for a specific PUUID across all log files
grep -r "abc123" logs/*.log

# Filter for a specific match ID
grep -r "NA1_12345" logs/*.log

# Parse JSON logs with jq (if installed)
docker compose logs --no-log-prefix fetcher 2>&1 | jq -r 'select(.level == "ERROR") | .message'
```

### Key Log Patterns

| Pattern | Meaning | Action |
|---------|---------|--------|
| `"level": "CRITICAL"` + `system halted` | 403 received; pipeline stopped | Rotate API key; `admin system-resume` |
| `"level": "ERROR"` + `rate limited` | 429 from Riot API | Normal — check if excessive |
| `"level": "WARNING"` + `lock contention` | Analyzer could not acquire PUUID lock | Normal — another worker holds the lock |
| `"level": "ERROR"` + `parse_error` | Parser failed on a match | Check raw blob; may need parser fix |
| `"level": "ERROR"` + `nack_to_dlq` | Message routed to DLQ after max attempts | Check DLQ for details |
| `"level": "INFO"` + `crawl complete` | Crawler finished a PUUID | Normal operation |
| `"level": "INFO"` + `auto-seeded` | UI auto-seeded a player on first lookup | Normal — player entered via Web UI |

---

## Redis Monitoring

### Memory Usage

```bash
# Overview
docker compose exec redis redis-cli INFO memory

# Key metrics
docker compose exec redis redis-cli INFO memory | grep -E "used_memory_human|maxmemory_human|mem_fragmentation_ratio"

# Total key count
docker compose exec redis redis-cli DBSIZE

# Key distribution by prefix (sample)
docker compose exec redis redis-cli --scan --pattern "raw:*" | wc -l
docker compose exec redis redis-cli --scan --pattern "player:*" | wc -l
docker compose exec redis redis-cli --scan --pattern "match:*" | wc -l
docker compose exec redis redis-cli --scan --pattern "participant:*" | wc -l
```

### Persistence Status

```bash
# Last save status
docker compose exec redis redis-cli LASTSAVE
docker compose exec redis redis-cli INFO persistence | grep -E "rdb_last|aof_"

# Trigger manual snapshot
docker compose exec redis redis-cli BGSAVE
```

### Connected Clients

```bash
docker compose exec redis redis-cli INFO clients | grep connected_clients
docker compose exec redis redis-cli CLIENT LIST
```

### Slow Queries

```bash
# Redis slow log (queries > 10ms by default)
docker compose exec redis redis-cli SLOWLOG GET 10
```

---

## Docker Monitoring

### Container Status

```bash
# All containers
docker compose ps

# Resource usage (live)
docker stats --no-stream

# Container restart count (indicator of crash loops)
docker compose ps --format '{{.Name}} {{.Status}}'
```

### Container Logs

```bash
# Last 100 lines for a service
docker compose logs --tail=100 fetcher

# Follow logs (live)
just logs fetcher

# All services, last 50 lines each
docker compose logs --tail=50
```

### Disk Usage

```bash
# Container runtime disk usage
podman system df   # or: docker system df

# Redis data directory size
du -sh redis-data/

# Match data directory size
du -sh lol-pipeline-fetcher/match-data/

# Log files
du -sh logs/
```

---

## Alerting Recommendations

For production deployment, consider alerting on these conditions:

### Critical (Immediate Response)

| Condition | Detection | Alert Method |
|-----------|-----------|--------------|
| `system:halted = "1"` | `redis-cli GET system:halted` | Cron job + email/webhook |
| Redis down | `redis-cli PING` fails | systemd watchdog or cron |
| All workers stopped | `docker compose ps` shows no running workers | Cron job |

### Warning (Investigate Soon)

| Condition | Detection | Threshold |
|-----------|-----------|-----------|
| DLQ growing | `XLEN stream:dlq` | > 10 entries |
| Stream backlog | `XLEN stream:match_id` | > 500 entries |
| Delayed queue buildup | `ZCARD delayed:messages` | > 100 entries |
| Redis memory high | `INFO memory` used_memory_human | > 80% of available RAM |
| Disk usage high | `du -sh redis-data/` | > 80% of partition |

### Simple Monitoring Script

```bash
#!/bin/bash
# monitor.sh — run via cron every 5 minutes
REDIS="docker compose exec -T redis redis-cli"

halted=$($REDIS GET system:halted)
if [ "$halted" = "1" ]; then
  echo "CRITICAL: system:halted is set" | mail -s "Pipeline Alert" admin@example.com
fi

dlq=$($REDIS XLEN stream:dlq)
if [ "$dlq" -gt 10 ]; then
  echo "WARNING: DLQ has $dlq entries" | mail -s "Pipeline Warning" admin@example.com
fi
```

---

## Dashboard Design Suggestions

### Terminal Dashboard (Current)

```bash
# Run this in a tmux/screen session for a live dashboard
watch -n 5 'just streams && echo "---" && docker compose ps --format "table {{.Name}}\t{{.Status}}" && echo "---" && docker compose exec -T redis redis-cli GET system:halted'
```

### Web UI Dashboard (Built-In)

The Web UI at `http://localhost:8080/streams` provides:

- Stream depths for all 6 streams + delayed queue
- `system:halted` status indicator
- Auto-refreshable via browser reload

The `/logs` page provides:

- Merged structured logs from all services
- Auto-refresh every 2 seconds
- Pause/resume button
- Color-coded log levels (CRITICAL, ERROR, WARNING, DEBUG)

### Future: Prometheus + Grafana

When the pipeline moves to production, the recommended observability stack is:

**1. Prometheus exporter for Redis:**

```yaml
# Add to docker-compose.prod.yml
redis-exporter:
  image: oliver006/redis_exporter:latest
  environment:
    REDIS_ADDR: redis://redis:6379
  ports:
    - "9121:9121"
```

**2. Custom metrics exporter (Python):**

A small sidecar that reads stream depths, DLQ counts, and delayed queue size, then exposes them as Prometheus metrics.

Key metrics to expose:

| Metric | Type | Labels |
|--------|------|--------|
| `pipeline_stream_depth` | Gauge | `stream` |
| `pipeline_stream_pending` | Gauge | `stream`, `group` |
| `pipeline_dlq_depth` | Gauge | — |
| `pipeline_dlq_archive_depth` | Gauge | — |
| `pipeline_delayed_count` | Gauge | — |
| `pipeline_system_halted` | Gauge | — |
| `pipeline_rate_limiter_short_count` | Gauge | — |
| `pipeline_rate_limiter_long_count` | Gauge | — |
| `pipeline_player_count` | Gauge | — |
| `pipeline_match_count` | Gauge | — |

**3. Grafana dashboards:**

- **Pipeline Overview**: Stream depths, DLQ, delayed, system status
- **Rate Limiter**: Short/long window usage, denied requests over time
- **Player Stats**: Total players, total matches, matches per day
- **Redis Health**: Memory, connected clients, operations/sec, persistence status

---

## Capacity Planning

### Redis Memory Estimates

| Data Type | Size Per Item | Typical Count | Total |
|-----------|---------------|---------------|-------|
| `raw:match:{match_id}` | ~15-30 KB | 10,000 matches | 150-300 MB |
| `match:{match_id}` hash | ~500 bytes | 10,000 matches | 5 MB |
| `participant:{match_id}:{puuid}` hash | ~300 bytes | 100,000 participants | 30 MB |
| `player:stats:{puuid}` hash | ~500 bytes | 1,000 players | 500 KB |
| `player:matches:{puuid}` sorted set | ~50 bytes/member | 100,000 entries | 5 MB |
| Stream entries | ~500 bytes each | Transient | Negligible when drained |

**Primary memory driver:** `raw:match:{match_id}` blobs. Enable `MATCH_DATA_DIR` for disk write-through to keep Redis memory manageable.

### Disk Space Estimates

| Directory | Growth Rate | Retention |
|-----------|-------------|-----------|
| `redis-data/` | Proportional to Redis memory | Permanent (RDB + AOF) |
| `match-data/` (if enabled) | ~15-30 KB per match | Permanent; use `just consolidate` to compress |
| `logs/` | Varies by activity | Rotating: 150 MB max per service, 3 backups |

### Rate-Limited Throughput

With the Riot API rate limit of 20 req/s and 100 req/2 min:

- **Sustainable throughput**: ~50 req/min (limited by the 2-minute window)
- **Match fetch rate**: ~50 matches/minute (one API call per match)
- **Player crawl rate**: depends on match history size (1-3 API calls per player for pagination)
- **Time to process 1000 matches**: ~20 minutes

---

## Future Improvements

### Short-Term

- Add `/health` endpoint to the Web UI that returns JSON service status
- Add `just monitor` command that prints a one-shot health summary
- Export `just streams` output as JSON for scripting

### Medium-Term

- Prometheus exporter sidecar for Redis and pipeline metrics
- Grafana dashboard with pre-built panels
- Structured log shipping to Loki or Elasticsearch
- Alerting via Alertmanager (PagerDuty, Slack, email)

### Long-Term

- Distributed tracing (OpenTelemetry) — trace a message through seed -> crawl -> fetch -> parse -> analyze
- Per-service latency histograms
- Automatic scaling based on stream depth
