# Phase 01 — Foundation

**Role:** Product Manager
**Objective:** Every repository exists, CI is green, and a developer can run `docker compose up redis` on a fresh clone. No business logic yet — just scaffolding that every subsequent phase builds on.

**Complexity: LOW** — mechanical scaffolding; no business logic.

**Value unlocked:** Parallel development across repos is possible. CI catches regressions from day one.

---

## Dependencies

None. This is the first phase.

---

## Deliverables

1. 10 local directories created, each initialized as a git repository (`git init`)
2. `lol-pipeline-common` has `pyproject.toml`, directory structure, and installable (empty) package
3. Each of 8 service repos has `pyproject.toml`, `Dockerfile`, `src/<package>/`, `tests/unit/`, `tests/integration/`
4. `lol-pipeline-deploy` has `docker-compose.yml`, `docker-compose.prod.yml`, `.env.example`, `Justfile`
5. CI workflow file (`.github/workflows/ci.yml`) present in every non-deploy repo (dormant locally; activates if/when pushed to GitHub)
6. Redis runs locally via Docker Compose with correct persistence settings

---

## Acceptance Criteria

### Repositories

- AC-01: 10 local directories exist with correct names, each initialized as a git repo (`git init`): `lol-pipeline-common`, `lol-pipeline-seed`, `lol-pipeline-crawler`, `lol-pipeline-fetcher`, `lol-pipeline-parser`, `lol-pipeline-analyzer`, `lol-pipeline-recovery`, `lol-pipeline-delay-scheduler`, `lol-pipeline-admin`, `lol-pipeline-deploy`. All sibling directories under the same parent folder.
- AC-02: `lol-pipeline-common` is a valid Python package: `pip install -e path/to/lol-pipeline-common` exits 0; `python -c "import lol_pipeline"` exits 0.
- AC-03: Each service repo's `pip install -e .` exits 0 and `python -c "import <service_package>"` exits 0.
- AC-04: Each service `pyproject.toml` declares `lol-pipeline-common>=1.0.0,<2.0.0` as a dependency.

### Docker and Redis

- AC-05: `docker compose up redis -d` exits 0 within 30 seconds.
- AC-06: `docker compose exec redis redis-cli ping` returns `PONG`.
- AC-07: Redis container is configured with AOF (`appendonly yes`, `appendfsync everysec`) and RDB (`save 900 1`, `save 300 10`, `save 60 10000`); verified by `redis-cli CONFIG GET appendonly` returning `yes`.
- AC-08: `docker compose down -v && docker compose up redis -d`: Redis starts cleanly (no data persistence error).

### CI Pipeline

- AC-09: Each of the 9 non-deploy repos has a `.github/workflows/ci.yml` that runs on push and pull_request to `main`. File is present and syntactically valid; it is not executed locally.
- AC-10: CI workflow steps in order: (1) `pip install -e ../lol-pipeline-common` (local path; substitute with `git+https://...@{tag}` when pushed to remote), (2) `pip install -e ".[dev]"`, (3) `pytest tests/unit --cov`, (4) `pytest tests/integration` (with Redis via testcontainers), (5) `docker build`.
- AC-11: Running `pytest tests/unit` locally in each repo exits 0 with 0 failures on the empty scaffolding.
- AC-12: `docker build` (using local wheel of common lib — see Notes) succeeds for each service image.

### Project Configuration

- AC-13: Each `pyproject.toml` defines `[tool.pytest.ini_options]` with `testpaths = ["tests/unit"]` as the default; integration tests require `pytest tests/integration` explicitly.
- AC-14: `pytest.ini_options` includes `asyncio_mode = "auto"` (pytest-asyncio auto mode).
- AC-15: Integration tests are marked `@pytest.mark.integration`; `pytest tests/unit` does not collect or run them.
- AC-16: Each dev dependency set includes: `pytest`, `pytest-asyncio`, `pytest-cov`, `fakeredis[aioredis]>=2.2.0`, `respx`, `freezegun`, `testcontainers`, `pytest-xdist`, `lupa`.
- AC-17: `.env.example` in `lol-pipeline-deploy` contains all required env vars from `docs/architecture/01-overview.md` with placeholder values.

### Common Library Structure

- AC-18: `lol-pipeline-common/src/lol_pipeline/` contains the following placeholder files (each with a single comment): `__init__.py`, `config.py`, `log.py`, `redis_client.py`, `models.py`, `streams.py`, `rate_limiter.py`, `raw_store.py`, `riot_api.py`.
- AC-19: `lol-pipeline-common/tests/conftest.py` exists; `lol-pipeline-common/tests/unit/` and `lol-pipeline-common/tests/integration/` directories exist.
- AC-20: `lol-pipeline-common/tests/fixtures/` directory exists with placeholder files for: `match_normal.json`, `match_aram.json`, `match_remake.json`, `match_large.json`, `account.json`, `account_unicode.json`.

---

## Notes

- Seed and Admin are one-shot containers. Their Dockerfiles must NOT include a `HEALTHCHECK` directive. Long-running service Dockerfiles include the standard healthcheck from `docs/architecture/07-containers.md`.
- All 10 directories sit as siblings under one parent (e.g., `~/projects/`). The `docker-compose.yml` uses `../lol-pipeline-common` relative paths; this layout is required.
- `lol-pipeline-deploy/docker-compose.yml` uses volume mounts for `lol-pipeline-common` in dev (`../lol-pipeline-common:/common`) and editable install override: `sh -c "pip install -q -e /common && python -m <service>"`. This is the primary way to run services locally and bypasses the Dockerfile's wheel-install step entirely.
- **Building Docker images locally (without a remote):** build a wheel from the common lib first, then pass it into the service build context. The Justfile `build` target handles this: `cd ../lol-pipeline-common && pip wheel . -w ../lol-pipeline-deploy/wheels/` then `docker build --build-arg COMMON_WHEEL=wheels/lol_pipeline_common-*.whl .` from each service directory. The Dockerfile `COPY wheels/ ./wheels/` + `pip install ./wheels/lol_pipeline_common-*.whl` pattern supports this. See `docs/architecture/07-containers.md` for the updated Dockerfile pattern.
