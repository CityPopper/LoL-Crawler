# Documentation Review -- All Agent Suggestions

Comprehensive review of all project documentation performed on 2026-03-18.
Covers 23 doc files, 11 service READMEs, and cross-references between them.

---

## Missing Documents (should be created)

| Document | Description | Requesting Agents |
|----------|-------------|-------------------|
| `lol-pipeline-discovery/README.md` | Service README does not exist. Every other service has one. Should cover: stream consumption, idle-check algorithm, `DISCOVERY_POLL_INTERVAL_MS`, `DISCOVERY_BATCH_SIZE`, scaling caveats. | Developer, DevEx, QA |
| `lol-pipeline-lcu/README.md` | Service README does not exist. Should cover: lockfile discovery, WSL2 setup, `LEAGUE_INSTALL_PATH`, `--poll-interval` flag, JSONL format, trust model. | Developer, DevEx |
| `docs/guides/03-ci-workflow.md` | No documentation of the GitHub Actions CI pipeline (`ci.yml`). Should cover: matrix structure, which jobs run, how to read failures, how to add new services, the `|| true` mypy issue. | DevOps, Developer, DevEx |
| `CONTRIBUTING.md` | No contribution guide. Needed for: branching strategy, PR process, commit conventions, required checks before merge, how to run the full check suite. | PM, DevEx, Content Writer |
| `CHANGELOG.md` | No changelog. Phase transitions, breaking changes, and test count milestones are scattered across TODO.md and commit messages. | PM, Content Writer |
| `docs/architecture/10-discovery.md` | Discovery service only has a section in `02-services.md`. Given the complexity of the idle-check, priority gating (Sprint 5), and fan-out behavior, it warrants its own architecture doc. | Architect |
| `docs/architecture/11-lcu.md` | LCU Collector is described in `02-services.md` section 8, but the WSL2/Docker bridge, lockfile format, trust model, and JSONL schema would benefit from a dedicated doc. | Architect, Developer |
| `docs/guides/03-wsl2-setup.md` | WSL2-specific setup (League install path, Docker host networking, lockfile access) is scattered. A focused guide would help onboarding. | DevEx, Debugger |

---

## Existing Documents -- Gaps and Improvements

### `/mnt/c/Users/WOPR/Desktop/Scraper/README.md`

1. **Stale test count.** States "366 unit tests + 44 contract tests." TODO.md says "383 unit + 44 contract." Phase 07 says baseline is 393 after placeholder deletion. The README was previously updated to 330, then 366. This number drifts with every session. Consider using a dynamic badge or a single-source count.
2. **Missing Discovery from pipeline diagram.** Discovery is in the table but not in the ASCII diagram flow. The diagram shows `Discovery` with an arrow to `Crawler` but Discovery actually publishes to `stream:puuid` consumed by Crawler -- the arrow direction is ambiguous.
3. **Missing `/players` and `/logs` routes in Web UI section.** The text mentions "Players" and "Logs" pages but the route table only says "Pages: Stats, Players, Streams, LCU, Logs." The `/players` and `/logs` routes are not in the 02-services.md route table either.
4. **`just lcu-watch` behavior.** README says "continuously collect, polling every `LCU_POLL_INTERVAL_MINUTES` (default: 5)" but the LCU default is actually 0 (one-shot). The `--poll-interval` CLI flag defaults to the env var, which defaults to 0.
5. **Missing `just consolidate` explanation.** Listed in Data Management section but no explanation of what JSONL+zstd archives are or when to use them.

### `/mnt/c/Users/WOPR/Desktop/Scraper/ARCHITECTURE.md`

1. **Broken link.** References `docs/phases/07-architect-review.md` in the Architect Review table, but this file does not exist on disk.
2. **Service count mismatch.** Table says "02 -- Service Contracts" covers "all 7 services" but there are actually 10+ services (Seed, Crawler, Fetcher, Parser, Analyzer, Recovery, Delay Scheduler, Discovery, LCU, UI, Admin).
3. **Data flow diagram missing Discovery.** The summary diagram shows the main pipeline and DLQ flow but omits Discovery's write to `discover:players` and read to `stream:puuid`.

