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
workspace/questions/etl-extensibility.md   ← one consultation
workspace/questions/ui-mobile.md           ← separate, concurrent consultation
workspace/questions/redis-schema.md        ← separate, concurrent consultation
```

**Related sub-topics → sections in one file.** If two questions share components or decisions in one affect the other, keep them in the same file so agents see both contexts.

Naming: `workspace/questions/{kebab-topic}.md`

---

## Steps

### 1. Create a questions file

Create `workspace/questions/{topic}.md`. Add all open questions and classify each:

- **`[H]` Human-required** — product scope, risk tolerance, budget, legal, priority. Only a human can answer. Ask the user — one at a time.
- **`[A]` Agent-resolvable** — technical choices where best practice or codebase constraints determine the answer. Agents proceed immediately without human input.

Organize questions by category: Architecture, Implementation, Security, Performance.

### 2. Proceed on agent-resolvable questions immediately

Do not wait for human answers to unblock technical work. Launch specialist agents on all `[A]` questions in parallel as soon as the file is created.

### 3. Surface human questions

Ask `[H]` questions one at a time. Record answers in the file. If unavailable, choose the most conservative default and flag the assumption clearly.

### 4. Launch specialist agents in parallel

Launch all relevant specialist agents simultaneously. Give each the questions file, `TODO.md`, and relevant source files.

Relevant agents: `architect`, `developer`, `tester`, `security`, `optimizer`, `formal-verifier`, `devops`.

Each agent responds with **APPROVE** or **REQUEST CHANGES**. If unresolved after two rounds, go to step 4.5.

### 4.5 Resolve disagreements via structured debate

If agents disagree (one APPROVE, another REQUEST CHANGES on the same question):

1. **Frame the contention** — orchestrator writes one sentence: what exactly the agents disagree on.
2. **Round 1** — each dissenting agent reads the other's full position, then responds with:
   - Their strongest 1–3 counter-arguments
   - A citation for every counter-argument (file path + line, calculation, or external source)
   - No approvals, no fixes — only rebuttals.
3. **Round 2** — each agent must either:
   - **Concede**: write "I concede: [reason]" — debate ends; record the winner's position as the locked decision.
   - **Strengthen**: add exactly one new piece of evidence not cited in Round 1.
4. **Orchestrator rules** — if no concession after Round 2, the orchestrator decides based on the citation with the highest specificity (concrete measurement beats general principle). Record the ruling and the losing agent's strongest unrefuted argument.

**Hard limits:** Maximum 2 rounds. Citations without a specific file path, line number, or quantitative result are rejected as insufficient evidence.

### 5. Lock decisions

Condense the file to decisions only. Each decision: what was decided, the rationale, rejected alternatives.

### 6. Move tasks to `TODO.md`

Write implementation tasks to `TODO.md` with mandatory TDD checklists:
```
- [ ] **Red:** Write failing test that proves the bug or missing behaviour
- [ ] **Green:** Implement the minimum change to make it pass
- [ ] **Refactor:** Clean up without breaking the test
```

### 7. Delete the questions file

Once **all** questions are answered and all implementation tasks are in `TODO.md`: **delete the questions file immediately.** Decisions live in `TODO.md` items and code comments — a stale questions file becomes misleading.

---

## File Roles

| File | Role |
|------|------|
| `workspace/questions/{topic}.md` | Active consultation — open questions → locked decisions |
| `TODO.md` | Implementation tasks with TDD checklists, derived from locked decisions |

---

## Reference

Used in: `CLAUDE.md` (Plan-first workflow), orchestrator workflows.
