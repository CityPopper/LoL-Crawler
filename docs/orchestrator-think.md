# Orchestrator "Think" Process

When the user says "think" (or tells the orchestrator to "think"), execute this loop **5 times** (or however many the user specifies):

1. **All agents review the entire codebase** and present improvements they would like in `TODO.md`
2. **Break large/complex tasks into smaller steps** in `TODO.md`
3. **All agents review the proposed TODOs and can present objections.** For each contested item, agents debate and propose compromises. If supermajority (>=2/3) is reached, the item proceeds. If not, send it back for another round of debate. After **3 failed debate rounds** on the same item, scrap it entirely and move on.
4. **Implement the steps**, removing completed tasks as you go
5. **Once all tasks are done**, repeat from step 1

---

## Concurrency

Run agents in parallel — **max 5 agents active at once**.

- In step 1: spawn review agents in batches of 5 (19 total = 4 batches); wait for each batch to complete before starting the next
- In step 3: spawn up to 5 debate sub-threads concurrently per round
- In step 4: spawn up to 5 implementer agents at a time; wait for each batch before starting the next
- Never serialize agents within a batch — always fill all 5 slots with independent tasks

---

## Step 1 — All Review Agents (spawn in batches of 5, 4 batches total)

| Agent type | Domain |
|------------|--------|
| `code-reviewer` | Correctness, race conditions, logic errors, contract violations |
| `security` | Vulnerabilities, injection, secrets, auth, insecure defaults |
| `optimizer` | Performance, algorithmic complexity, Redis N+1, async bottlenecks |
| `architect` | Architecture, coupling, scalability, design patterns |
| `tester` | Test coverage gaps, missing edge cases, brittle tests |
| `devops` | CI/CD, Dockerfiles, Justfile, compose, build reproducibility |
| `database` | Redis key design, atomicity, memory efficiency, missing expiry |
| `qa-tester` | End-to-end UX, CLI help text, error messages, docs vs behavior |
| `doc-keeper` | Docs accuracy, stale content, missing documentation |
| `debugger` | Latent runtime failures, error propagation gaps, exception swallowing |
| `formal-verifier` | Invariants, atomicity proofs, protocol correctness, dead states |
| `product-manager` | Feature gaps, low-value items to descope, operator workflows |
| `ui-ux` | Navigation, empty/loading/error states, accessibility, consistency |
| `web-designer` | HTML semantics, CSS architecture, JS quality, dark theme |
| `content-writer` | Error messages, labels, CLI help text, README clarity |
| `devex` | Dev loop speed, onboarding friction, debugging experience |
| `responsive-designer` | Mobile breakpoints, touch targets, overflow, viewport |
| `design-director` | Design token consistency, visual hierarchy, cross-page coherence |
| `graphic-designer` | CLI output formatting, admin stats display, terminal UX |

## Step 4 — Implementer Agent

Use `developer` for all implementation tasks. Spawn one `developer` agent per independent task/file group in parallel.

---

## Debate Protocol (Step 3)

For each TODO item that any agent objects to:
1. Spawn one debate thread (parallel) per contested item
2. Each thread gets the objecting agent's argument and all other agents' counter-arguments
3. Supermajority vote: if ≥ 2/3 of participating agents approve → proceed
4. If vote fails → send back for revised proposal
5. After 3 failed rounds on the same item → scrap it, move on
6. **Log all scrapped items in `REJECTED.md`** with the reason they were rejected — review agents must read `REJECTED.md` before proposing new items and MUST NOT re-propose anything already listed there

---

## Rejected Items Log

All items scrapped during debate (step 3) are recorded in `REJECTED.md` at the repo root.

**Format for each entry:**
```
## [ID] Short title
**Proposed**: what was proposed
**Rejected because**: the winning objection
**Do not re-propose**: specific variants also ruled out
```

Review agents (step 1) **must read `REJECTED.md` first** before writing their findings. Any proposal that duplicates a rejected item will be automatically scrapped without debate.
