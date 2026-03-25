# Doc-Keeper Bookend Workflow

The doc-keeper agent runs SEQUENTIALLY as a bookend around any implementation cycle — once before, once after. Never in parallel with implementation agents.

---

## Why Sequential

Docs must be verified against a stable codebase. Running doc-keeper concurrently with the developer produces a race condition: the doc-keeper reads stale source, the developer changes it, and the doc update is wrong. Bookending eliminates this.

---

## Steps

### Before implementation

Spawn `doc-keeper` with:
- The list of files that will change (or a description of the planned change)
- Instruction: verify that docs currently describing those areas are accurate before we touch the code

The doc-keeper:
1. Reads the source files and their corresponding docs
2. Reports any existing drift (stale docs that will become wrong after the change)
3. Fixes pre-existing drift now, so the post-implementation pass starts clean

Wait for doc-keeper to complete before spawning implementation agents.

### Implementation

Run the implementation workflow (sequential TDD or parallel TDD).

### After implementation

Spawn `doc-keeper` again with:
- The diff or list of changed files
- Instruction: update docs to reflect the changes just made

The doc-keeper:
1. Reads the changed source files
2. Cross-references against the doc inventory in its agent file
3. Updates any docs that have drifted
4. Reports what was changed

---

## When to Skip

Skip the bookend only when:
- The change is entirely internal (no public API, stream, key, or env var changes)
- The change is a test-only addition (tests are not documented at the per-test level)

When in doubt, run it.

## Related

- Sequential TDD: `docs/workflows/tdd-sequential.md`
- Review cycle: `docs/workflows/review-cycle.md`