### `/mnt/c/Users/WOPR/Desktop/Scraper/TODO.md`

1. **Stale test count in summary table.** Says "Target: 192 -> ~320 unit tests (current -> with all tiers complete)" but current count is already 383+. The summary table at the bottom has outdated numbers.
2. **"Data collection priority" item is not marked DONE.** This is a pending TODO in TODO.md and also in CLAUDE.md under "Pending Work" as part of Phase 07 Sprint 5. The two tracking locations should be in sync.
3. **Tier 3 and Tier 4 status unclear.** CLAUDE.md says Tier 3 is pending. Phase 07 doc says Tier 3 is "COMPLETE." TODO.md doesn't clearly mark Tier 3 done/not-done.

### `/mnt/c/Users/WOPR/Desktop/Scraper/CLAUDE.md`

1. **Pending Work section out of sync.** Lists "Tier 3 Tests" as pending, but `docs/phases/07-next-phase.md` Sprint 3 says "Status: COMPLETE." One of these is wrong.
2. **Missing weighted queue from Pending Work.** The TODO.md "Data collection priority" item and Phase 07 Sprint 5 describe a large pending feature. CLAUDE.md should track it under Pending Work if it is not yet implemented.
3. **Key Locations table missing entries.** Does not mention `Justfile`, `docker-compose.yml`, `.env.example`, `.github/workflows/ci.yml`, `scripts/`, or `docs/` subdirectories.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/01-overview.md`

1. **12-Factor "Codebase" row says "Polyrepo."** This is a monorepo. The `08-repo-structure.md` title is "Strategy: Monorepo with Shared Library." This is a direct contradiction.
2. **"Port binding" row says "Services are workers (no inbound ports)."** The Web UI binds port 8080. This should note the exception.
3. **Missing `GITHUB_TOKEN` from env var table.** The CLAUDE.md secrets section references it but the env var reference doesn't list it.
4. **`LCU_POLL_INTERVAL_MINUTES` description says "UI reloads LCU data."** But the same env var also controls the `--poll-interval` default for the LCU collector CLI. The dual purpose is not mentioned.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/02-services.md`

1. **Missing `/players` and `/logs` routes in Web UI section 10.** The README says these pages exist, but the route table here only lists `/`, `/stats`, `/stats/matches`, `/streams`, `/lcu`.
2. **Section numbering jump.** Sections go 1-8, then jump to 9 (Discovery) and 10 (Web UI). The LCU Collector is section 8 but listed after 7 (Delay Scheduler). This could be confusing since the numbering does not match the pipeline order.
3. **Missing Admin CLI contract.** The Admin CLI has no section in this document. While it does not consume streams, its Redis read/write contract should be documented somewhere (what keys it reads, writes, deletes).

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/03-streams.md`

1. **`XAUTOCLAIM` noted as "not currently implemented" in Delivery Guarantees section.** But `06-failure-resilience.md` describes a `pending_redelivery_loop` that uses `XAUTOCLAIM`. These contradict each other. The code uses XAUTOCLAIM via the pending redelivery loop, so `03-streams.md` is stale.
2. **Missing `discover:players` from Stream Registry table.** This is a Sorted Set, not a stream, but the Delay Scheduler's `delayed:messages` is already listed. `discover:players` deserves a similar mention.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/04-storage.md`

1. **Missing `player:name:{name}#{tag}` key.** Seed README mentions `player:name:{name}#{tag}` as a Redis cache key for PUUID lookup. This key is not in the Redis Key Schema table.
2. **`discover:players` listed but Discovery section in `02-services.md` calls it `discover:players` while troubleshooting guide (`02-troubleshooting.md`) calls it `discovered:players`.** This is a naming inconsistency -- need to verify which is the actual key name in the code.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/05-rate-limiting.md`

1. **Python Usage section shows hardcoded limits (20, 100).** But the actual code passes `cfg.api_rate_limit_per_second` from Config. The code snippet should show the configurable version.
2. **Missing `wait_for_token()` function documentation.** The doc mentions `wait_for_token()` wraps `acquire_token()` in a polling loop but doesn't show its signature or behavior.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/06-failure-resilience.md`

