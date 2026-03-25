# Overview & 12-Factor Alignment

## Summary

A pipeline of independent, stateless services connected by durable Redis Streams. Modeled
loosely after the crawler/indexer separation in "The Anatomy of a Web Search Engine" — raw
data collection is decoupled from analysis so each stage can fail, retry, and scale
independently. All state lives in Redis; services are stateless processes.

---

## Technology Stack

| Concern              | Technology                                              |
|----------------------|---------------------------------------------------------|
| Queues               | Redis Streams (consumer groups, ACK, persistent)        |
| All data storage     | Redis (Hashes, Strings, Sorted Sets)                    |
| Raw match blobs      | `RawStore` abstraction backed by Redis (swappable)      |
| Delayed messages     | Redis Sorted Set (`delayed:messages`) + Delay Scheduler |
| Rate limiter         | Redis Sorted Sets + atomic dual-window Lua script       |
| Config               | Environment variables only                              |
| Containers           | Podman (default) or Docker; see [07-containers.md](07-containers.md) |
| Repo structure       | Monorepo; see [08-repo-structure.md](08-repo-structure.md) |

Redis persists via RDB snapshots + AOF. In prod, point `REDIS_URL` at a managed instance
(Redis Cloud, ElastiCache, etc.). In dev, run a local Redis container.

---

## Language Selection

All services are written in Python. The primary bottleneck is the Riot API rate limit (20 req/s, 100 req/2 min) and Redis round-trip latency — not CPU or memory. The Lua rate limiter already executes atomically inside Redis, which is the optimal placement. At MVP scale no service justifies the added complexity of an additional language.

---

## Services

| # | Service           | Role                                              |
|---|-------------------|---------------------------------------------------|
| 1 | Crawler           | Fetches all match IDs for a PUUID; deduplicates   |
| 2 | Fetcher           | Downloads raw match JSON from Riot API            |
| 3 | Parser            | Transforms raw JSON into structured Redis records |
| 4 | Player Stats      | Builds incremental per-player aggregate stats     |
| 5 | Champion Stats    | Aggregates ranked solo queue stats per champion, patch, and role |
| 6 | Recovery          | Processes DLQ; retries or archives failed jobs    |
| 7 | Delay Scheduler   | Moves ready delayed messages into target streams  |
| 8 | Discovery         | Promotes co-discovered players to stream:puuid when idle |
| 9 | Admin             | One-shot CLI tool; includes `track` sub-command for seeding players |
| 10 | UI               | Read-only web dashboard for stats, streams, and logs (port 8080) |
| 11 | Admin UI          | Write operations web interface: DLQ management, halt/resume (port 8081; opt-in via `tools` profile) |

Full service contracts: [02-services.md](02-services.md)

---

## 12-Factor Alignment

| Factor               | Implementation                                                       |
|----------------------|----------------------------------------------------------------------|
| Codebase             | Monorepo; one deployable per service; shared common library          |
| Dependencies         | Explicit in `pyproject.toml`; isolated virtualenv per service        |
| Config               | All secrets and connection strings via environment variables only    |
| Backing services     | Redis treated as attached resource via `REDIS_URL`                   |
| Build / release / run| Separate stages; config injected at runtime, not build time          |
| Stateless processes  | No local state; all state in Redis                                   |
| Port binding         | Services are workers (no inbound ports); seeding via `just admin track` |
| Concurrency          | Scale each service by running more worker containers                 |
| Disposability        | Fast startup; safe crash — PEL drain + XAUTOCLAIM reclaims messages  |
| Dev/prod parity      | Local Redis container in dev; managed Redis URL in prod; same code   |
| Logs                 | Each service emits structured JSON to stdout; no log files           |
| Admin processes      | One-off tasks run as `admin` commands against same Redis instance    |

---

## Required Environment Variables

> **Source of truth:** `.env.example` at the repo root. The table below is a summary;
> see `.env.example` for full documentation, comments, and platform-specific notes.

| Variable                    | Description                                       | Default   |
|-----------------------------|---------------------------------------------------|-----------|
| `RIOT_API_KEY`              | Riot Games API key                                | required  |
| `REDIS_URL`                 | Redis connection string (`redis://host:port/db`)  | required  |
| `RAW_STORE_BACKEND`         | `redis` or `s3`                                   | `redis`   |
| `RAW_STORE_URL`             | Object store URL (s3 backend only)                | —         |
| `SEED_COOLDOWN_MINUTES`     | Minutes before a player can be re-seeded          | `30`      |
| `STREAM_ACK_TIMEOUT`        | Seconds before unACK'd message re-appears         | `60`      |
| `MAX_ATTEMPTS`              | Max delivery attempts before DLQ                  | `5`       |
| `DLQ_MAX_ATTEMPTS`          | Max recovery attempts before DLQ archive          | `3`       |
| `DELAY_SCHEDULER_INTERVAL_MS` | How often Delay Scheduler polls (ms)            | `500`     |
| `ANALYZER_LOCK_TTL_SECONDS`   | TTL for the per-PUUID Player Stats lock                   | `300`     |
| `API_RATE_LIMIT_PER_SECOND`   | Riot API per-second request cap (1s sliding window)       | `20`      |
| `MATCH_DATA_DIR`              | Directory for write-through raw match JSON disk persistence | `` |
| `DISCOVERY_POLL_INTERVAL_MS`  | How often Discovery polls for idle pipeline (ms) | `5000` |
| `DISCOVERY_BATCH_SIZE`        | Max players promoted per idle poll | `10` |
| `ADMIN_UI_SECRET`             | Shared secret for Admin UI `X-Admin-Secret` header | required (Admin UI only) |
