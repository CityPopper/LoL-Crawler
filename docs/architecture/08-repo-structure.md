# Repository Structure

## Strategy: Monorepo with Shared Library

All services live as sibling directories in a single git repository. Each service has
its own `pyproject.toml` and `Dockerfile`. Infrastructure files (`docker-compose.yml`,
`.env.example`, `Justfile`) live at the repo root.

---

## Repository List

| Repository                    | Type           | Contents                                       |
|-------------------------------|----------------|------------------------------------------------|
| `lol-pipeline-common`         | Library        | All shared infrastructure (see below)          |
| `lol-pipeline-seed`           | Service        | Seed Service                                   |
| `lol-pipeline-crawler`        | Service        | Crawler Service                                |
| `lol-pipeline-fetcher`        | Service        | Fetcher + Riot API client                      |
| `lol-pipeline-parser`         | Service        | Parser Service                                 |
| `lol-pipeline-analyzer`       | Service        | Analyzer Service                               |
| `lol-pipeline-recovery`       | Service        | Recovery Service                               |
| `lol-pipeline-delay-scheduler`| Service        | Delay Scheduler Service                        |
| `lol-pipeline-ui`             | Service        | Web UI (FastAPI, port 8080)                    |
| `lol-pipeline-admin`          | Service        | Admin CLI                                      |
| `lol-pipeline-discovery`      | Service        | Discovery Service — idle fan-out of co-players |

---

## Common Library (`lol-pipeline-common`)

Package name: `lol_pipeline`

```
lol-pipeline-common/
├── pyproject.toml
├── contracts/
│   ├── README.md              # CDCT workflow, pact file locations, update checklist
│   └── schemas/               # canonical JSON Schema definitions (DRY source of truth)
│       ├── envelope.json      # MessageEnvelope — all standard fields
│       ├── dlq_envelope.json  # DLQEnvelope — extends envelope, adds failure fields
│       └── payloads/
│           ├── puuid_payload.json
│           ├── match_id_payload.json
│           ├── parse_payload.json
│           └── analyze_payload.json
├── src/
│   └── lol_pipeline/
│       ├── __init__.py
│       ├── config.py          # env var loading + validation (pydantic-settings)
│       ├── log.py             # structured JSON formatter (not logging.py — avoids stdlib conflict)
│       ├── redis_client.py    # async connection pool singleton
│       ├── models.py          # MessageEnvelope, DLQEnvelope, payload dataclasses
│       ├── streams.py         # publish / consume / ack / nack_to_dlq / requeue_delayed
│       ├── rate_limiter.py    # Lua sliding window, acquire_token()
│       ├── raw_store.py       # RawStore protocol + Redis/S3 implementations
│       └── riot_api.py        # async Riot HTTP client
└── tests/
    └── ...                    # unit tests for shared infrastructure
```

The `contracts/schemas/` directory is the **single source of truth** for all message shapes.
When a schema changes, update here first, then propagate to affected `pacts/` files in
consumer repos and provider verification tests in provider repos.

---

## Service Repository Layout

Each service follows the same structure:

```
lol-pipeline-{service}/
├── pyproject.toml             # depends on lol-pipeline-common
├── Dockerfile
├── src/
│   └── lol_{service}/
│       ├── __init__.py
│       └── main.py            # service entry point
├── pacts/
│   └── {consumer}-{provider}.json   # Pact v3 message pact (consumer-owned)
└── tests/
    ├── contract/
    │   ├── test_consumer.py   # generates/verifies the pact file
    │   └── test_provider.py   # verifies this service satisfies its consumers' pacts
    └── ...                    # unit + integration tests
```

**Pact file ownership:** Each `pacts/` file is owned by the **consumer** service repo.
Provider verification tests live in the **provider** repo and load pact files from the
consumer's sibling directory (`../lol-pipeline-{consumer}/pacts/`) in local dev, or from
a Pact Broker in CI.

See `lol-pipeline-common/contracts/README.md` for the full CDCT workflow.

**`pyproject.toml` dependency declaration:**
```toml
[project]
dependencies = [
    "lol-pipeline-common>=1.0.0,<2.0.0",
]
```

---

## Versioning

`lol-pipeline-common` uses semantic versioning.

| Change type                        | Version bump |
|------------------------------------|--------------|
| New field in existing model        | patch        |
| New module or utility function     | minor        |
| Breaking interface change          | major        |

Service repos pin to `>=X.Y.0,<X+1.0.0` (minor-compatible).
When a major version is released, services update their pins explicitly.

---

## Local Development Workflow

Repository layout:
```
repo-root/
├── .env.example                # all env vars
├── .env                        # local config (git-ignored)
├── docker-compose.yml          # all services
├── docker-compose.prod.yml     # prod overrides
├── Justfile                    # developer commands
├── lol-pipeline-common/
├── lol-pipeline-crawler/
├── lol-pipeline-fetcher/
├── lol-pipeline-parser/
├── lol-pipeline-analyzer/
├── lol-pipeline-recovery/
├── lol-pipeline-delay-scheduler/
├── lol-pipeline-seed/
├── lol-pipeline-admin/
├── lol-pipeline-discovery/     ← idle fan-out; promotes discovered players
├── lol-pipeline-ui/
├── scripts/                    # update_mocks.py, fixtures
├── tests/                      # e2e tests
└── docs/                       # architecture, phases, standards
```

**Setup for a service (e.g. crawler):**
```bash
cd lol-pipeline-crawler
python -m venv .venv && source .venv/bin/activate

# Install common library in editable mode from sibling directory
pip install -e ../lol-pipeline-common

# Install service itself in editable mode
pip install -e ".[dev]"
```

Changes to `lol-pipeline-common` are immediately visible without reinstalling.

**Running all services locally (from the repo root):**
```bash
just setup          # copies .env.example → .env
just build          # builds all container images
just run            # start Redis + all service containers (podman compose up -d)
just seed "Faker#KR1"
just ui             # open web UI at http://localhost:8080
```

Podman is the default runtime. Use Docker via `RUNTIME=docker just <cmd>`.

---

## Docker Build Strategy

### Development

`docker-compose.yml` mounts `./lol-pipeline-common` as a volume and installs it in
editable mode at container startup. No rebuild needed when common lib changes:

```yaml
volumes:
  - ./lol-pipeline-common:/common
command: sh -c "pip install -q -e /common && python -m lol_crawler"
```

### CI / Production

Each service's CI pipeline builds its image with a pinned common lib version:

```bash
docker build \
  --build-arg COMMON_VERSION=1.2.3 \
  -t lol-pipeline/crawler:${GIT_SHA} \
  .
```

The Dockerfile installs the common lib from the git tag:
```dockerfile
ARG COMMON_VERSION=main
RUN pip install --no-cache-dir \
  "lol-pipeline-common @ git+https://github.com/your-org/lol-pipeline-common.git@${COMMON_VERSION}"
```

### Triggering Service Rebuilds

When `lol-pipeline-common` releases a new version (git tag), a webhook triggers CI pipelines
for all dependent service repos. Services test against the new version and rebuild if tests
pass. Services may choose to stay on the previous minor version if the change is not needed.

---

## Infrastructure Files

Infrastructure files live at the repo root:

| File                       | Purpose                                              |
|----------------------------|------------------------------------------------------|
| `docker-compose.yml`       | All services (dev mode with volume mounts)           |
| `docker-compose.prod.yml`  | Prod overrides (no volume mounts, registry images)   |
| `.env.example`             | All env vars with defaults and comments              |
| `Justfile`                 | Developer commands (`just seed`, `just logs`, etc.)  |
| `docs/`                    | Architecture, phases, standards documentation        |
