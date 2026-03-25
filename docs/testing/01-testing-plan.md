# Testing Plan

## Philosophy

Red/green TDD: write a failing test first, implement minimum code to pass, refactor. Tests are the specification — if a test fails, the implementation is wrong.

## Workflows

| Scenario | Workflow Doc |
|----------|-------------|
| New module / coverage gap | `docs/workflows/tdd-sequential.md` |
| Existing function with 3+ test scenarios | `docs/patterns/parallel-tdd-pattern.md` |

## Test Tiers

| Tier | Purpose | Location | Runner |
|------|---------|----------|--------|
| Unit | Function-level isolation | Colocated: `test_foo.py` next to `foo.py` | `just test-svc <name>` |
| Regression | Bug-fix red/green tests kept forever | `tests/regression/` | `just test-svc <name>` |
| Contract | Consumer-driven pact verification | `tests/contract/` | `just contract` |
| Integration | Full pipeline with real Redis (testcontainers) | `tests/integration/` | `just integration` |
| E2E | Live stack with real API key | `tests/e2e/` | `just e2e` |

## Key Tools

- **pytest** + pytest-asyncio, pytest-timeout (10s limit), pytest-xdist (parallel)
- **fakeredis** for unit tests, **testcontainers** for integration
- **respx** for HTTP mocking, **hypothesis** for property-based fuzz tests
- **pact-python** for consumer-driven contracts

## Running Tests

```bash
just test              # all services in parallel
just test-svc ui       # single service
just integration       # integration tests (needs Docker)
just contract          # contract tests
just dev-ci            # full CI in dev container
```

## Standards

See `docs/standards/03-testing-standards.md` for speed limits, parallelism rules, and naming conventions. Test files are in each service's source tree — see `lol-pipeline-*/src/` and `lol-pipeline-*/tests/`.
