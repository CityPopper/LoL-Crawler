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
- All complexity/lint thresholds configured in each service's `pyproject.toml` (see `docs/standards/`)

## Key Locations

| Path | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Doc index |
| `docs/standards/01-coding-standards.md` | Lint, type, complexity config |
| `docs/standards/03-testing-standards.md` | Test speed limits, timeout config, parallelism, agent batch strategy |
| `lol-pipeline-common/contracts/schemas/` | Canonical Pact v3 schemas |
| `lol-pipeline-*/pacts/` | Per-service consumer contracts |
| `tests/integration/` | 12 integration tests (IT-01 through IT-12, testcontainers) |

## TODO — Orchestrator Review Fixes (F1-F8)

- [x] F1: Parser drops priority on outbound analyze envelopes — propagate `envelope.priority`
- [x] F2: Ban/matchup HINCRBY not idempotent on retry — guard with `match:status:parsed` check
- [x] F3: `patch:list` has no TTL — add EXPIRE in `_UPDATE_CHAMPION_LUA` (analyzer + admin)
- [x] F4: Per-region rate limiter 4x bypass — remove per-region key separation
- [x] F5: `seen:matches` TTL resets on every write — set EXPIRE only when no TTL exists
- [x] F6: Redis key injection in champions/matchups routes — add input validation
- [x] F7: 7 config vars missing from `.env.example` — add entries
- [x] F8: Analyzer uses legacy `role` instead of `team_position` — fix `_process_matches`

