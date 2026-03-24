# Feedback Pattern

The Feedback Pattern drives non-trivial architecture decisions to consensus before any code is written. It eliminates guesswork, prevents premature implementation, and creates a durable record of *why* decisions were made.

---

## When to Use

Use when:
- A feature touches more than one service or changes a cross-cutting concern
- The approach is non-obvious or has meaningful trade-offs
- Human product decisions (scope, risk tolerance, priority) must be made before technical choices

Do NOT use for bug fixes or changes with obvious, unambiguous solutions.

---

## Concurrent Topics — One File vs Many

**Unrelated topics → separate files.** Each file runs its own consultation independently, so agents stay focused and can be launched in parallel without context pollution.

```
questions-etl-extensibility.md   ← one consultation
questions-ui-mobile.md           ← separate, concurrent consultation
questions-redis-schema.md        ← separate, concurrent consultation
```

**Related sub-topics → sections in one file.** If two questions share components or decisions in one affect the other, keep them in the same file so agents see both contexts.

Naming: `questions-{kebab-topic}.md` in the repo root. The generic `questions.md` is for the current active feature when only one consultation is running.

**To run multiple consultations in parallel:**
1. Create one file per unrelated topic
2. Launch separate specialist agent sets for each file simultaneously (background agents)
3. Collect results independently; no cross-file merging needed unless topics converge

---

## Steps

### 1. Create a questions file

Create `questions-{topic}.md` (or `questions.md` for a single active topic). Add all open questions organized by category:
- **Human decisions** — scope, risk tolerance, priority (user must answer)
- **Architecture** — system design, data flow, contracts, service boundaries
- **Implementation** — code patterns, library choices, testing strategy
- **Security** — threat model, secrets, input validation
- **Performance** — scale projections, Redis memory, hot paths

### 2. Surface human questions first

Mark human-only questions as `## ❓ Needs Your Input` at the top. Ask the user — one at a time. Record answers in the file immediately.

### 3. Launch specialist agents in parallel

Once human answers are in, launch all relevant specialist agents simultaneously. Each proposes answers to the remaining technical questions. Give each agent the questions file, `TODO.md`, `REJECTED.md`, and the relevant source files.

Relevant agents: `architect`, `developer`, `tester`, `code-reviewer`, `debugger`, `security`, `database`, `optimizer`, `formal-verifier`, `devops`, `product-manager`.

Each agent responds with **APPROVE** or **REQUEST CHANGES** + a confidence score (1–10).

### 4. Vote & consolidate

Round 1: gather all proposals. Round 2: agents with concerns re-review. Round 3: address specifics. Max 3 rounds — escalate to user if unresolved. Consensus = all consulted agents return APPROVE.

### 5. Lock decisions

Condense the file to decisions only. Each decision: what was decided, the rationale, rejected alternatives (link `REJECTED.md` if applicable).

### 6. Move tasks to `TODO.md`

Write implementation tasks to `TODO.md` with mandatory TDD checklists:
```
- [ ] **Red:** Write failing test that proves the bug or missing behaviour
- [ ] **Green:** Implement the minimum change to make it pass
- [ ] **Refactor:** Clean up without breaking the test
```

### 7. Flush the questions file

Once all decisions are locked and tasks are in `TODO.md`:
- **Remove** `## ❓ Needs Your Input` entirely
- **Remove** all Q&A rows — answered questions are not decisions
- **Keep only** final locked decision bullets
- **Delete the file** once the feature ships and decisions are no longer needed for reference

If a question led to a decision already captured elsewhere, delete the row — it is redundant.

---

## File Roles

| File | Role |
|------|------|
| `questions-{topic}.md` | Active consultation — open questions → locked decisions |
| `TODO.md` | Implementation tasks with TDD checklists, derived from locked decisions |
| `.claude/archive/REJECTED.md` | Project-wide record of rejected ideas with rationale |

`REJECTED.md` is project-wide and permanent. Questions files are per-feature and temporary.

---

## Reference

Used in: `CLAUDE.md` (Plan-first workflow), `.claude/skills/think.md` (Step 2: Questions Phase).
