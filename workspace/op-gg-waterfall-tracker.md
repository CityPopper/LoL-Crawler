# Op.gg Source Waterfall — Progress Tracker

_Last updated: 2026-03-25_

---

## Overall Status: 🔄 Design Review Phase — R1 complete, architect revision done, R2 pending

---

## Phase 0 — PRIN Refactoring (prerequisite) ✅ COMPLETE (+92 unit tests committed)

All 33 PRIN violations fixed and committed:
- CMN-01/03/04/05/06/07, ANZ-04 (common)
- CRW-01/02/03 (crawler)
- FET-01 (fetcher)
- PAR-01/02/03 (parser)
- REC-01/02 (recovery)
- DLY-01/02/03/04 (delay-scheduler)
- DSC-01/02 (discovery)
- ADM-01/02/03/04 (admin)
- AUI-01/02/03 (admin-ui)
- UI-01/02 (ui)
- CHS-01/02/03/04 (champion-stats)

Stale PRIN-ANZ-01/02/03 removed (service no longer exists).

---

## Phase 1 — Design Document

**File**: `workspace/design-source-waterfall.md`

| Update | Status |
|--------|--------|
| Initial architect draft (691 lines) | ✅ Done |
| Rate limit corrected to 1 req/s | ✅ Done |
| Testing strategy section added | ✅ Done |
| Open questions Q1-Q6 resolved (autonomous) | ✅ Done |
| Tester-paired implementation note | ✅ Done |
| Genericity audit (DataType enum, Source protocol, BlobStore, coordinator) | ✅ Done |
| Live op.gg test requirement | ✅ Done |

---

## Phase 2 — Design Review Cycles

**Goal**: All reviewers report no major issues before implementation starts.

| Reviewer | Round 1 | Round 2 | Round 3 | Final |
|----------|---------|---------|---------|-------|
| developer | ✅ Done | 🔄 Pending | — | — |
| formal-verifier | ✅ Done | 🔄 Pending | — | — |
| optimizer | ✅ Done | 🔄 Pending | — | — |
| security | ✅ Done | 🔄 Pending | — | — |
| ai-specialist | ✅ Done | 🔄 Pending | — | — |

**Current iteration**: Round 2 (architect revision complete, awaiting reviewer re-review)

---

## Phase 3 — TODO Breakdown

**Status**: ⏳ Blocked on Phase 2 completion

---

## Phase 4 — Implementation

**Status**: ⏳ Blocked on Phase 3

Each task requires:
- Developer agent (implementation)
- Tester agent (concurrent, writes tests)
- Tests pass before task closes
- Commit after each batch

### Planned task batches (TBD after design is locked):
1. Generic foundation (DataType enum, Source protocol, SourceRegistry, BlobStore)
2. WaterfallCoordinator + integration into Fetcher
3. RiotSource implementation
4. Op.gg ETL fix (gameCreation → gameStartTimestamp)
5. OpggSource implementation (extractor + transformer)
6. Proactive emit (stream:blob_available)
7. Config + migration path

---

## Phase 5 — Integration & E2E Tests

**Status**: ⏳ Blocked on Phase 4

Tests to run after every batch:
- Unit tests: `just test-svc fetcher`, `just test-svc common`
- Integration tests (testcontainers): IT-WF-01 through IT-WF-04
- **Live op.gg tests** (actual HTTP, env flag `OPGG_LIVE_TESTS=1`): validate real API shape, real rate limit enforcement, real blob disk write
- E2E: full `stream:match_id` → waterfall → `stream:parse` roundtrip

---

## Phase 6 — Prod Pattern (post-integration)

**Status**: ⏳ Blocked on Phase 5

Run prod pattern repeatedly until no major bugs found.
Agents: architect + developer + formal-verifier + optimizer + security

Each round appended to `workspace/design-source-waterfall.md`.

---

## Phase 7 — Doc Sync

**Status**: ⏳ Blocked on Phase 6

- doc-keeper sweep across entire project
- Update `docs/architecture/03-streams.md`, `04-storage.md`, `05-rate-limiting.md`
- New architecture doc for source waterfall
- ARCHITECTURE.md summary update

---

## Phase 8 — Final Commit & Cleanup

**Status**: ⏳ Blocked on Phase 7

- Remove this tracker file
- Commit everything

---

## Active Agents

| Agent ID | Role | Task | Status |
|----------|------|------|--------|
| af4c308 | developer | Full test sweep + fix all failures | ✅ Done — 1,346 tests green, committed |
| a584d75 | tester | Coverage gap review across PRIN changes | ✅ Done — 92 tests committed |
| Review cycle agents | 5× reviewers | Design doc review round 1 | ✅ Done |
| architect | system-architect | Revise design doc per R1 findings (12 issues) | ✅ Done |

---

## Decisions Made Autonomously (user unavailable)

| Decision | Choice | Rationale |
|----------|--------|-----------|
| Q1: Op.gg live fallback | Disk-cache-hit path only; no live fetch by Riot match ID | Op.gg has no match-by-ID endpoint |
| Q2: Match ID cross-ref | Not attempted | No feasible mapping between op.gg internal IDs and Riot IDs |
| Rate limit | 1 req/s | Explicit user requirement |
| Source genericity | Full protocol abstraction; op.gg = first instance, not special-cased | User requirement: u.gg and others must be addable with zero coordinator changes |
| Live op.gg tests | Required, gated behind OPGG_LIVE_TESTS=1 env flag | User requirement for real API validation |

---

## Issues / Blockers

| Issue | Service | Status |
|-------|---------|--------|
| `_90_DAYS` N806 lint error | champion-stats tests | 🔄 Being fixed by test sweep agent |
| ETL gap: `gameCreation` vs `gameStartTimestamp` | common/_opgg_etl.py | Design doc Phase 1 prerequisite; fix in OpggSource phase |
| Op.gg has no match-by-ID API | common/opgg_client.py | Documented; waterfall value is cache-hit path |

---

## Key File Locations

| File | Purpose |
|------|---------|
| `workspace/design-source-waterfall.md` | Design doc (reviews appended here) |
| `workspace/op-gg-waterfall-tracker.md` | This file |
| `workspace/rejected.md` | Rejected proposals (must read before proposing) |
| `lol-pipeline-common/src/lol_pipeline/opgg_client.py` | Existing op.gg client |
| `lol-pipeline-common/src/lol_pipeline/_opgg_etl.py` | Existing op.gg ETL |
| `lol-pipeline-fetcher/src/lol_fetcher/main.py` | Fetcher (waterfall hooks here) |
