# Orchestrator "Think" Process

When the user says "think", execute this loop **5 times** (or as specified):

1. **All agents review the codebase** and propose improvements in `TODO.md`
2. **Break large tasks into smaller steps** in `TODO.md`
3. **Agents debate contested items.** Supermajority (>=2/3) to proceed; 3 failed rounds = scrapped
4. **Implement**, removing completed tasks as you go
5. **Repeat from step 1**

## Concurrency

Max 5 agents active at once. Spawn review agents in batches of 5; wait for each batch before starting the next. Same batching for debate threads and implementer agents.

## Step 1 — Review Agents

Agent definitions live in `.claude/agents/`. Spawn all agents found there (currently ~20) in batches of 5. Each agent reviews the codebase through its domain lens and proposes TODO items.

The `doc-keeper` agent follows the **bookend pattern** (see `CLAUDE.md`): runs sequentially before and after implementation agents, never in parallel with them.

## Step 4 — Implementation

Use the `developer` agent (`.claude/agents/developer.md`) for all implementation. Spawn one per independent task/file group, up to 5 in parallel.

## Debate Protocol (Step 3)

For contested items: spawn one debate thread per item (up to 5 parallel). Supermajority vote (>=2/3) to proceed. After 3 failed rounds, scrap the item and log it in `.claude/archive/REJECTED.md`.

Review agents **must read `REJECTED.md` first** — duplicates of rejected items are auto-scrapped without debate. See `.claude/archive/REJECTED.md` for format and existing entries.
