# CLAUDE.md — Project Instructions

## Project

LoL Match Intelligence Pipeline — monorepo, Redis Streams, Python 3.12, Docker Compose.
See `ARCHITECTURE.md` for doc index. See `docs/standards/01-coding-standards.md` for lint/type config.

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
| `lol-pipeline-common/contracts/schemas/` | Canonical Pact v3 schemas |
| `lol-pipeline-*/pacts/` | Per-service consumer contracts |
| `lol-pipeline-lcu/lcu-data/` | JSONL match history — **precious, do not delete** |
| `tests/integration/` | 7 integration tests (IT-01 through IT-07, testcontainers) |

## Secrets

- GitHub token: `GITHUB_TOKEN` in `.env` — use for pushes and GitHub API (releases, CI checks)

## Constraints

- Do not modify failing tests without user confirmation

## TODO — Orchestrator Cycle 1 (10-agent review)

### Correctness Bugs
- [ ] C1: Analyzer — include cursor SET in same MULTI/EXEC as HINCRBY (crash → double-count)
- [x] C2: Priority — use SET NX in `_SET_INCR_LUA` to prevent double-increment
- [x] C3: service.py — wrap dispatch loop in try/except (RedisError, OSError)
- [ ] C4: Discovery main loop — add Redis error handling (try/except + sleep)
- [ ] C5: Delay Scheduler main loop — add Redis error handling (try/except + sleep)

### Safety / Unbounded Growth
- [ ] S1: Add maxlen to all 6 direct r.xadd() calls bypassing publish()
- [ ] S2: Docker — Redis restart: always
- [ ] S3: Docker — Redis --maxmemory 800mb --maxmemory-policy noeviction
- [ ] S4: Docker — log rotation (json-file driver, max-size 50m, max-file 5)
- [ ] S5: Create .dockerignore

### Security
- [ ] X1: Fix DOM XSS in UI match history JS error handler (innerHTML → textContent)

### Code Quality
- [ ] Q1: Rename _CACHE_TTL_S → CACHE_TTL_S in resolve.py + update importers
- [ ] Q2: Move `import contextlib` to top-level in discovery + delay-scheduler
- [ ] Q3: Fix envelope.json dlq_attempts type: "string" → "integer"
- [ ] Q4: Admin dlq replay — validate args (no id + no --all → error message)

### Performance
- [ ] P1: UI _streams_fragment_html — pipeline 9 Redis calls into 1
- [ ] P2: consume() — cache _ensure_group, skip after first call per (stream, group)

### Tests
- [ ] Run all tests after changes