1. **Pending Entry Redelivery section says "runs as a background task within each consumer process."** Need to verify this is accurate -- some implementations use XAUTOCLAIM in the main consume loop rather than a separate background task.
2. **Missing TOCTOU fix documentation.** TODO.md marks "RawStore TOCTOU race on bundle writes" as DONE, but the resilience doc doesn't describe the NX-based coordination.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/07-containers.md`

1. **docker-compose.yml snippet is incomplete.** Missing services: UI, Discovery, LCU. The note at the bottom says "See docker-compose.yml at the repo root for authoritative configuration," but the example is significantly incomplete, which could mislead.
2. **References `docker-compose.prod.yml`.** This file has been deleted (git status shows `D docker-compose.prod.yml`). The doc should either note it does not exist yet or remove references until it is recreated (Phase 07 DK-6).

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/08-repo-structure.md`

1. **Local Development Workflow lists `docker-compose.prod.yml` in the Infrastructure Files table.** This file has been deleted.
2. **References `github.com/your-org/lol-pipeline-common.git`.** Placeholder URL. Should either be the real repo URL or explicitly noted as a placeholder.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/architecture/09-design-comparison.md`

1. **Section 6 (BFS Crawl) says "We explicitly reject automatic fan-out."** This was true at the time of writing but the Discovery service was added later and implements exactly this (auto-promoting co-players found in parsed matches). The comparison should be updated to reflect Discovery's BFS-like fan-out behavior.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/standards/01-coding-standards.md`

1. **No issues found.** Well-structured and comprehensive.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/standards/02-service-layout.md`

1. **Checklist item 8 says "Add to Justfile lint/typecheck loops (automatic -- glob matches lol-pipeline-*/)."** Should clarify whether this is truly automatic or requires manual addition.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/testing/01-testing-plan.md`

1. **CI Pipeline section shows a GitHub Actions example with `${COMMON_VERSION:-main}`.** In reality, the monorepo CI installs common from the sibling directory, not from a git URL. The CI example is aspirational, not actual.
2. **Coverage targets stated as "lol-pipeline-common >= 90%; each service >= 80%."** No documentation of how to measure or enforce these. No CI integration for coverage gating.
3. **Missing test count baseline.** Multiple documents cite different test counts. The testing plan should be the single source of truth for the current test count, updated after each tier completion.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/phases/README.md`

1. **References `07-architect-review.md` in the Architect Review table.** This file does not exist on disk.
2. **Phase 07 name says "Post-MVP."** But `07-next-phase.md` titles itself "Phase 07 -- IRONCLAD." The names should match.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/phases/07-next-phase.md`

1. **P0-5 says "Update README test count to actual (currently 401 unit + 44 contract)."** The README currently says 366. Neither number matches TODO.md (383) or CLAUDE.md. This is the third different number.
2. **Sprint 3 says "COMPLETE" but CLAUDE.md has Tier 3 unchecked.** Contradicts the pending work section.
3. **Coverage Baseline section says "Exact per-service numbers to be measured at Sprint 1 start and recorded here."** This was never filled in.
4. **Sprint 5 prerequisite says "CQ-5 must be done first" but Sprint 2 status shows all CQ items as "Pending."** No tracking of Sprint 2 progress.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/security/01-security.md`

1. **Redis section says "Port 6379 bound to localhost only in dev."** But Docker by default binds to 0.0.0.0 unless explicitly configured. Phase 07 SEC-4 identifies this as a bug to fix. The security doc should note the current state accurately.
2. **Missing PUUID format validation recommendation.** SEC-1 in Phase 07 identifies PUUID validation as a medium-severity issue, but the Input Validation section of the security doc says PUUIDs are "validated by Riot API response" without noting that the UI accepts them directly from query parameters.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/operations/01-deployment.md`

