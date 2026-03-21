# Contributing

## Prerequisites

- Python 3.14
- [just](https://github.com/casey/just) (`brew install just`)
- Podman (default) or Docker — see `RUNTIME=docker just <cmd>` to switch
- A Riot Games API key from [developer.riotgames.com](https://developer.riotgames.com)

---

## Dev Setup

```bash
# 1. Copy .env and fill in RIOT_API_KEY
just setup

# 2. Build all service images
just build

# 3. Start Redis + all workers
just run
```

`just up` runs all three steps in sequence.

Edit `.env` before running `just build`. The key variables are:

| Variable | Purpose |
|----------|---------|
| `RIOT_API_KEY` | Required. Your Riot API key. |
| `REDIS_URL` | Defaults to `redis://redis:6379/0` (in-compose). |
| `MATCH_DATA_DIR` | Optional disk write-through cache path. |

See `.env.example` for all variables and their defaults.

---

## Running Tests

### Unit tests (all services in parallel)

```bash
just test
```

Each service runs in its own subprocess. Output is shown grouped per service.

### Single service

```bash
just test-svc crawler
just test-svc discovery
```

### Contract tests

```bash
just contract
```

Runs Pact v3 consumer and provider contract tests for all services that have a
`tests/contract/` directory. Contract tests verify that message envelopes conform
to the schemas in `lol-pipeline-common/contracts/schemas/`.

### Integration tests

```bash
just integration
```

Requires Docker or Podman for testcontainers. Tests spin up a real Redis instance.
Each test has a 120 s timeout enforced by `pytest-timeout`. See
[tests/integration/](tests/integration/) for the 7 scenarios (IT-01 through IT-07).

### All unit + contract

```bash
just test-all
```

### Coverage

```bash
just coverage
```

Prints per-file coverage for each service. No minimum is enforced by CI today.

---

## Code Standards

This project enforces standards via automated tooling. All checks must pass before
a PR can merge.

### Linting and formatting — ruff

```bash
# Check all services
just lint

# Auto-fix and format
just fix
```

Or from a single service directory:

```bash
ruff check src/
ruff format --check src/
ruff format src/
```

Key ruff rulesets enabled: `E/W` (pycodestyle), `F` (pyflakes), `I` (isort),
`B` (bugbear), `C90` (McCabe), `UP` (pyupgrade), `N` (pep8-naming), `S` (bandit),
`ANN` (annotations), `SIM` (simplify), `PLR` (pylint refactoring), `RUF` (ruff-native).

Complexity limits (per `pyproject.toml` in each service):

| Limit | Value |
|-------|-------|
| McCabe max-complexity | 10 |
| max-branches | 12 |
| max-statements | 50 |
| max-args | 7 |
| max-returns | 6 |
| Line length | 100 |

See `docs/standards/01-coding-standards.md` for the full reference.

### Type checking — mypy strict

```bash
# Check all services
just typecheck

# Single service (from service directory)
MYPYPATH="$(pwd)/../lol-pipeline-common/src" mypy src/
```

All services use `strict = true`. Type annotations are required on all functions.
Use `dict[str, Any]` for Redis field dicts and `X | None` (not `Optional[X]`) for
optional types.

### TDD discipline

This project follows Red → Green → Refactor:

1. Write a failing test that describes the expected behavior.
2. Write the minimum code to make it pass.
3. Refactor — improve structure without changing behavior.

Do not modify a failing test to match broken output. If a contract or behavior is
ambiguous, ask before writing the test.

### PACT contracts

When modifying a service's input or output message shape:

1. Update the relevant schema in `lol-pipeline-common/contracts/schemas/`.
2. Update the consumer pact in `lol-pipeline-*/pacts/`.
3. Update the provider contract test in `lol-pipeline-*/tests/contract/`.
4. Run `just contract` — all contract tests must pass before submitting a PR.

Schemas are the single source of truth. Services must not import from each other;
they communicate only through stream message envelopes.

---

## PR Process

All of the following must pass before a PR is mergeable:

| Check | Local command | CI job |
|-------|--------------|--------|
| Lint | `just lint` | `Lint & Format` |
| Type check | `just typecheck` | `Type Check` |
| Unit tests | `just test` | `Unit Tests ($service)` |
| Contract tests | `just contract` | `Contract Tests ($service)` |
| Integration tests | `just integration` | `Integration Tests` |
| Security audit | — | `Security Audit` |
| Docker build | `just build` | `Docker Build ($service)` |

The Docker build job in CI is gated on lint, typecheck, unit, and contract — a broken
build cannot produce an image.

### Pre-commit hooks

`just setup` installs pre-commit hooks if `pre-commit` is available:

```bash
pip install pre-commit
just setup
```

Hooks run ruff and mypy on staged files before every commit. The pre-push hook runs
mypy on the full source tree.

---

## Commit Message Style

Follow the style used in this repository's git history:

```
<verb> <area>: <what changed> (<scope if relevant>)
```

Examples from the log:

```
Fix mypy: suppress hmget await overload ambiguity in discovery
Fix all ruff lint + format issues across 27 files
Fix 8 immediate code issues, add 14 tests (546→560)
Fix CI typecheck: explicit mypy install, add mypy to pre-push hook
v1.0.0 — Orchestrator cycles 2+3: 30 bug fixes, full test coverage
v0.9.0 — Hardening, correctness fixes, full test coverage expansion
```

Guidelines:
- Imperative present tense: "Fix", "Add", "Update", "Remove" — not "Fixed" or "Fixes"
- Include test count deltas when tests are added: `(560→574 tests)`
- Reference the bug ID from `CLAUDE.md` when fixing a tracked issue: `(B7)`, `(I2-H9)`
- Keep the subject line under 72 characters

---

## Adding a New Service

Follow the checklist in `docs/standards/02-service-layout.md`. The key steps:

1. Create `lol-pipeline-<name>/` with `src/`, `tests/unit/`, `tests/contract/`, `pacts/`.
2. Add `pyproject.toml` matching the canonical template in `docs/standards/01-coding-standards.md`.
3. Add a `Dockerfile` with `--start-period 60s` on `HEALTHCHECK`.
4. Add the service to `docker-compose.yml`.
5. Add the service to the `test` and `contract` matrix in `.github/workflows/ci.yml`.
6. Add the service to the `docker-build` matrix in `.github/workflows/ci.yml`.
7. Wire input/output via `lol_pipeline.streams.publish` and `lol_pipeline.streams.consume`.
8. Do not import from other service packages — use only `lol-pipeline-common`.

---

## Key Locations

| Path | Purpose |
|------|---------|
| `ARCHITECTURE.md` | Doc index — start here |
| `docs/standards/01-coding-standards.md` | Lint, type, complexity config |
| `docs/standards/02-service-layout.md` | New service checklist |
| `docs/standards/03-testing-standards.md` | Test speed limits, timeout config |
| `lol-pipeline-common/contracts/schemas/` | Pact v3 schemas (source of truth) |
| `lol-pipeline-*/pacts/` | Per-service consumer contracts |
| `tests/integration/` | IT-01 through IT-07 integration tests |
| `.env.example` | All environment variables with defaults and descriptions |
