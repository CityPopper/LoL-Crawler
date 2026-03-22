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
- **PACT contracts**: Schemas in `lol-pipeline-common/contracts/schemas/` are the DRY source. IMPORTANT: Never modify schema files without updating all downstream consumer pacts and provider tests.
- **Research before implementation**: Before implementing any non-trivial change, agents MUST research the web for current best practices, existing solutions, known pitfalls, and alternatives. Do not rely solely on training data.
- **Doc-agent bookend pattern**: When running parallel agents, run the doc-keeper agent SEQUENTIALLY — once BEFORE (verify docs are current) and once AFTER (update docs with changes). Doc agent must not run in parallel with implementation agents.
- **Confidence threshold**: Agents should only provide feedback when >=80% confident the change will improve things. It is OK to return no feedback if nothing substantial is found.
- **Quantifiable improvements only**: Every proposed improvement must have a measurable before/after metric (e.g., "reduces Redis calls from N to 1", "prevents unbounded growth of key X", "fixes crash in scenario Y"). Lateral moves (same quality, different style) are rejected. Tests must validate the improvement; if tests fail, roll back and add to REJECTED.md.
- **No hardcoded counts in docs**: Documents should not contain precise counts of tests, files, or lines. Use order-of-magnitude estimates (~10, ~100, ~1000) instead. Precise counts become stale immediately.
- **Container dev environment**: Always run lint, typecheck, and tests inside the dev container (`just dev-ci` or `just dev "just test"`). The host environment may have different Python/dep versions. Build the dev container first with `just dev-build`.
- **Before compound tasks**: Update CLAUDE.md with a TODO list; remove when done.
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

## TODO — Phase 21 CLARITY

- [x] Bound `stream:match_id` MAXLEN to 500,000 (prevent OOM)
- [x] Add rejected ideas OT1-R1 through OT1-R11 to REJECTED.md
- [x] Simplify services: unified Dockerfile + shared requirements
- [x] Doc fixes batch (broken links, naming inconsistencies, missing Discovery README)
- [x] Correlation ID propagation through pipeline
- [x] Extract contract conftest helpers to shared module
- [x] Consumer lag monitoring in UI (`XINFO GROUPS`)
- [x] Fix SyntaxError: unparenthesized multi-except clauses (service.py, redis_client.py, delay_scheduler)
- [x] Fix fetcher priority drop on outbound parse envelopes
- [x] Adaptive rate limiter polling (return wait hint from Lua)
- [x] DLQ analytics summary in UI
- [x] Graceful shutdown draining
- [x] Champion pool diversity (entropy/HHI)
- [x] Tilt/streak indicator
- [x] Match badges (objective: Perfect Game, Penta, Deathless, High KDA)
- [x] Patch-over-patch delta on champions page
- [x] Tier list formula (PBI)

## TODO — Phase 22 TRAJECTORY

- [x] S0: Pin redis:7.2.11-alpine (CVE-2025-49844 Lua RCE fix)
- [x] S0: Increase Redis mem_limit to 2g (AOF rewrite headroom)
- [x] S1: Fix crawler XADD missing maxlen (bypassed Phase 21 OOM fix)
- [x] S1: Fix parser ban/matchup TOCTOU race (SISMEMBER → atomic SADD)
- [x] S1: Fix delay scheduler ZSCORE guard (prevent duplicate dispatch)
- [x] S1: Replace priority SCAN with SET (O(N) → O(1))
- [x] S2: Pipeline analyzer sequential EVALs
- [x] S2: Scope RawStore bundle scan to current month
- [x] S2: Pipeline fetcher sequential Redis calls
- [x] S2: Cache RiotClient rate limit writes
- [x] S3: Per-champion player stats on stats page
- [x] S3: Role performance breakdown
- [x] S3: Playstyle tags
- [x] S3: Rank history time series