1. **"Future: Bare-Metal Production" section includes a `docker-compose.prod.yml` template** but the actual file has been deleted. Should note this is a proposed template, not a reference to an existing file.
2. **Justfile Reference table is comprehensive.** No significant gaps.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/operations/02-monitoring.md`

1. **Health Check Matrix lists Discovery's key as `discovered:players` but the architecture docs call it `discover:players`.** Naming inconsistency (see also `04-storage.md` entry).
2. **Future Improvements section lists `/health` endpoint.** Should cross-reference Phase 07 DK-3 which adds HEALTHCHECK to UI Dockerfile.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/guides/01-local-dev.md`

1. **No significant gaps.** Well-structured guide covering venvs, testing, linting, new service creation, IDE tips.
2. **Minor: "Adding a New Service" checklist does not mention adding to CI matrix (`ci.yml`).** This was a real issue -- LCU was missing from CI until recently.

### `/mnt/c/Users/WOPR/Desktop/Scraper/docs/guides/02-troubleshooting.md`

1. **Redis State Inspection section uses `SCARD "discovered:players"`.** Should be `ZCARD "discover:players"` -- it is a Sorted Set, not a Set, and the key name is inconsistent with other docs.
2. **Missing section on Docker build failures.** Common issues: stale layers, pip cache, volume mount permissions on WSL2.

### `/mnt/c/Users/WOPR/Desktop/Scraper/lol-pipeline-admin/README.md`

1. **Lists `system-halt` command.** This command does not exist in the admin source code. Phase 07 P0-1 identifies this as a doc/code mismatch.
2. **Lists `streams` command.** This command does not exist in the admin source code. Phase 07 P0-2 identifies this.
3. **Missing `replay-parse`, `replay-fetch`, `dlq replay`, `dlq clear`, `reseed` commands.** These all exist in the code but are not documented in the README.
4. **Missing `dlq replay` subcommand.** Only `dlq list` and `dlq clear --all` are listed.

### `/mnt/c/Users/WOPR/Desktop/Scraper/lol-pipeline-common/contracts/README.md`

1. **No significant gaps.** Clear CDCT workflow, schema location, and update checklist.
2. **Minor: Code example uses `datetime.utcnow()`.** This is deprecated since Python 3.12; should use `datetime.now(tz=UTC)`.

### `/mnt/c/Users/WOPR/Desktop/Scraper/lol-pipeline-ui/README.md`

1. **Route table is outdated.** Lists `/` as "Home -- seed form + stream depth overview" but `02-services.md` says `/` redirects to `/stats`. Missing `/players`, `/logs`, `/streams`, `/stats/matches` routes.

---

## Content That Needs Updating

### Test count discrepancy (affects 4+ files)

| File | Stated Count |
|------|-------------|
| `README.md` | 366 unit + 44 contract |
| `TODO.md` summary | "Current state: 383 unit + 44 contract" |
| `CLAUDE.md` | (no count) |
| `07-next-phase.md` P0-5 | "currently 401 unit + 44 contract" |
| `07-next-phase.md` summary | "Unit tests: 393 (401 minus 8 placeholders)" |

**Resolution:** Determine the actual count (run `just test` and count), update a single source-of-truth location, and have other files reference it. The 9 placeholder test files still exist on disk, inflating the count.

### `discover:players` vs `discovered:players` (affects 2 files)

- `docs/architecture/04-storage.md` line 23: `discover:players` (Sorted Set)
- `docs/operations/02-monitoring.md` line 115: `discovered:players set`
- `docs/guides/02-troubleshooting.md` line 381: `SCARD "discovered:players"`

**Resolution:** Check the actual Redis key name in source code and make all docs consistent. Also fix `SCARD` to `ZCARD` (it is a Sorted Set).

### "Polyrepo" vs monorepo (affects 1 file)

- `docs/architecture/01-overview.md` line 70: `Polyrepo; one deployable per service; shared common library`

**Resolution:** Change to "Monorepo" to match `08-repo-structure.md` and the actual repo structure.

### `docker-compose.prod.yml` references (affects 3 files)

- `docs/architecture/08-repo-structure.md` Infrastructure Files table
- `docs/architecture/07-containers.md` (no direct reference, but implied)
- `docs/operations/01-deployment.md` Production section

