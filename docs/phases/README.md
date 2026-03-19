# Implementation Phases

Phased delivery plan for the LoL Match Intelligence Pipeline backend MVP.

Each phase has a clear objective, concrete deliverables, and quantifiable acceptance criteria. A phase is not complete until every AC passes.

---

## Phase Index

| Phase | Name | Objective |
|-------|------|-----------|
| [00](00-mvp-scope.md) | MVP Scope | PM definition of done for the full backend |
| [01](01-foundation.md) | Foundation | Repos, CI, Docker, Redis — dev env works |
| [02a](02a-shared-foundation.md) | Shared Foundation | config, log, redis_client, models — stable base |
| [02b](02b-shared-protocols.md) | Shared Protocols | streams, rate_limiter, raw_store, riot_api — pipeline primitives |
| [03](03-ingestion.md) | Ingestion Pipeline | Seed + Crawler + Fetcher — match data into RawStore |
| [04](04-processing.md) | Processing Pipeline | Parser + Analyzer — structured data + player stats |
| [05](05-resilience.md) | Resilience Layer | Recovery + Delay Scheduler — failure handling |
| [06](06-operations.md) | Operations | Admin CLI + Justfile + integration tests — operable system |

## Post-MVP

| Phase | Name | Objective |
|-------|------|-----------|
| [07](07-next-phase.md) | IRONCLAD | Security hardening, code quality, Docker/CI, weighted priority queue |

---

## Gate Criteria

Every phase gate requires:
- All phase ACs passing (automated where possible)
- `pytest` exits 0, zero failures, zero errors
- Coverage targets met (lol-pipeline-common ≥ 90%; each service ≥ 80%)
- Docker image(s) build successfully
- No unresolved `TODO`/`FIXME` in delivered code unless tracked in a phase AC
