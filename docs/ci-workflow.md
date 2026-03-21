# CI Workflow

CI runs on every push to `main` and on every pull request targeting `main`.
The workflow file is `.github/workflows/ci.yml`. All jobs run on `ubuntu-latest`
with Python 3.14.

---

## Job Overview and Dependencies

```
lint ──────────┐
typecheck ─────┤──► docker-build
test ──────────┤
contract ──────┘

test ──────────┐
lint ──────────┴──► integration-tests

security-audit   (runs independently)
```

| Job | Needs | Purpose |
|-----|-------|---------|
| `lint` | — | ruff check + format check for all services |
| `typecheck` | — | mypy strict for all services |
| `test` | — | pytest unit tests, one matrix entry per service |
| `contract` | — | pytest contract tests, one matrix entry per service |
| `integration-tests` | `test`, `lint` | testcontainers integration suite |
| `security-audit` | — | pip-audit CVE scan for all packages |
| `docker-build` | `lint`, `typecheck`, `test`, `contract` | builds each service image |

The `docker-build` job is gated on all four quality gates. A broken lint, type error,
failing unit test, or failing contract test blocks image production.

---

## Job Details

### lint

Iterates every `lol-pipeline-*/` directory that contains a `pyproject.toml` and runs:

```bash
ruff check src/
ruff format --check src/
```

Runs in parallel with `typecheck` and `test`. Failure blocks `docker-build` and
`integration-tests`.

### typecheck

Installs `lol-pipeline-common[dev]` first, then installs each service's dev extras,
then runs:

```bash
MYPYPATH="$PWD/lol-pipeline-common/src" mypy src/
```

for every service directory containing a `pyproject.toml` and a `src/`. The
`MYPYPATH` environment variable makes the shared library resolvable by mypy without
a system install.

`mypy` and `types-requests` are installed explicitly at the top level to ensure the
mypy binary is available regardless of which extras include it.

### test

Matrix job — one parallel runner per service:

```
lol-pipeline-common, lol-pipeline-seed, lol-pipeline-crawler,
lol-pipeline-fetcher, lol-pipeline-parser, lol-pipeline-recovery,
lol-pipeline-delay-scheduler, lol-pipeline-analyzer, lol-pipeline-admin,
lol-pipeline-discovery, lol-pipeline-ui
```

Each runner installs `lol-pipeline-common[dev]` plus the service's own dev extras,
then runs:

```bash
python -m pytest tests/unit -v --tb=short
```

`fail-fast: false` ensures all services are tested even if one fails.

### contract

Same matrix as `test` but covers only services with contract tests:

```
lol-pipeline-common, lol-pipeline-seed, lol-pipeline-crawler,
lol-pipeline-fetcher, lol-pipeline-parser, lol-pipeline-recovery,
lol-pipeline-delay-scheduler, lol-pipeline-analyzer
```

Runs:

```bash
python -m pytest tests/contract -v --tb=short
```

Contract tests verify that message envelope shapes match the Pact v3 schemas in
`lol-pipeline-common/contracts/schemas/`.

### integration-tests

Needs `test` and `lint` to pass first. Installs all service dependencies in one
environment plus `testcontainers`, `pytest-asyncio`, and `pytest-timeout`, then runs:

```bash
python3 -m pytest tests/integration/ -v --timeout=120
```

Each test has a 120 s hard timeout. The integration tests use fakeredis (not
testcontainers for Redis) for speed, but the `testcontainers` package is installed
for future use.

#### What the integration tests cover

| Test | Scenario |
|------|---------|
| IT-01 | Happy path: seed → crawler → fetcher → parser → analyzer |
| IT-02 | Idempotency: re-seed same player, stats remain unchanged |
| IT-03 | 429 end-to-end recovery: fetcher → DLQ → recovery → delay → retry |
| IT-04 | Worker crash and redelivery via XAUTOCLAIM |
| IT-05 | `system:halted` propagation on 403 auth failure |
| IT-06 | Concurrent workers: 2 parsers + 2 analyzers, no data corruption |
| IT-07 | Rate limit enforcement: 3 fetchers never exceed 20 req/s |

### security-audit

Runs independently of the quality gates. Installs `pip-audit` and scans all packages:

```bash
pip install -e "lol-pipeline-common/"
pip-audit --skip-editable

for dir in lol-pipeline-*/; do
  pip install -e "$dir" 2>/dev/null || true
done
pip-audit --skip-editable
```

