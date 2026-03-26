# LoL Match Intelligence Pipeline — Architecture Index

Detailed documentation lives in `docs/`. Start here for orientation, then follow links.

---

## Architecture Docs

| Doc | Contents |
|-----|----------|
| [01 — Overview & 12-Factor](docs/architecture/01-overview.md) | Technology stack, service summary, 12-factor table, env vars |
| [02 — Service Contracts](docs/architecture/02-services.md) | Input/output contracts for all services |
| [03 — Streams & Messaging](docs/architecture/03-streams.md) | Stream registry, message envelope, DLQ envelope, delayed message pattern |
| [04 — Storage](docs/architecture/04-storage.md) | Redis key schema, RawStore abstraction, match status lifecycle |
| [05 — Rate Limiting](docs/architecture/05-rate-limiting.md) | Dual-window Lua script, acquire_token(), backoff |
| [06 — Failure & Resilience](docs/architecture/06-failure-resilience.md) | system:halted, failure modes, DLQ lifecycle, incident recovery |
| [07 — Containers](docs/architecture/07-containers.md) | Docker image design, docker-compose, scaling |
| [08 — Repo Structure](docs/architecture/08-repo-structure.md) | Monorepo layout, shared library, versioning, local dev workflow |
| [09 — Design Comparison](docs/architecture/09-design-comparison.md) | Contrast with simple scripts, AWS pipeline, Scrapy-Redis, Kafka, CQRS, BFS crawl |
| [10 — Source Waterfall](docs/architecture/10-source-waterfall.md) | WaterfallCoordinator, BlobStore, SourceRegistry, fetch algorithm, error semantics |

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
| [Troubleshooting](docs/guides/02-troubleshooting.md) | Diagnostic commands, stream/DLQ/lock debugging, nuclear options |

---

## Data Flow (Summary)

```
just admin track "GameName#TagLine"   ←── Admin UI (POST /system/halt, /system/resume, /dlq/*)
    │                                       (port 8081; X-Admin-Secret auth; profile: tools)
    ▼
stream:puuid ──► Crawler ──stream:match_id──► Fetcher ──stream:parse──► Parser ──stream:analyze──► Player Stats
                                              (WaterfallCoordinator: Riot → BlobStore cache → op.gg; see docs/architecture/10-source-waterfall.md)
                                                                                                         └──────────────────────────────────────────────────────────────────► Champion Stats
                                                 │                         │
                                            RawStore                 Redis (match/
                                           (raw blob)                participant/
                                                                      player data)
Any service failure
    │
    ▼
stream:dlq ──► Recovery ──► delayed:messages ──► Delay Scheduler ──► source stream (retry)
                        └──► stream:dlq:archive  (exhausted)

Web UI (port 8080) — read-only: reads Redis, streams, logs; no write calls
```
