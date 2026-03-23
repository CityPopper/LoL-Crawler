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

## i18n Architecture

Two-layer localization:
- **`lol_pipeline/i18n.py`** (common) — shared domain vocabulary: role names, rank tiers, queue types, status labels, failure codes. `label("role", "TOP", "zh-CN")` → "上单". Missing translations tracked in Redis SET `i18n:missing:{lang}`.
- **`strings.py`** (per-service) — UI text stays in UI (`t()`), admin stays English.
- **DDragon game data** — fetch per-locale at render time (`ddragon:champion_names:{lang}`), 24h TTL. English keys everywhere in data layer, translate only at HTML render.

## TODO

- [ ] Audit all fallback/default values — replace with explicit errors. No silent fallbacks to magic strings/numbers.
- [ ] Wire `lol_pipeline.i18n.label()` into all UI displays of roles, tiers, queues (currently raw English codes)
- [ ] Bugfix: switching themes should keep you on the same page (currently redirects to /)
- [ ] Art Pop theme overhaul (graded C+ by graphic designer):
  - [ ] Bright colored header bar (solid #ff2d9b or #00c8ff with dark text) — Pop Art backgrounds ARE the color
  - [ ] Ben-Day dots: raise opacity to 35%, increase radius to 2.5px, reduce grid to 12px
  - [ ] Decorative shapes: double all opacities to 25-40%
  - [ ] Nav bar: add colored bottom border + slight background tint
  - [ ] Table headers: solid color block (Impact, dark text on bright bg)
  - [ ] Card borders: thicken to 3px solid #f5f0e6 (visible white outline)
  - [ ] Champions page: add row separation, fix dark-on-dark text
- [ ] Bugfix: skip-to-content link visible in Art Pop theme (themes.py line 208 sets position:relative, breaks absolute hiding)
- [ ] Bugfix: theme switcher overlaps footer on mobile (add padding-bottom to footer)
- [ ] Bugfix: DLQ Analytics card has hardcoded English (dlq_helpers.py ~10 strings need t())
- [ ] Bugfix: stream status badges (OK/Busy/Backlog) not translated in zh-CN
- [ ] UX: mobile nav has no scroll affordance (4 of 8 items hidden, no indicator)
- [ ] UX: mobile streams table columns truncated (Lag/Status cut off)
- [ ] Nit: logs auto-refresh interval label discrepancy

## DONE — Phase 25 UI POLISH

- [x] R2-1: Delete dead code `_load_tilt_data` (stats.py) and `_render_build_section` (match_detail.py)
- [x] R2-2: Replace bare `int()` with `_safe_int()` in champions_helpers.py and rank.py
- [x] R2-3: Fix log_helpers.py DRY violation (import from constants.py and rendering.py)
- [x] UX-1: Logs page expandable log entries
- [x] UX-2: Logs page Clear button
- [x] UX-3: Logs page service filter dropdown
- [x] UX-4: DLQ page expandable entries
- [x] SEC-1: Add Permissions-Policy header
