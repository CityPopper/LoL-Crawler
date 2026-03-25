# Review Cycle Workflow

A post-implementation adversarial review pass before a task is closed. Routes the implementation through relevant specialist agents to catch issues the developer and tester may have missed.

Not every task needs a review cycle — use judgment based on risk.

---

## When to Run

Run a review cycle when:
- A change touches a critical path (rate limiter, DLQ lifecycle, distributed lock, system:halted)
- A new service or major module is added
- A cross-service contract changes (stream schema, envelope fields)
- The implementation was done quickly under time pressure

Skip for: test-only changes, doc-only changes, trivial one-line fixes, refactors with no logic change.

---

## Steps

### 1. Identify reviewers

Select agents whose domain intersects the change. Use the prod-pattern agent selection table (`docs/patterns/prod-pattern.md#2-identify-relevant-agents`) as a guide. Typical review cycle involves 2–3 agents.

Common pairings:
- Logic change → `developer` (correctness review)
- Redis protocol → `formal-verifier`
- Performance-sensitive path → `optimizer`
- Security boundary → `security`
- Docker/infra change → `devops`

### 2. Spawn reviewers in parallel

Give each reviewer:
- The diff or changed files (specific file paths + line ranges)
- The relevant test files (so they can see what's already covered)
- `workspace/rejected.md` (so they don't re-propose rejected ideas)

Each reviewer applies their domain checklist and returns findings in the format:
```
file:line — severity (critical/warning/nit) — issue — fix
```

Verdict: `APPROVE` | `REQUEST CHANGES` | `NEEDS DISCUSSION`

### 3. Triage findings

| Severity | Action |
|---|---|
| `critical` | Fix before closing the task. Re-run affected tests. |
| `warning` | Fix if straightforward. Add to `TODO.md` if deferred. |
| `nit` | Optional. Apply only if unambiguous improvement. |

If a finding reveals a fundamental design problem, run the Prod Pattern (`docs/patterns/prod-pattern.md`) to stress-test the decision.

### 4. Apply fixes

Spawn `developer` with the list of critical/warning findings. The developer applies fixes. Re-run tests.

### 5. Close with doc-keeper

After fixes, run the doc-keeper bookend's "after implementation" step (`docs/workflows/doc-bookend.md`) to pick up any doc drift introduced by the fixes.

---

## Related

- Adversarial stress-test (pre-commit): `docs/patterns/prod-pattern.md`
- Doc sync: `docs/workflows/doc-bookend.md`
- TDD: `docs/workflows/tdd-sequential.md`
