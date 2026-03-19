# LoL Match Intelligence Pipeline — Architecture Index

Detailed documentation lives in `docs/`. Start here for orientation, then follow links.

---

## Architecture Docs

| Doc | Contents |
|-----|----------|
| [01 — Overview & 12-Factor](docs/architecture/01-overview.md) | Technology stack, service summary, 12-factor table, env vars |
| [02 — Service Contracts](docs/architecture/02-services.md) | Input/output contracts for all 7 services |
| [03 — Streams & Messaging](docs/architecture/03-streams.md) | Stream registry, message envelope, DLQ envelope, delayed message pattern |
| [04 — Storage](docs/architecture/04-storage.md) | Redis key schema, RawStore abstraction, match status lifecycle |
| [05 — Rate Limiting](docs/architecture/05-rate-limiting.md) | Dual-window Lua script, acquire_token(), backoff |
| [06 — Failure & Resilience](docs/architecture/06-failure-resilience.md) | system:halted, failure modes, DLQ lifecycle, incident recovery |
| [07 — Containers](docs/architecture/07-containers.md) | Docker image design, docker-compose, scaling |
| [08 — Repo Structure](docs/architecture/08-repo-structure.md) | Monorepo layout, shared library, versioning, local dev workflow |
| [09 — Design Comparison](docs/architecture/09-design-comparison.md) | Contrast with simple scripts, AWS pipeline, Scrapy-Redis, Kafka, CQRS, BFS crawl |

## Testing

| Doc | Contents |
|-----|----------|
| [Testing Plan](docs/testing/01-testing-plan.md) | Red/green TDD philosophy, unit/integration/edge case/contract tests |
| [Contract Schemas](lol-pipeline-common/contracts/README.md) | CDCT workflow, Pact v3 message pacts, schema locations |

## Standards

| Doc | Contents |
|-----|----------|
| [Coding Standards](docs/standards/01-coding-standards.md) | Linting (ruff), type checking (mypy), complexity limits, security rules, naming |
| [Service Layout](docs/standards/02-service-layout.md) | Standard directory structure, deviations, new service checklist |

## Security

| Doc | Contents |
|-----|----------|
| [Security Posture](docs/security/01-security.md) | Threat model, secret management, Redis/Docker hardening, input validation, incident response |

## Operations

| Doc | Contents |
|-----|----------|
| [Deployment & Operations](docs/operations/01-deployment.md) | Prerequisites, Docker Compose, scaling, env vars, incident response, backup/recovery |
| [Monitoring & Observability](docs/operations/02-monitoring.md) | Metrics, health checks, log analysis, alerting, capacity planning, dashboard design |

## Guides

| Doc | Contents |
|-----|----------|
| [Local Development](docs/guides/01-local-dev.md) | Setup, venv workflow, testing, linting, TDD, adding services, IDE tips |
| [Troubleshooting](docs/guides/02-troubleshooting.md) | Diagnostic commands, stream/DLQ/lock debugging, LCU issues, nuclear options |

## Implementation Phases

See [docs/phases/README.md](docs/phases/README.md) for the phased delivery plan with quantifiable acceptance criteria.

| Phase | Name |
|-------|------|
| [00](docs/phases/00-mvp-scope.md) | MVP Scope & Success Criteria |
| [01](docs/phases/01-foundation.md) | Foundation — repos, CI, Docker |
| [02a](docs/phases/02a-shared-foundation.md) | Shared Foundation — config, log, redis_client, models |
| [02b](docs/phases/02b-shared-protocols.md) | Shared Protocols — streams, rate_limiter, raw_store, riot_api |
| [03](docs/phases/03-ingestion.md) | Ingestion Pipeline — Seed, Crawler, Fetcher |
| [04](docs/phases/04-processing.md) | Processing Pipeline — Parser, Analyzer |
| [05](docs/phases/05-resilience.md) | Resilience Layer — Recovery, Delay Scheduler |
| [06](docs/phases/06-operations.md) | Operations — Admin CLI, Justfile, integration tests |
| [07](docs/phases/07-next-phase.md) | IRONCLAD — Security hardening, code quality, weighted queue |

---

## Data Flow (Summary)

```
CLI Input
    │
    ▼
Seed ──stream:puuid──► Crawler ──stream:match_id──► Fetcher ──stream:parse──► Parser ──stream:analyze──► Analyzer
                                                       │                         │
                                                  RawStore                 Redis (match/
                                                 (raw blob)                participant/
                                                                           player data)
Any service failure
    │
    ▼
stream:dlq ──► Recovery ──► delayed:messages ──► Delay Scheduler ──► source stream (retry)
                        └──► stream:dlq:archive  (exhausted)
```