`--skip-editable` excludes the project's own packages (no published CVE entries)
and limits the scan to third-party dependencies. A CVE finding causes the job to
fail but does not gate `docker-build` or `integration-tests`.

### docker-build

Matrix job — one parallel runner per service (10 services, `lol-pipeline-common`
excluded as it has no Dockerfile):

```
lol-pipeline-admin, lol-pipeline-analyzer, lol-pipeline-crawler,
lol-pipeline-delay-scheduler, lol-pipeline-discovery, lol-pipeline-fetcher,
lol-pipeline-parser, lol-pipeline-recovery, lol-pipeline-seed, lol-pipeline-ui
```

Each runner builds from the repo root context using the service-specific Dockerfile:

```bash
docker build -f $SERVICE/Dockerfile -t $SERVICE:ci .
```

`fail-fast: false` ensures all images are attempted even if one fails.

---

## Running CI Checks Locally

The local equivalents for each CI job:

| CI job | Local command |
|--------|--------------|
| lint | `just lint` |
| typecheck | `just typecheck` |
| test | `just test` |
| contract | `just contract` |
| integration-tests | `just integration` |
| lint + typecheck | `just check` |
| unit + contract | `just test-all` |
| docker-build | `just build` |

For a single service:

```bash
# Lint
cd lol-pipeline-crawler && ruff check src/ && ruff format --check src/

# Typecheck
cd lol-pipeline-crawler && MYPYPATH="../lol-pipeline-common/src" mypy src/

# Unit tests
just test-svc crawler

# Contract tests
cd lol-pipeline-crawler && python3 -m pytest tests/contract -v
```

---

## Fixing Common CI Failures

### Lint failure

```
error: `ruff check` found N errors
```

Run `just fix` from the repo root to auto-fix most issues. For remaining violations,
read the error output — each line shows the rule ID (e.g., `E501`, `S105`, `ANN001`).
Check `docs/standards/01-coding-standards.md` for the meaning of each rule and how
to override if the violation is intentional.

### Type check failure

```
error: Function is missing a return type annotation
error: Argument 1 to "foo" has incompatible type
```

Run `just typecheck` locally. Common causes:

- Missing return type annotation on a new function — add `-> None` or the correct type.
- `hmget` / `eval` return types from redis-py require `# type: ignore[misc]` due to
  overload ambiguity in redis-py 7.x.
- Async files must begin with `from __future__ import annotations` to allow forward
  references in type annotations.
- `Redis` is not generic in redis-py 7.x — use `aioredis.Redis` unparameterized.

### Unit test failure

Run the failing service in isolation to see the full output:

```bash
just test-svc <service>
# or
cd lol-pipeline-<service> && python -m pytest tests/unit -v --tb=long
```

Do not modify a failing test to match broken output. Fix the source code.

### Contract test failure

A contract test failure means an envelope field was added, removed, or renamed
without updating the schema or pact. Steps:

1. Identify which schema is affected in `lol-pipeline-common/contracts/schemas/`.
2. Update the schema to match the new shape.
3. Update the pact in `lol-pipeline-*/pacts/`.
4. Re-run `just contract`.

### Integration test failure

Integration tests have a 120 s timeout. Common causes:

- A service function is hanging waiting for a message that was never published.
  Check that the test publishes to the correct stream with the correct envelope shape.
- An `asyncio.sleep()` in test setup is too slow. Use `fakeredis` and avoid real sleeps.
- A port conflict with a locally running Redis. Integration tests use an in-process
  fakeredis; no external Redis is required.

### Docker build failure

Build failures almost always indicate a missing dependency in the Dockerfile or a
broken `pip install -e` for a service. Check that:

- The `COPY` commands include the `lol-pipeline-common/` directory.
- The service's `pyproject.toml` lists all runtime dependencies (not just dev extras).
- The Dockerfile uses the correct Python base image.

Note: there is a known parity gap (R7 in `CLAUDE.md`) — Dockerfiles use
`python:3.12-slim` while CI uses Python 3.14. This is a tracked open item.

---

## Security

The workflow uses `permissions: read-all` at the top level, granting the minimum
GitHub token permissions required for a checkout-and-test workflow. No secrets are
exposed to forks; `GITHUB_TOKEN` is only used for reading the repository.

The `pip-audit` job scans third-party dependencies for known CVEs on every CI run.
To check locally:

```bash
pip install pip-audit
pip install -e lol-pipeline-common/
pip-audit --skip-editable
```
