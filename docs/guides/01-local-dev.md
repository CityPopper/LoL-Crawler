# Local Development Guide

## Prerequisites

| Tool | Version | Install |
|------|---------|---------|
| Python | 3.14+ | https://python.org or `pyenv install 3.14` |
| Podman | 4.x+ | https://podman.io/getting-started/installation (default runtime) |
| Podman Compose | 1.x+ | `pip install podman-compose` or via Homebrew |
| just | 1.x+ | https://github.com/casey/just#installation |
| Redis CLI | 7.x+ (optional) | `brew install redis` or via container |

> To use Docker instead of Podman: set `RUNTIME=docker` in your environment or prefix any `just` command with `RUNTIME=docker just <cmd>`.

---

## Initial Setup

```bash
# Clone and enter the repo
git clone <repo-url> && cd LoL-Crawler

# Create .env from template
just setup

# Edit .env — set your Riot API key (required)
# Get a key at https://developer.riotgames.com
# Dev keys expire every 24 hours

# Build all Docker images
just build

# Start everything
just run

# Seed a player to verify the pipeline works
just seed "Faker#KR1" kr

# Open the Web UI
just ui
# http://localhost:8080
```

---

## Per-Service Venv Workflow

Each service has its own virtualenv. This is the workflow for developing a single service.

### Create a Venv

```bash
cd lol-pipeline-crawler
python3 -m venv .venv
source .venv/bin/activate

# Install the common library in editable mode (changes are live)
pip install -e ../lol-pipeline-common

# Install the service itself in editable mode with dev dependencies
pip install -e ".[dev]"
```

### Activate an Existing Venv

```bash
cd lol-pipeline-crawler
source .venv/bin/activate
```

### Why Per-Service Venvs?

- Service isolation: each service declares its own dependencies in `pyproject.toml`
- Matches the Docker container model where each container installs its own dependencies
- Prevents hidden cross-service imports (a service should only depend on `lol-pipeline-common`)
- The common library is installed in editable mode, so changes to `lol-pipeline-common/src/` are immediately visible in all venvs without reinstalling

### Root-Level Venv (for cross-service tasks)

For running `just test`, `just lint`, or integration tests, you can create a root-level venv:

```bash
cd /path/to/LoL-Crawler
python3 -m venv .venv
source .venv/bin/activate

# Install everything
pip install -e lol-pipeline-common
for svc in lol-pipeline-*/; do
  [ -f "$svc/pyproject.toml" ] && pip install -e "$svc[dev]"
done
```

---

## Running the Full Pipeline

### Start All Services

```bash
just run
# Starts Redis + all long-running services

# Equivalent (Podman):
podman compose up -d
# or Docker: RUNTIME=docker just run
```

### Seed a Player

```bash
just seed "Faker#KR1" kr
# Auto-starts the stack if not running
# Region defaults to na1 if omitted

just seed "Hide on bush#KR1" kr
```

### Watch the Pipeline Process

```bash
# In terminal 1: watch stream depths
watch -n 2 'just streams'

# In terminal 2: follow fetcher logs
just logs fetcher

# In terminal 3: follow parser logs
just logs parser

# Or use the Web UI:
# http://localhost:8080/streams — stream depths
# http://localhost:8080/logs — merged logs with auto-refresh
```

### Stop / Resume

```bash
just stop    # pause containers (data preserved)
just run     # resume

just down    # remove containers (data preserved)
just run     # rebuild and resume

just reset   # remove containers AND wipe Redis data
```

---

## Running Individual Services

### Outside Docker (Native Python)

Useful for debugging with breakpoints or running under a profiler.

```bash
# Terminal 1: ensure Redis is running
docker compose up -d redis

# Terminal 2: run a service natively
cd lol-pipeline-crawler
source .venv/bin/activate

# Set env vars (or source .env from the repo root)
export RIOT_API_KEY=RGAPI-your-key
export REDIS_URL=redis://localhost:6379/0

python -m lol_crawler
```

### In a Container (Single Service)

```bash
# Restart just one service (picks up code changes via volume mount)
just restart crawler

# Or start a specific service
podman compose up -d crawler
```

---

## Testing Workflow

### Test Hierarchy

| Level | Command | Requires | Speed |
|-------|---------|----------|-------|
| Unit | `just test` | Nothing (fakeredis, respx) | Fast (~10s) |
| Contract | `just contract` | Nothing (pure serialization) | Fast (~5s) |
| Integration | `just integration` | Docker (testcontainers) | Medium (~30s) |
| End-to-end | `just e2e` | Running stack + valid API key | Slow (~2 min) |