**Resolution:** Note that the file has been deleted and is planned for recreation in Phase 07 DK-6.

### `07-architect-review.md` broken link (affects 2 files)

- `ARCHITECTURE.md` line 69
- `docs/phases/README.md` Architect Review table

**Resolution:** Either create the file or remove the references.

### XAUTOCLAIM contradiction (affects 2 files)

- `docs/architecture/03-streams.md` line 99: "XAUTOCLAIM is not currently implemented"
- `docs/architecture/06-failure-resilience.md` lines 83-91: Describes `pending_redelivery_loop` using XAUTOCLAIM

**Resolution:** Update `03-streams.md` to reflect that XAUTOCLAIM is implemented via the pending redelivery loop.

---

## Style and Consistency Issues

### Terminology inconsistencies

| Term in Doc A | Term in Doc B | Correct Term |
|---------------|---------------|-------------|
| `discover:players` (04-storage) | `discovered:players` (02-monitoring, 02-troubleshooting) | Check source code |
| "Polyrepo" (01-overview) | "Monorepo" (08-repo-structure) | Monorepo |
| "SEED_COOLDOWN_HOURS" (01-testing-plan) | "SEED_COOLDOWN_MINUTES" (README, 01-overview) | `SEED_COOLDOWN_MINUTES` |
| "all 7 services" (ARCHITECTURE.md) | 10+ services in reality | Update count |
| Web UI `/` description differs | "Redirect to /stats" (02-services) vs "Home -- seed form" (UI README) | Check source code |

### Formatting differences

1. **Phase docs** use `Status:` field; other docs do not track status.
2. **Service READMEs** vary in depth: Crawler has detailed behavior + error table; Discovery and LCU have no README at all; Admin README is 22 lines.
3. **Environment variable tables** appear in 4 different locations (README, 01-overview, 07-containers, 01-deployment) with slightly different columns and ordering. Should have one authoritative table with others referencing it.

### Missing cross-references

1. `02-services.md` does not link to the troubleshooting guide for debugging specific services.
2. `01-security.md` does not link to Phase 07 security fixes (SEC-1 through SEC-4).
3. `01-testing-plan.md` does not link to TODO.md Tier 3/4 test plans.
4. `01-deployment.md` does not link to `01-local-dev.md` for the dev setup alternative.
5. No doc links to the CI workflow file (`.github/workflows/ci.yml`).

---

## Agent-Specific Suggestions

### Architect

1. **Discovery architecture doc is missing.** The idle-check algorithm, priority gating (Sprint 5), fan-out behavior, and interaction with `system:priority_count` are complex enough for a standalone doc.
2. **Design comparison (09) is stale.** Section 6 says "we explicitly reject automatic fan-out" but Discovery implements exactly this. Update to acknowledge BFS-like discovery while noting it is idle-gated rather than immediate.
3. **Weighted queue architecture is only in Phase 07.** Once implemented, the priority system (Lua scripts, counter semantics, TTL safety net) should be promoted to a proper architecture doc section.
4. **Missing ADR (Architecture Decision Records).** Key decisions (why Redis Streams over Kafka, why monorepo, why no authentication) are embedded in various docs but not recorded as formal decisions with context and consequences.

### PM (Project Manager)

1. **No changelog or release notes.** Phase transitions, test milestones, and breaking changes are not tracked in a structured way.
2. **Test count tracking is broken.** Four different files cite four different numbers. Needs a single source of truth.
3. **Phase 07 sprint progress is not tracked.** Sprint 2 items are all "Pending" with no dates or assignments. Sprint 1 P0 items have no completion status.
4. **No definition of "Phase 08."** Phase 07 defers items to Phase 8 but there is no Phase 8 document or even a stub.
5. **TODO.md is a hybrid tracking/history doc.** Completed items are struck through but remain in the file, making it hard to see what is actually pending. Consider separating active TODOs from completed history.

### Developer

