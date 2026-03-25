# Prod Pattern

The Prod Pattern challenges a proposed action, decision, or implementation before it is committed. It surfaces non-obvious problems, missed alternatives, and unjustified assumptions by routing the proposal through relevant specialist agents — each researching independently and asking hard questions.

The name is deliberate: "prodding" a decision to see if it holds up.

---

## When to Use

Use when:
- A decision has already been made (or is about to be) and you want to stress-test it
- An implementation was done quickly and the rationale was assumed, not argued
- You suspect a simpler or better alternative exists but haven't articulated why
- You want adversarial review before committing to a direction

Do NOT use for decisions that are obviously correct, reversible with low cost, or already consensus-driven via the Feedback Pattern.

The Prod Pattern is retrospective or pre-commit. The Feedback Pattern is prospective (before any decision). They are complementary.

---

## What It Produces

Each participating agent returns a set of **substantial questions** — not nitpicks, not approvals, not implementation feedback. Questions only. The questions must challenge:

- **Justification** — Why this approach and not another? What assumption is load-bearing here?
- **Alternatives** — What was ruled out? Was it ruled out for the right reason?
- **Simplification** — Is this more complex than the problem requires? What is the minimum viable version?
- **Optimization** — Is there a strictly better solution on the dimensions that matter (cost, performance, maintainability, correctness)?
- **Second-order effects** — What does this decision make harder in the future? What does it break that wasn't immediately obvious?

Trivial questions are prohibited. A question is trivial if it can be answered by reading the existing code or docs without judgment. Every question must require the proposer to think.

---

## Steps

### 1. State the proposal clearly

Write a 1–3 sentence description of the action or decision being prodded:
- What was done (or is proposed)
- The justification given (or assumed)
- The scope (which files, services, or systems it touches)

### 2. Identify relevant agents

Select agents whose domain intersects the proposal. Do not involve agents with no stake in the decision — their questions will be generic.

| If the proposal touches... | Include... |
|---------------------------|------------|
| Architecture, data flow, service design | `architect` |
| Implementation, libraries, code patterns | `developer` |
| Test strategy, coverage, TDD | `tester` |
| Correctness, invariants, atomicity | `formal-verifier` |
| Performance, complexity, Redis access patterns | `optimizer` |
| Security, secrets, input validation | `security` |
| Docker, CI, deployment | `devops` |
| Documentation, cross-surface accuracy | `doc-keeper` |
| Web UI, CLI ergonomics, diagrams | `designer` |
| Agent design, prompt patterns, Claude API | `ai-specialist` |

Typical prod involves 2–4 agents. More than 5 is usually a sign the proposal scope is too broad.

### 3. Launch agents in parallel

Spawn all selected agents simultaneously. Give each agent:
- The proposal statement (step 1)
- The relevant source files or diffs
- `.claude/archive/REJECTED.md` — so they don't ask about already-rejected alternatives
- Instruction to **research first** (web search, HN, codebase) before asking questions

Each agent must be prompted with this framing (the pre-mortem inversion):

> "Assume this decision has already caused a production incident or a painful refactor. Your job is to explain what went wrong. Return only questions — no verdicts, no fixes, no approvals. Every question must cite the specific finding (file path, search result, or calculation) that motivated it. Questions without citations are rejected."

This framing shifts the cognitive task from "find problems with this plan" to "explain why this plan failed" — which surfaces deeper assumptions than direct critique.

Each agent must:
1. Research the domain (HN, docs, codebase) independently before forming any question
2. Return **only questions** — no verdicts, no fixes, no approvals
3. Limit output to 3–5 substantial questions
4. **Cite the specific finding that motivated each question** — a file path, a search result, a concrete calculation. A question without a citation is rejected.

### 4. Consolidate and answer

Collect all questions. For each:
- If the question reveals the proposal was wrong → revert or redesign; run Feedback Pattern for the replacement
- If the question reveals a missed optimization → evaluate; add to `TODO.md` if worth pursuing
- If the question can be answered and the answer validates the proposal → record the rationale (code comment, TODO note, or CLAUDE.md gotcha)
- If the question surfaces a rejected alternative → add to `.claude/archive/REJECTED.md` with the reason it was ruled out

### 5. Record outcomes

There are three possible outcomes per proposal:

| Outcome | Action |
|---------|--------|
| **Holds up** — all questions answered, rationale validated | Record key rationale where the decision lives; done |
| **Needs adjustment** — a better version exists | Make the adjustment; re-prod if the change is non-trivial |
| **Wrong call** — a question reveals a fundamental problem | Revert; run Feedback Pattern before proceeding |

---

## Question Quality Bar

Reject any question that:
- Can be answered by reading the existing code without judgment ("did you consider X?" where X is already in the file)
- Is stylistic rather than substantive ("should this be a function or a method?")
- Is a request for justification of something already obvious from context
- Is a restatement of the proposal ("are you sure you want to do this?")

A good prod question has the form: **"If [assumption], then [consequence] — is [assumption] actually true, and if not, what changes?"**

Examples of substantial questions (from the session that produced this pattern):
- "This block is identical across 11 files except for domain nouns — is the per-file tailoring load-bearing, or is it cosmetic differentiation that doesn't justify the maintenance cost?"
- "CLAUDE.md is already loaded into every agent context. What loading gap does per-file duplication fill that a single CLAUDE.md directive wouldn't?"
- "The 'never optional' closing rule appears in a file for a use case (chinese-translator + HN) where the probability of a relevant result is near zero — does a hard 'never optional' rule make sense when the cost/benefit is this asymmetric?"

---

## Relationship to Other Patterns

| Pattern | When | Direction | Output |
|---------|------|-----------|--------|
| Feedback Pattern | Before a decision | Prospective | Locked decisions → TODO tasks |
| Prod Pattern | After a decision (or pre-commit) | Retrospective / adversarial | Questions → validated rationale or redesign |

The Prod Pattern feeds into the Feedback Pattern when the outcome is "Wrong call" — prodding reveals the problem, the Feedback Pattern drives the replacement decision.

---

## Reference

Used in: `CLAUDE.md` (Research before implementation), `.claude/archive/REJECTED.md` (record alternatives ruled out during prodding).
