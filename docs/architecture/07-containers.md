# Containers

## Overview

Every service runs as a container (Podman by default; Docker available via `RUNTIME=docker`). The Delay Scheduler is also a container. Redis runs as a container in dev and as a managed service in prod. There are no host-level dependencies beyond Podman or Docker.

---

## Image Design

### Service Image Pattern (multi-stage)

All services share a single `Dockerfile.service` at the repo root, parameterized by build args:

```dockerfile
# Dockerfile.service — unified for all pipeline services
ARG SERVICE_NAME
ARG MODULE_NAME

FROM python:3.14-slim AS builder
WORKDIR /build

COPY lol-pipeline-common/ ./common/
RUN pip install --no-cache-dir ./common/

ARG SERVICE_NAME
COPY lol-pipeline-${SERVICE_NAME}/pyproject.toml .
COPY lol-pipeline-${SERVICE_NAME}/src/ ./src/
RUN pip install --no-cache-dir .

FROM python:3.14-slim AS runtime
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.14/site-packages \
                    /usr/local/lib/python3.14/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

RUN adduser --disabled-password --no-create-home appuser
USER appuser

STOPSIGNAL SIGTERM

ARG MODULE_NAME
ENV MODULE_NAME=${MODULE_NAME}
CMD python -m ${MODULE_NAME}
```

Build a specific service with:
```bash
docker build -f Dockerfile.service \
  --build-arg SERVICE_NAME=crawler \
  --build-arg MODULE_NAME=lol_crawler \
  -t lol-pipeline/crawler .
```

Healthchecks are defined in `docker-compose.yml` (workers use Redis ping, UI uses HTTP).

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

All services use the shared `Dockerfile.service` at the repo root, parameterized via `SERVICE_NAME` and `MODULE_NAME` build args:

```yaml
x-service-build: &service-build
  context: .
  dockerfile: Dockerfile.service

# Example service entry:
crawler:
  build:
    <<: *service-build
    args:
      SERVICE_NAME: crawler
      MODULE_NAME: lol_crawler
```

See `docker-compose.yml` at the repo root for the authoritative configuration of all services.

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
