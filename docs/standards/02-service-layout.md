# Service Layout Standard

## Standard Directory Structure

Every service follows this layout:

```
lol-pipeline-{service}/
├── pyproject.toml             # Package config, ruff, mypy, pytest settings
├── Dockerfile                 # Multi-stage build (builder + runtime)
├── src/
│   └── lol_{service}/
│       ├── __init__.py
│       ├── __main__.py        # python -m lol_{service} entry point
│       └── main.py            # Service logic
├── pacts/
│   └── {consumer}-{provider}.json   # Pact v3 message pact (consumer-owned)
├── tests/
│   ├── __init__.py
│   ├── unit/
│   │   ├── __init__.py
│   │   └── test_main.py
│   └── contract/
│       ├── test_consumer.py
│       └── test_provider.py
└── wheels/                    # Pre-built common wheel (CI builds only)
```

## pyproject.toml

All services share identical tool config (ruff, mypy). Copy from any existing service
and update only `[project]` fields (name, dependencies).

> **Source of truth:** `docs/standards/01-coding-standards.md` has the canonical config block.

## Intentional Deviations

| Service | Deviation | Reason |
|---------|-----------|--------|
| `lol-pipeline-common` | No `pacts/`, has `contracts/schemas/` | Library, not a stream consumer. Schemas are the canonical source. |
| `lol-pipeline-admin` | No `pacts/` | CLI tool; reads from Redis directly, doesn't consume streams. |
| `lol-pipeline-seed` | No `pacts/` | CLI tool; produces to `stream:puuid` but doesn't consume. |
| `lol-pipeline-ui` | No `pacts/` | HTTP service; reads Redis directly, doesn't consume streams. |

## Checklist for New Services

1. Create directory with standard layout above
2. Copy `pyproject.toml` from an existing service, update `[project]` fields
3. Copy `Dockerfile` from a similar service (stream consumer vs CLI tool)
4. Add service to `docker-compose.yml` with bind mount volumes (`./lol-pipeline-{svc}:/svc`)
5. If the service consumes a stream: create `pacts/` with consumer pact JSON
6. Write unit tests (TDD: tests first)
7. If consuming/producing streams: write contract tests
8. Add to `Justfile` lint/typecheck loops (automatic — glob matches `lol-pipeline-*/`)
