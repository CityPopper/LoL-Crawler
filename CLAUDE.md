# CLAUDE.md — Project Instructions

## Project

LoL Match Intelligence Pipeline — monorepo, Redis Streams, Python 3.14, Podman Compose (default) / Docker Compose.
See `ARCHITECTURE.md` for doc index. See `docs/standards/01-coding-standards.md` for lint/type config.
Platform: macOS. Container runtime: Podman (default). Switch with `RUNTIME=docker just <cmd>`.

## Persona

If no agent persona is explicitly set, adopt the **orchestrator** persona: `.claude/agents/orchestrator.md`.

## Directives

- **Research before implementation**: Agents MUST research current best practices and known pitfalls before any non-trivial change. Do not rely solely on training data. For technical decisions and implementation choices, research must include multiple sources — official docs, recent web search, and Hacker News (`site:news.ycombinator.com`), which surfaces production war stories and post-mortems that official docs omit. Tailor queries to the task domain. If a source returns nothing relevant, proceed — but the research step is not optional.
- **Confidence threshold**: Only propose changes when >=80% confident they improve things. No feedback is fine if nothing substantial is found.
- **Quantifiable improvements only**: Every proposed improvement needs a measurable before/after metric. Lateral moves are rejected. Tests must validate; if tests fail, roll back and add to `REJECTED.md`.
- **Replies**: Direct, fewest words.

## Gotchas

- `.claude/archive/REJECTED.md` lists ideas evaluated and rejected. Agents MUST read it before proposing new ideas to avoid re-proposals.

## Key Locations — When to Read What

| When working on... | Read... |
|-----|---------|
| Stream consumers or producers | `docs/architecture/03-streams.md` |
| Redis keys or data models | `docs/architecture/04-storage.md` |
| Rate limiting | `docs/architecture/05-rate-limiting.md` |
| Failure handling, DLQ, system:halted | `docs/architecture/06-failure-resilience.md` |
| Docker/compose changes | `docs/architecture/07-containers.md` |
| Architecture overview | `ARCHITECTURE.md` |
| Lint, type, complexity config | `docs/standards/01-coding-standards.md` |
| Test speed limits, parallelism | `docs/standards/03-testing-standards.md` |
| Contract schemas (DRY source) | `lol-pipeline-common/contracts/schemas/` |
| Per-service consumer contracts | `lol-pipeline-*/pacts/` |
| Integration tests | `tests/integration/` (IT-01 through IT-12, testcontainers) |
| Rejected ideas (do not re-propose) | `.claude/archive/REJECTED.md` |

## TODO

All work items tracked in `TODO.md`. See CLAUDE.md directives above for workflow rules.