### Run All Unit Tests

```bash
just test
# Runs unit tests for all services in parallel
# Output grouped by service
```

### Run Tests for a Single Service

```bash
cd lol-pipeline-crawler
source .venv/bin/activate
python -m pytest tests/unit -v
```

### Run a Single Test

```bash
cd lol-pipeline-crawler
source .venv/bin/activate
python -m pytest tests/unit/test_main.py::test_crawler__zero_matches__updates_last_crawled -v
```

### Run Contract Tests

```bash
just contract
# Runs tests/contract/ for every service that has them
```

### Run Integration Tests

```bash
just integration
# Uses testcontainers — starts a real Redis container automatically
# No need for the full stack to be running
# Requires Podman or Docker; use RUNTIME=docker if needed
```

### Coverage

```bash
cd lol-pipeline-common
source .venv/bin/activate
python -m pytest tests/unit --cov=lol_pipeline --cov-report=term-missing
```

**Coverage targets:**
- `lol-pipeline-common`: >= 90%
- Each service: >= 80%

### Run All Tests + Lint

```bash
just test-all   # unit + contract
just check      # lint + typecheck
```

> Integration tests require Podman or Docker for testcontainers. Set `RUNTIME=docker` if using Docker.

---

## Linting & Typechecking

### Lint All Services

```bash
just lint
# Runs: ruff check . && ruff format --check .
# For each service directory
```

### Lint a Single Service

```bash
cd lol-pipeline-crawler
ruff check .             # lint check
ruff format --check .    # format check (non-destructive)
ruff format .            # apply formatting
```

### Typecheck All Services

```bash
just typecheck
# Runs: mypy src/
# For each service directory
# MYPYPATH is set to include lol-pipeline-common/src
```

### Typecheck a Single Service

```bash
cd lol-pipeline-crawler
source .venv/bin/activate
MYPYPATH="../lol-pipeline-common/src" mypy src/
```

### Combined Check

```bash
just check
# lint + typecheck in one command
```

### Tool Configuration

All services share identical ruff and mypy config in their `pyproject.toml`. The canonical configuration is documented in `docs/standards/01-coding-standards.md`.

Key settings:
- `ruff`: Python 3.14 target, line length 100, security rules (S), complexity limits (C901, PLR)
- `mypy`: strict mode, all functions annotated, no implicit Any
- Tests are exempt from `S101` (assert), `ANN` (annotations), and `SIM` (simplification)

---

## Writing New Tests (TDD)

### The TDD Cycle

1. **Red**: Write a failing test that describes the desired behavior
2. **Green**: Write the minimum code to make it pass
3. **Refactor**: Clean up; tests must stay green
4. **Never modify a failing test without explicit confirmation**

### Test Naming Convention

```python
def test_{component}__{scenario}__[outcome]():
    ...

# Examples:
def test_seed__within_cooldown__skips_publish():
def test_rate_limiter__short_window_full__denies_and_returns_sleep_time():
def test_parser__missing_game_start__routes_to_dlq():
```

### Common Test Fixtures

Most services use these shared fixtures defined in `tests/conftest.py`:

```python
import fakeredis.aioredis
import pytest

@pytest.fixture
def redis():
    """In-memory Redis for unit tests."""
    return fakeredis.aioredis.FakeRedis()

@pytest.fixture
def settings(monkeypatch):
    """Override env vars for tests."""
    monkeypatch.setenv("RIOT_API_KEY", "test-key")
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    # ... other required vars
```

### Mocking the Riot API

Use `respx` to mock HTTP calls:

```python
import respx
from httpx import Response

@respx.mock
async def test_fetcher__successful_fetch__stores_and_publishes():
    respx.get("https://americas.api.riotgames.com/lol/match/v5/matches/NA1_123").mock(
        return_value=Response(200, json={"metadata": {...}, "info": {...}})
    )
    # ... test logic
```

### Freezing Time

Use `freezegun` for time-dependent tests:

```python
from freezegun import freeze_time

@freeze_time("2025-01-15T12:00:00Z")
async def test_seed__cooldown_boundary__proceeds():
    # seeded_at is exactly SEED_COOLDOWN_MINUTES ago
    ...
```

### Async Tests

All service tests are async. `pytest-asyncio` is configured with `asyncio_mode = auto`:

```python
# No @pytest.mark.asyncio decorator needed
async def test_crawler__successful_crawl__publishes_matches():
    ...
```

