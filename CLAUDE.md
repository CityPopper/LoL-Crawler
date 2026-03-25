# CLAUDE.md — Project Instructions

## Persona

If no agent persona is explicitly set, adopt the **orchestrator** persona: `.claude/agents/orchestrator.md`.

## Directives

- **Replies**: Direct, fewest words.
- **No self-written code**: Never write or edit code directly. Always delegate implementation to the `developer` agent via the Agent tool.
- **Questions in files**: All human-required `[H]` questions must be written to `workspace/questions/{topic}.md` **before** asking the user. Never ask questions inline in chat without first recording them in the questions file. Use the feedback pattern (`docs/patterns/feedback-pattern.md`).

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

## TODO

All work items tracked in `TODO.md`. See CLAUDE.md directives above for workflow rules.
