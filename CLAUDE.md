# CLAUDE.md — Project Instructions

## Project

LoL Match Intelligence Pipeline — monorepo, Redis Streams, Python 3.14, Podman Compose (default) / Docker Compose.
See `ARCHITECTURE.md` for doc index. See `docs/standards/01-coding-standards.md` for lint/type config.
Platform: macOS. Container runtime: Podman (default). Switch with `RUNTIME=docker just <cmd>`.

## Directives

- **TDD (Red → Green → Refactor)**: Write failing test first. Never skip. Never change contracts to match broken output. Ask if ambiguous.
- **12-factor app** methodology
- **DRY** — Don't Repeat Yourself
- **Service isolation**: Services know only their own input/output contracts. No cross-service imports.
- **PACT contracts**: Consumer-driven, file-based. Schemas in `lol-pipeline-common/contracts/schemas/` are the DRY source. Per-service pacts in `lol-pipeline-*/pacts/`. If no consumer uses a contract, it doesn't exist. Evolve incrementally when adding new fields.
- **Research before implementation**: Before implementing any non-trivial change, agents MUST research the web for current best practices, existing solutions, known pitfalls, and alternatives. Do not rely solely on training data.
- **Doc-agent bookend pattern**: When running parallel agents, run the doc-keeper agent SEQUENTIALLY — once BEFORE (verify docs are current) and once AFTER (update docs with changes). Doc agent must not run in parallel with implementation agents.
- **Confidence threshold**: Agents should only provide feedback when >=80% confident the change will improve things. It is OK to return no feedback if nothing substantial is found.
- **Quantifiable improvements only**: Every proposed improvement must have a measurable before/after metric (e.g., "reduces Redis calls from N to 1", "prevents unbounded growth of key X", "fixes crash in scenario Y"). Lateral moves (same quality, different style) are rejected. Tests must validate the improvement; if tests fail, roll back and add to REJECTED.md.
- **No hardcoded counts in docs**: Documents should not contain precise counts of tests, files, or lines. Use order-of-magnitude estimates (~10, ~100, ~1000) instead. Precise counts become stale immediately.
- **Everything runs in containers**: Always run lint, typecheck, tests, and ALL development commands inside the dev container (`just dev-ci` or `just dev "just test"`). Never rely on host Python/deps — consistency across any machine. Build the dev container first with `just dev-build`.
- **Before compound tasks**: Update CLAUDE.md with a TODO list; remove when done.
- **One function per file**: Every new function goes in its own module file. This lets AI agents load only the relevant module instead of an entire monolith. Shared helpers used by 2+ modules live in a `_helpers.py` co-located with their consumers (DRY). Constants and types shared across a package go in `_types.py` or `_constants.py`. Route handlers are grouped by feature in a `routes/` subpackage.
- **Test structure — colocated**: Unit tests live next to the source file they test: `foo.py` → `test_foo.py` in the same directory. AI agents find both instantly. Bug-fix regression tests go in `tests/regression/` (the red/green test that proved the bug, kept forever). Contract tests are **consumer-driven via pact broker** — consumers publish pacts to a database; providers verify against published pacts. If no consumer uses a contract, it doesn't exist. One file per consumer-provider boundary in `tests/contract/`.
- **Layered composition**: Build code in layers — small "implementation" functions that do one thing, "feature" functions composed of implementations, "business logic" composed of features. New code should call existing functions wherever possible rather than reimplementing. This keeps each layer testable, reusable, and small.
- **Docs: high-level only, link to files**: Documentation covers concepts, goals, and architecture — never implementation details. Link to source files for examples. Never list every test case, duplicate code blocks, or write exhaustive per-function descriptions. If you can link to the file instead, link. Keep sections to ~1-2 paragraphs + file references.
- **Replies**: Direct, fewest words.

## Gotchas

- All complexity/lint thresholds configured in each service's `pyproject.toml` (see `docs/standards/`)
- `Dockerfile.service` is the unified Dockerfile for all services — parameterized by `SERVICE_NAME` and `MODULE_NAME` build args. Individual service Dockerfiles no longer exist.
- `except (X, Y):` — always parenthesize multi-exception clauses (Python 3 syntax). Never write `except X, Y:`.
- Every outbound `MessageEnvelope` must propagate `priority` and `correlation_id` from the inbound envelope. Omitting these is a bug.
- `.claude/archive/REJECTED.md` lists ideas that were evaluated and rejected with reasoning. Agents MUST read it before proposing new ideas to avoid re-proposals.

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

## TODO — Phase 24 MATCH INTELLIGENCE UI

- [x] S0-S5: All sprint tasks complete
- [x] Split admin service into modules (`cmd_*.py`, `_helpers.py`, `_formatting.py`)
- [x] Docs cleanup: XAUTOCLAIM fix in `03-streams.md`, admin README updated
- [x] Delete `docs/SPRINT-PLAN.md` (implemented)
- [ ] Future: Full localization (zh-CN translations, language switcher, extract to JSON)
- [ ] Future: Summoner icon + level badge on profile
- [x] Profile tabs (Summary/Mastery/ARAM) on player stats page
- [x] Enhanced `/health` JSON endpoint with Redis/stream status
- [x] `just monitor` recipe for one-shot health summary
- [x] `just streams-json` recipe for machine-readable stream depths