---

## Adding a New Service

### Checklist

1. **Create directory structure:**

   ```
   lol-pipeline-{service}/
   ├── pyproject.toml
   ├── Dockerfile
   ├── src/
   │   └── lol_{service}/
   │       ├── __init__.py
   │       ├── __main__.py
   │       └── main.py
   ├── pacts/                    # if consuming a stream
   │   └── {consumer}-{provider}.json
   └── tests/
       ├── __init__.py
       ├── conftest.py
       ├── unit/
       │   ├── __init__.py
       │   └── test_main.py
       └── contract/             # if consuming/producing streams
           ├── test_consumer.py
           └── test_provider.py
   ```

2. **Copy `pyproject.toml`** from an existing similar service. Update `[project]` fields (name, dependencies). Keep tool config (ruff, mypy, pytest) identical.

3. **Copy `Dockerfile`** from a similar service (stream consumer vs CLI tool).

4. **Add to `docker-compose.yml`:**

   ```yaml
   new-service:
     <<: *service-defaults
     build:
       context: .
       dockerfile: lol-pipeline-new-service/Dockerfile
       args:
         COMMON_VERSION: local
     volumes:
       - ./lol-pipeline-common:/common
       - ./lol-pipeline-new-service:/svc
       - ./logs:/logs
     command: ["python", "-m", "lol_new_service"]
   ```

5. **If consuming a stream:** Create `pacts/` with consumer pact JSON matching the schemas in `lol-pipeline-common/contracts/schemas/`.

6. **Write unit tests first (TDD):** Tests before implementation.

7. **If consuming/producing streams:** Write contract tests (consumer and provider).

8. **Lint and typecheck pass:** `ruff check . && ruff format --check . && mypy src/`

---

## Common Tasks

### Modify a Message Schema

1. Update the canonical schema in `lol-pipeline-common/contracts/schemas/`
2. Update `models.py` in `lol-pipeline-common` if the Python model changes
3. Update affected consumer pact files in `lol-pipeline-{consumer}/pacts/`
4. Update consumer tests to verify the new shape
5. Update provider verification tests to produce the new shape
6. Run `just contract` to verify all contract tests pass
7. Run `just test` to verify unit tests pass

### Add a New Redis Key

1. Document the key pattern, value type, and TTL in comments where it is first used
2. If the key is used across services, add it to the relevant service documentation
3. Ensure the key is constructed from validated data (not raw user input)
4. Add test coverage for the new key's lifecycle (create, read, update, delete)
5. If the key needs backup/restore consideration, document it

### Add a New Environment Variable

1. Add to `lol-pipeline-common/src/lol_pipeline/config.py`:

   ```python
   class Config(BaseSettings):
       new_variable: int = 42  # default value
   ```

2. Add to `.env.example` with a comment explaining the variable:

   ```env
   # Description of what this variable controls.
   NEW_VARIABLE=42
   ```

3. Add to `docs/architecture/01-overview.md` env var table
4. Add to `docs/operations/01-deployment.md` env var reference table
5. Write a unit test in `lol-pipeline-common/tests/unit/test_config.py` verifying the default and type coercion

### Update Test Fixtures

```bash
# Refresh fixtures from live Riot API (uses Pwnerer#1337 account)
just update-mocks

# This runs scripts/update_mocks.py which:
# 1. Fetches real API responses
# 2. Anonymizes PUUIDs and player names
# 3. Writes to tests/fixtures/ directories
```

### Consolidate Match Data

```bash
# Bundle individual match JSON files into compressed JSONL archives
just consolidate
# Runs scripts/consolidate_match_data.py --delete-originals
```

---

## IDE Setup Tips

### VSCode

Recommended `settings.json` for the workspace:

```json
{
  "python.defaultInterpreterPath": "${workspaceFolder}/lol-pipeline-common/.venv/bin/python",
  "python.analysis.extraPaths": [
    "${workspaceFolder}/lol-pipeline-common/src"
  ],
  "editor.formatOnSave": true,
  "[python]": {
    "editor.defaultFormatter": "charliermarsh.ruff"
  },
  "ruff.lint.args": ["--config", "pyproject.toml"],
  "mypy.targets": ["src/"]
}
```

### PyCharm

1. Mark `lol-pipeline-common/src` as a "Sources Root"
2. Set the Python interpreter to the service's `.venv`
3. Configure ruff as the external formatter
4. Add `MYPYPATH` to the mypy run configuration
