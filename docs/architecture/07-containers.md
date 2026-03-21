# Containers

## Overview

Every service runs as a container (Podman by default; Docker available via `RUNTIME=docker`). The Delay Scheduler is also a container. Redis runs as a container in dev and as a managed service in prod. There are no host-level dependencies beyond Podman or Docker.

---

## Image Design

### Base Image

All service images use a common base:

```dockerfile
# base.Dockerfile  (built once; used by all services)
FROM python:3.14-slim AS base
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1
```

### Service Image Pattern (multi-stage)

Each service repo has its own Dockerfile following this pattern:

```dockerfile
FROM python:3.14-slim AS builder
WORKDIR /build

# Install common library (version pinned via build arg)
ARG COMMON_VERSION=main
RUN pip install --no-cache-dir \
    "lol-pipeline-common @ git+https://github.com/your-org/lol-pipeline-common.git@${COMMON_VERSION}"

# Install service-specific deps
COPY pyproject.toml .
COPY src/ ./src/
RUN pip install --no-cache-dir .

# ---- runtime ----
FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.14/site-packages \
                    /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy service source
COPY src/ ./src/

# Health check: verify Redis is reachable
HEALTHCHECK --interval=10s --timeout=5s --retries=3 \
    CMD python -c "import asyncio, os; from lol_pipeline.redis_client import get_redis, health_check; r=get_redis(os.environ.get('REDIS_URL', 'redis://redis:6379/0')); print(asyncio.run(health_check(r)))"

CMD ["python", "-m", "service"]
```

### Image Names

| Service           | Image Name                          | Module Entry Point               |
|-------------------|-------------------------------------|----------------------------------|
| Seed              | `lol-pipeline/seed`                 | `python -m lol_seed`             |
| Crawler           | `lol-pipeline/crawler`              | `python -m lol_crawler`          |
| Fetcher           | `lol-pipeline/fetcher`              | `python -m lol_fetcher`          |
| Parser            | `lol-pipeline/parser`               | `python -m lol_parser`           |
| Analyzer          | `lol-pipeline/analyzer`             | `python -m lol_analyzer`         |
| Recovery          | `lol-pipeline/recovery`             | `python -m lol_recovery`         |
| Delay Scheduler   | `lol-pipeline/delay-scheduler`      | `python -m lol_delay_scheduler`  |
| Discovery         | `lol-pipeline/discovery`            | `python -m lol_discovery`        |
| Admin CLI         | `lol-pipeline/admin`                | `python -m lol_admin`            |
| Web UI            | `lol-pipeline/ui`                   | `python -m lol_ui` (port 8080)   |

---

## Compose File (Local Dev)

The project uses Podman Compose by default. Run with Docker via `RUNTIME=docker just <cmd>`.

## docker-compose.yml (Local Dev)

```yaml
version: "3.9"

x-service-defaults: &service-defaults
  env_file: .env
  restart: unless-stopped
  depends_on:
    redis:
      condition: service_healthy

services:

  redis:
    image: redis:7-alpine
    command: >
      redis-server
      --appendonly yes
      --appendfsync everysec
      --save 900 1
      --save 300 10
      --save 60 10000
    ports:
      - "6379:6379"
    volumes:
      - ${REDIS_DATA_DIR:-./redis-data}:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  crawler:
    <<: *service-defaults
    build:
      context: lol-pipeline-crawler
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    command: >
      sh -c "pip install -q -e /common && python -m lol_crawler"

  fetcher:
    <<: *service-defaults
    build:
      context: lol-pipeline-fetcher
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    command: >
      sh -c "pip install -q -e /common && python -m lol_fetcher"

  parser:
    <<: *service-defaults
    build:
      context: lol-pipeline-parser
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    command: >
      sh -c "pip install -q -e /common && python -m lol_parser"

  analyzer:
    <<: *service-defaults
    build:
      context: lol-pipeline-analyzer
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    command: >
      sh -c "pip install -q -e /common && python -m lol_analyzer"

  recovery:
    <<: *service-defaults
    build:
      context: lol-pipeline-recovery
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    command: >
      sh -c "pip install -q -e /common && python -m lol_recovery"

  delay-scheduler:
    <<: *service-defaults
    build:
      context: lol-pipeline-delay-scheduler
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    command: >
      sh -c "pip install -q -e /common && python -m lol_delay_scheduler"

  # One-shot tools — not started by default; use `docker compose run`
  seed:
    <<: *service-defaults
    build:
      context: lol-pipeline-seed
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    profiles: ["tools"]
    restart: "no"
    command: >
      sh -c "pip install -q -e /common && python -m lol_seed"

  admin:
    <<: *service-defaults
    build:
      context: lol-pipeline-admin
      args:
        COMMON_VERSION: local
    volumes:
      - ./lol-pipeline-common:/common
    profiles: ["tools"]
    restart: "no"
    command: ["python", "-m", "lol_admin"]

```

> **Note:** The example above is illustrative. See `docker-compose.yml` at the repo root
> for the authoritative configuration including all services (ui, discovery, seed, admin).

---

## Scaling Workers

Because services are stateless, they scale horizontally by running more container replicas:

```bash
# Run 3 fetcher workers in parallel
podman compose up --scale fetcher=3
# or: RUNTIME=docker just scale fetcher 3

# Or in prod (Docker Swarm / k8s):
# replicas: 3  (in the service spec)
```

The rate limiter's global Redis counter is shared across all replicas automatically.

---

## Environment Variables

All config is injected via `.env` (dev) or secrets manager (prod). No config in images.

```env
# .env (copy from .env.example — see lol-pipeline-deploy/.env.example for full list)
RIOT_API_KEY=RGAPI-...
REDIS_URL=redis://redis:6379/0
REDIS_DATA_DIR=./redis-data
RAW_STORE_BACKEND=redis
SEED_COOLDOWN_MINUTES=30
STREAM_ACK_TIMEOUT=60
MAX_ATTEMPTS=5
DLQ_MAX_ATTEMPTS=3
DELAY_SCHEDULER_INTERVAL_MS=500
ANALYZER_LOCK_TTL_SECONDS=300
DISCOVERY_POLL_INTERVAL_MS=5000
DISCOVERY_BATCH_SIZE=10
```

In prod, `REDIS_URL` points at the managed instance. Everything else is the same.

---

## Running the Seed (one-shot)

```bash
# Via Justfile (uses Podman by default)
just seed "Faker#KR1"

# Directly with podman compose
podman compose run --rm seed "Faker#KR1"
```

## Running Admin Commands

```bash
just admin dlq list
just admin stats "Faker#KR1"
just admin dlq replay --all
```
