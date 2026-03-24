# CLAUDE.md — Project Instructions

## Project

LoL Match Intelligence Pipeline — monorepo, Redis Streams, Python 3.14, Podman Compose (default) / Docker Compose.
See `ARCHITECTURE.md` for doc index. See `docs/standards/01-coding-standards.md` for lint/type config.
Platform: macOS. Container runtime: Podman (default). Switch with `RUNTIME=docker just <cmd>`.

## STOP — TDD Enforcement (read before any implementation)

**Before writing a single line of implementation code, you MUST:**
1. Write the failing test (`[ ] Red`)
2. Run it and confirm it fails for the right reason
3. Only then write the minimum code to make it pass (`[ ] Green`)
4. Refactor (`[ ] Refactor`)

Skipping or reordering these steps is **never allowed**, regardless of task size, urgency, or apparent obviousness. If you find yourself reading implementation files before a test exists and is confirmed failing — **stop and write the test first**.

---

## Directives

- **TDD (Red → Green → Refactor)**: See STOP block above. Never change contracts to match broken output. Ask if ambiguous. Every `TODO.md` task must have explicit `[ ] Red`, `[ ] Green`, `[ ] Refactor` checklist items.
- **Research before implementation**: Agents MUST research current best practices and known pitfalls before any non-trivial change. Do not rely solely on training data.
- **Doc-agent bookend**: Run the doc-keeper agent SEQUENTIALLY — once BEFORE (verify docs are current) and once AFTER (update docs). Never in parallel with implementation agents.
- **Confidence threshold**: Only propose changes when >=80% confident they improve things. No feedback is fine if nothing substantial is found.
- **Quantifiable improvements only**: Every proposed improvement needs a measurable before/after metric. Lateral moves are rejected. Tests must validate; if tests fail, roll back and add to `REJECTED.md`.
- **Plan-first workflow**: Before executing non-trivial tasks, follow the **Feedback Pattern** (`docs/patterns/01-feedback-pattern.md`): write questions to `questions-{topic}.md`, consult specialist agents in parallel, lock decisions, then write a plan with TDD checklists in `TODO.md`. Multiple unrelated topics run concurrently in separate files. For simple tasks, skip directly to writing the plan.
- **Parallel execution**: Spawn 3-5 agents in parallel for multi-service tasks. Use `run_in_background: true` when results aren't immediately needed. Only go sequential when a later step genuinely depends on an earlier one.
- **Replies**: Direct, fewest words.

## Gotchas

- Every outbound `MessageEnvelope` must propagate `priority` and `correlation_id` from the inbound envelope. Omitting these is a bug.
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