1. **Admin CLI README is severely outdated.** Documents commands that don't exist (`system-halt`, `streams`) and omits commands that do (`replay-parse`, `replay-fetch`, `dlq replay`, `reseed`).
2. **Missing Discovery and LCU service READMEs.** Every service should have a README with behavior, env vars, error handling, and usage examples.
3. **No API documentation for `lol-pipeline-common` modules.** The README lists modules but does not document function signatures, return types, or usage patterns beyond what is in the architecture docs.
4. **`your-org` placeholder in Dockerfile examples.** `08-repo-structure.md` and `07-containers.md` reference `github.com/your-org/lol-pipeline-common.git`. Should use actual repo URL or explicitly note placeholders.

### Tester

1. **Test count inconsistency across docs.** See "Content That Needs Updating" section above.
2. **Tier 3 status contradiction.** CLAUDE.md says Tier 3 is pending; Phase 07 says complete. One must be updated.
3. **Coverage targets are stated but not enforced.** No CI job fails on coverage regression. `01-testing-plan.md` states targets but `07-next-phase.md` says "Exact per-service numbers to be measured at Sprint 1 start and recorded here" and that line was never filled in.
4. **9 placeholder test files still exist.** Phase 07 P0-7 calls for their deletion. They inflate test counts and provide no value.
5. **Integration test docs reference IT-01 through IT-07 (CLAUDE.md)** but the testing plan describes 7 integration test scenarios without numbering them. The IDs should match.

### Security

1. **Redis port binding claim is inaccurate.** Security doc says "Port 6379 bound to localhost only in dev" but Docker typically binds to 0.0.0.0 unless the compose file specifies `127.0.0.1:6379:6379`. Phase 07 SEC-4 identifies this as a fix needed.
2. **PUUID validation gap.** The UI accepts PUUIDs from query parameters without format validation. The security doc claims PUUIDs are "validated by Riot API response" but this is not true for the UI path where a user directly visits `/stats/matches?puuid=...`.
3. **`.dockerignore` files do not exist.** SEC-3 in Phase 07 identifies this. The security doc should note this current gap.
4. **`datetime.utcnow()` usage.** The contracts README code example uses the deprecated `datetime.utcnow()`. While not a security issue per se, it is a Python 3.12 deprecation.
5. **No dependency vulnerability scanning in CI.** The security doc recommends `pip-audit` and `safety` but neither is integrated. Should be documented as a gap.

### DevOps

1. **CI workflow is undocumented.** `.github/workflows/ci.yml` exists but no doc describes its structure, matrix, or failure modes.
2. **`docker-compose.prod.yml` has been deleted.** Multiple docs reference it. Phase 07 DK-6 plans to recreate it but no timeline is given.
3. **No documentation of Docker image tagging strategy.** `07-containers.md` shows image names but not how versions are tagged, pushed, or deployed.
4. **No documentation of backup automation.** `01-deployment.md` has manual backup commands but no scheduled backup recommendation.
5. **mypy CI is gated with `|| true`.** Phase 07 DK-8 identifies this. The deployment/CI docs should note this current weakness.

### UI/UX

1. **Web UI route table is inconsistent across docs.** `02-services.md` lists 5 routes; README lists 5 pages by name; UI README lists 3 routes. The actual UI may have more (e.g., `/players`, `/logs`).
2. **No screenshots or visual documentation of the UI.** For a user-facing component, visual docs help new developers and users understand what they are working with.
3. **Match history JS error documented in Phase 07 CQ-4** (`e` -> `(e.message || e)`) is not mentioned in the troubleshooting guide.
4. **No documentation of the auto-seed UX flow.** The UI auto-seeds when a player has no data, but the user experience (what they see, how long to wait, how to know it worked) is not documented anywhere user-facing.

### Content Writer

1. **TODO.md is 310 lines with extensive struck-through content.** Completed items should be archived to maintain readability. The active/pending items are buried among the completed ones.
2. **Phase 07 is the largest doc at ~280 lines.** Consider breaking Sprint 5 (Weighted Queue) into its own document once implementation begins, since it has its own prerequisites, design, contract changes, and 15 acceptance criteria.
3. **Inconsistent heading styles.** Some docs use `##` for major sections; others use `#`. The phase docs use `---` separators liberally while architecture docs use them sparingly.
4. **Admin README is terse.** At 22 lines, it is the thinnest service README. It documents nonexistent commands and omits real ones. A rewrite is needed.

