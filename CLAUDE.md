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

## Pending Work

GitHub CI fix: `ruff format` failing on multiple services. Run `ruff format` across all service src/ dirs to fix formatting. Push to branch, verify CI passes, merge to main.

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
