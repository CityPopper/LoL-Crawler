---
name: think
description: Run a full orchestrate-think cycle — questions.md for design decisions, plan in TODO.md, launch ALL agents for consensus review, implement with TDD, doc-agent bookend. Use for any non-trivial cross-cutting work.
user_invocable: true
---

# Orchestrate-Think Cycle

Full multi-agent review and implementation cycle. Every step is mandatory.

## Rules

1. **Confidence >=80%**: Only propose changes you are >=80% confident will improve things. No filler, no lateral moves.
2. **Quantifiable improvements only**: Every proposal needs a measurable before/after metric. Tests must validate; if tests fail, roll back and add to `REJECTED.md`.
3. **Doc-agent bookend**: Run `doc-keeper` SEQUENTIALLY before and after. Never in parallel with implementation agents.
4. **Research before implementation**: Research the web for best practices before any non-trivial change.
5. **No hardcoded counts in docs**: Use order-of-magnitude estimates (~10, ~100).
6. **Test validation**: All changes must pass tests in the dev container (`just dev-ci`). If tests fail, roll back.
7. **Check REJECTED.md first**: Read `.claude/archive/REJECTED.md` before proposing anything.
8. **Plan-first**: Write a checklist plan in `TODO.md` before executing. Spawn ALL agents to review the plan. Proceed only after consensus.

## Step 1: Research Current State

Read these files to understand context:
- `CLAUDE.md` — Project directives
- `TODO.md` — Open work items
- `ARCHITECTURE.md` — Doc index
- `.claude/archive/REJECTED.md` — Rejected ideas (do not re-propose)
- Any files relevant to the user's specific request

## Step 2: Questions Phase (non-trivial features only)

Follow the **Feedback Pattern** (`docs/patterns/01-feedback-pattern.md`) for any new feature or significant design decision. Summary:

1. Create `questions-{topic}.md` per unrelated topic (concurrent topics → separate files)
2. Classify questions: `[H]` human-required (product/scope/risk) vs `[A]` agent-resolvable (technical)
3. Launch agents on `[A]` questions immediately — no waiting for human
4. Surface `[H]` questions to user one at a time; if unavailable, use lowest-risk default + flag assumption
5. Vote & consolidate (max 3 rounds); lock decisions; move tasks to `TODO.md`
6. Flush: remove all Q&A rows, keep only locked decision bullets, delete file when feature ships

Skip for small bug fixes or changes with obvious answers.

## Step 3: Write Plan in TODO.md

Write a checklist of proposed changes in `TODO.md` under a new section for this cycle. Each item should be specific and actionable. Link back to `questions.md` for context if a questions phase was run.

**Every implementation task MUST include explicit TDD steps as the first checklist items:**
```
- [ ] **Red:** Write failing test that proves the bug or missing behaviour
- [ ] **Green:** Implement the minimum change to make it pass
- [ ] **Refactor:** Clean up without breaking the test
```
A task without these three steps is incomplete and must not be executed.

## Step 4: Doc-Agent Bookend (BEFORE)

Launch `doc-keeper` agent **sequentially** to verify docs are current before making changes.

## Step 5: Launch ALL Agents in Parallel

Spawn every available agent simultaneously. Each reviews the plan from their specialty:

| Agent | Reviews for |
|-------|------------|
| `architect` | System design, trade-offs, stream topology |
| `developer` | Implementation patterns, TDD plans, technical debt |
| `tester` | Test coverage, contract tests, fixture quality |
| `code-reviewer` | Quality, security, standards compliance |
| `debugger` | Failure paths, error handling, race conditions |
| `security` | Threats, vulnerabilities, secret handling |
| `devops` | Docker, CI/CD, deployment, scaling |
| `optimizer` | Performance, Big-O complexity, hot paths |
| `database` | Redis architecture, key design, memory |
| `formal-verifier` | Correctness proofs, invariants, atomicity |
| `product-manager` | Prioritization, requirements, acceptance criteria |
| `content-writer` | User-facing text, terminology consistency |
| `qa-tester` | End-to-end experience, docs-vs-code accuracy |
| `devex` | Developer experience, tooling, onboarding |
| `design-director` | Design vision, cross-surface consistency |
| `graphic-designer` | Visual quality, color, typography, spacing |
| `ui-ux` | Interface design, user experience, ergonomics |
| `responsive-designer` | Mobile/tablet/desktop responsiveness |
| `web-designer` | HTML/CSS/JS implementation, layouts |

For UI tasks, also invoke `/analyze-ui` to capture and review screenshots.

Each agent must respond with:
- **APPROVE** or **REQUEST CHANGES**
- Specific findings with file:line references
- Confidence level (1-10)

## Step 6: Consensus Protocol

- **Round 1**: Gather initial feedback from all agents
- **Round 2**: Incorporate feedback, re-review with agents that had concerns
- **Round 3**: Address remaining specifics
- **Max 3 rounds**: If consensus not reached, escalate to user with disagreement summary
- **Approved** = ALL consulted agents return APPROVE

## Step 7: Implement Changes

Apply changes based on consensus. For each change:
- Write failing test first (TDD)
- Implement the fix
- Verify test passes
- Ensure measurable before/after metric exists

## Step 8: Update TODO.md

Mark completed items. Remove done sections. Add any new items discovered during implementation.

## Step 9: Doc-Agent Bookend (AFTER)

Launch `doc-keeper` agent **sequentially** to update docs with changes made.

## Step 10: Report to User

1. **Agent roster**: Which agents were consulted
2. **Consensus status**: Per-agent APPROVE/REQUEST CHANGES with confidence
3. **Findings summary**: De-duplicated, prioritized
4. **Actions taken**: What was changed
5. **TODO.md status**: Items completed vs remaining
6. **Next steps**: What remains
