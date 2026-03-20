# CLAUDE.md — Project Instructions

## Project

LoL Match Intelligence Pipeline — monorepo, Redis Streams, Python 3.14, Podman Compose (default) / Docker Compose.
See `ARCHITECTURE.md` for doc index. See `docs/standards/01-coding-standards.md` for lint/type config.
Platform: macOS. Container runtime: Podman (default). Switch with `RUNTIME=docker just <cmd>`.

## Directives

- **TDD (Red → Green → Refactor)**: Write failing test first. Never skip. Never change contracts to match broken output. Ask if ambiguous.
- **12-factor app** methodology
- **DRY** — Don't Repeat Yourself
- **Service isolation**: Services know only their own input/output contracts. No cross-service imports.
- **PACT contracts**: Schemas in `lol-pipeline-common/contracts/schemas/` are the DRY source. When modifying a service: (1) update schemas if shape changes, (2) update consumer pacts, (3) update provider contract tests. Contract tests must pass before merge.
- **Before compound tasks**: Update CLAUDE.md with a TODO list; remove when done.
- **Replies**: Direct, fewest words.

## Gotchas

- `Redis` is NOT generic in redis-py 7.x — use `Redis` unparameterized
- Async Redis files use `from __future__ import annotations`
- `hmget(key, ["field1", "field2"])` — list form required (variadic removed in redis-py 7.x)
- `seed`/`admin` use `entrypoint` (not `command`) for arg passthrough
- All complexity/lint thresholds configured in each service's `pyproject.toml` (see `docs/standards/`)

## Key Locations

| Path | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Doc index |
| `docs/standards/01-coding-standards.md` | Lint, type, complexity config |
| `docs/standards/03-testing-standards.md` | Test speed limits, timeout config, parallelism, agent batch strategy |
| `lol-pipeline-common/contracts/schemas/` | Canonical Pact v3 schemas |
| `lol-pipeline-*/pacts/` | Per-service consumer contracts |
| `tests/integration/` | 7 integration tests (IT-01 through IT-07, testcontainers) |

## Secrets

- GitHub token: `GITHUB_TOKEN` in `.env` — use for pushes and GitHub API (releases, CI checks)

## Constraints

- Do not modify failing tests without user confirmation

## TODO — Review Cycle 2

### Critical
- [x] R1: Priority counter drift on TTL expiry — `system:priority_count` never decremented when `player:priority:{puuid}` key expires, permanently blocks discovery
- [x] R2: Priority counter can go negative — `_DEL_DECR_LUA` needs floor at 0
- [x] R3: `asyncio.get_event_loop()` deprecated → replace with `asyncio.get_running_loop()` in service.py, discovery, delay-scheduler, recovery (4 files)

### Code Quality
- [x] R4: Analyzer `ack()` outside try/finally — move inside try block to prevent unACKed messages on clear_priority() failure
- [ ] R5: Crawler only clears priority when `published == 0` — if matches stall in pipeline, priority never cleared

### DevOps
- [x] R6: Justfile `localhost/` prefix only works with Podman — `RUNTIME=docker just seed/admin` broken
- [ ] R7: Dockerfiles use `python:3.12-slim` but CI uses 3.14 — runtime/CI parity gap
- [x] R8: UI Dockerfile HEALTHCHECK missing `--start-period` — exhausts retries before server starts
- [x] R9: `{{PROJECT}}_redis_1` container name breaks under Docker Compose v2 (uses `-`, not `_`)
- [x] R10: Add `COMPOSE_PROJECT_NAME` to .env.example (breaking for users who rename project)

### UI / QA
- [x] R11: Region dropdown missing 7 of 14 regions (`la1, la2, tr1, ru, ph2, sg2, th2, tw2, vn2`)
- [x] R12: Region dropdown HTML: missing space before `selected` attribute
- [x] R13: No Redis error handling in UI routes — ConnectionError → 500 stack trace

### Docs
- [x] R14: Multiple doc files: `cd Scraper` → `cd LoL-Crawler` (local-dev.md, deployment.md)
- [x] R15: ARCHITECTURE.md service count "7" → "10"; add Phase 08 to phases table
- [x] R16: storage.md: `just down -v` → `just reset`; player:name: TTL "none" → "86400s"
- [x] R17: security.md, monitoring.md, troubleshooting.md: add Podman note alongside Docker commands

### Tests
- [x] R18: ~17 missing unit tests (XAUTOCLAIM corrupt msgs, recovery DLQ, admin helpers, crawler priority, delay-scheduler OSError)