### Debugger

1. **Troubleshooting guide is comprehensive** but missing a section on Docker build/startup failures (common on WSL2: permission issues, volume mounts, pip cache).
2. **No documentation of how to debug the rate limiter Lua script.** If the script misbehaves, developers need to know how to inspect its behavior (e.g., `EVALSHA` directly, check ZRANGE of ratelimit keys).
3. **No documentation of how to replay a single player end-to-end** for debugging. The troubleshooting guide shows tracing but not replay.
4. **Missing log level configuration guide.** `LOG_LEVEL` env var is documented in the deployment doc but the troubleshooting guide does not mention setting `LOG_LEVEL=DEBUG` as a diagnostic step.

### QA (Quality Assurance)

1. **Cross-reference gaps between phase docs and implementation status.** Phase 07 says Tier 3 is COMPLETE; CLAUDE.md says it is pending. These are the two files an AI agent reads first -- contradictions here cause work to be repeated or skipped.
2. **Admin README documents commands that do not exist in the code.** This is a verification gap -- docs were not checked against the implementation.
3. **No doc verification process defined.** There is no checklist or CI step that validates docs against code (e.g., verifying that documented admin commands exist, that documented Redis keys are used, that env var tables match Config class fields).
4. **Env var table appears in 4 locations.** `README.md`, `01-overview.md`, `07-containers.md`, `01-deployment.md`. None explicitly states "this is the authoritative source" or links to `.env.example` as the single source of truth.

### DevEx (Developer Experience)

1. **No onboarding checklist.** A new developer joining the project has no step-by-step guide. The local dev guide assumes familiarity with the project. A "first 30 minutes" guide would help.
2. **Service README coverage is uneven.** Crawler, Seed, Common, UI, and Admin have READMEs. Discovery and LCU do not. The existing READMEs vary from 22 lines (Admin) to detailed multi-section docs (Crawler).
3. **Missing "Adding to CI" step in new service checklist.** `02-service-layout.md` and `01-local-dev.md` both have new-service checklists but neither mentions adding the service to `.github/workflows/ci.yml`. This was a real bug (LCU was missing from CI).
4. **No documentation of the `scripts/` directory.** `update_mocks.py` and `consolidate_match_data.py` are referenced in Justfile commands but have no documentation beyond what `just update-mocks` and `just consolidate` say.
5. **IDE setup tips are minimal.** Only VSCode and PyCharm are covered. No mention of Cursor, Zed, Neovim, or remote development via WSL2 (which is the actual development environment based on the repo path).

---

## Priority Summary

### P0 -- Fix immediately (broken/incorrect content)

1. Admin README: remove nonexistent commands, add real commands
2. Fix "Polyrepo" -> "Monorepo" in `01-overview.md`
3. Fix XAUTOCLAIM contradiction between `03-streams.md` and `06-failure-resilience.md`
4. Remove or fix broken `07-architect-review.md` link
5. Resolve Tier 3 status contradiction between CLAUDE.md and Phase 07

### P1 -- Fix soon (stale content causing confusion)

1. Standardize test count across all docs
2. Fix `discover:players` / `discovered:players` naming inconsistency
3. Note `docker-compose.prod.yml` deletion in all referencing docs
4. Create Discovery and LCU service READMEs
5. Update admin README with actual commands

### P2 -- Improve (gaps that slow down development)

1. Create CI workflow documentation
2. Create CONTRIBUTING.md
3. Consolidate env var tables to single source of truth
4. Add "add to CI matrix" to new service checklists
5. Update Design Comparison section 6 to reflect Discovery

### P3 -- Enhance (nice-to-have improvements)

1. Create ADR (Architecture Decision Records) directory
2. Create CHANGELOG.md
3. Create onboarding checklist for new developers
4. Add UI screenshots to docs
5. Archive completed TODO.md items
