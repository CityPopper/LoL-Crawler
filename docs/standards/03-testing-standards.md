# Testing Standards

## Goals

| Goal | Rationale |
|------|-----------|
| **Fast feedback loop** | Slow tests kill developer flow. A suite that takes minutes discourages running tests before commits. |
| **Parallelism by default** | Independent tests should never block on each other. `pytest-xdist` runs tests across CPU cores. |
| **Hard per-test timeout** | Unpatched I/O or `sleep()` calls can cause runaway tests. A hard ceiling catches these immediately. |

---

## Per-Test Time Limit: ≤ 10s

Every unit test must complete within **10 seconds**. This is enforced via `pytest-timeout`.

### Configuration (canonical template — all services must match)

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
timeout = 10           # hard kill any test exceeding 10s
addopts = "-n auto"    # pytest-xdist: use all available CPU cores

[project.optional-dependencies]
dev = [
    "pytest",
    "pytest-asyncio",
    "pytest-timeout",   # per-test timeout enforcement
    "pytest-xdist",     # parallel test execution
    # ... other deps
]
```

### Suite-level capacity planning

With `timeout = 10` per test, the worst-case suite time is:

```
max_suite_time = num_tests × 10s
```

Examples:
- 100 tests → ≤ 1000s
- 200 tests → ≤ 2000s
- 50 tests  → ≤ 500s

With `-n auto` (xdist), actual time ≈ `max_suite_time / num_cores`. On a 4-core machine, a 100-test suite should finish in ~250s.

---

## Common Causes of Slow Tests (and Fixes)

| Cause | Symptom | Fix |
|-------|---------|-----|
| Unpatched `asyncio.sleep()` | Test takes exactly N seconds | `@patch("asyncio.sleep", new_callable=AsyncMock)` |
| Unpatched `time.sleep()` | Test takes exactly N seconds | `@patch("time.sleep")` |
| Unpatched HTTP I/O | Flaky / slow depending on network | Mock `httpx.AsyncClient` or use `respx` |
| Unpatched Redis I/O | Slow or requires live Redis | Use `fakeredis` or `AsyncMock` |
| Large fixture data | Slow fixture setup | Trim to minimum fields needed for the test |
| Module-level import side-effects | Slow collection phase | Lazy-import or mock at module level |

---

## Parallelism: When to Use xdist

`pytest-xdist` is installed in all services (`pytest-xdist` in dev deps) but **`-n auto` is NOT enabled by default** for unit tests.

**Why**: With highly-mocked, fakeredis-based tests that run in <0.1s each, xdist worker spawn overhead (~1.6s per service) dominates. Measured result:
- Sequential (3.78s total across 11 services) vs xdist (30.68s) — xdist is 8× **slower**

**When to enable xdist** (`addopts = "-n auto"` in `pyproject.toml`):
- Test suite total exceeds ~30s sequentially
- Tests hit real I/O (integration tests, not unit tests)
- CPU-bound tests (property-based fuzzing with Hypothesis)

**Rule of thumb**: if the suite runs in <10s without xdist, leave it off. If it exceeds 30s, enable xdist and benchmark before committing.

---

## Optimizing Tests: Agent Batch Strategy

When adding `pytest-timeout` causes existing tests to fail (they exceed the 10s limit), fix them in parallel:

### Step 1 — Profile

```bash
cd lol-pipeline-<service> && python3 -m pytest tests/unit --durations=20 -q
```

Collect the `--durations` output for all services. Note any test ≥ 10s.

### Step 2 — Batch and parallelize

Group slow tests into batches of ~50-75 tests per agent. Spawn one developer agent per batch simultaneously. Each agent:
1. Identifies the slow test
2. Patches the underlying slow call (sleep, I/O)
3. Verifies the test still passes and now runs in < 10s

**Example batching by service:**

| Agent | Services | Approx tests |
|-------|----------|--------------|
| A | `common` | ~180 |
| B | `ui` | ~150 |
| C | `crawler`, `fetcher`, `parser` | ~90 |
| D | `analyzer`, `recovery`, `delay-scheduler` | ~90 |
| E | `seed`, `admin`, `discovery` | ~60 |

Run all 5 agents in a single message (parallel). Never run them sequentially.

---

## Running Locally

```bash
# Single service (from service directory):
python3 -m pytest tests/unit -v --timeout=10

# All services (from repo root):
just test

# Profile slow tests:
python3 -m pytest tests/unit --durations=20 -q
```

---

## CI Enforcement

`pytest-timeout` is active in CI (same `pyproject.toml` config). Any test that exceeds 10s will:
1. Be killed by the timeout
2. Report as `FAILED` with `Timeout`
3. Block the CI run

This means **adding real sleeps or unpatched I/O in tests will break CI**, not just make it slow.
