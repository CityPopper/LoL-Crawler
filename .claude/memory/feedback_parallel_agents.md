---
name: Spawn multiple dev agents in parallel
description: For tasks spanning multiple services or files, always spawn multiple developer agents in parallel rather than one sequential agent
type: feedback
---

For any task that touches multiple services or can be decomposed into independent subtasks, spawn multiple developer agents in a single message (parallel) rather than one agent doing everything sequentially.

**Why:** User explicitly requested this to speed up execution. A single agent working across 11 services is much slower than 3-4 agents each handling a subset.

**How to apply:**
- Split work by service group, e.g. Agent 1: common/seed/crawler, Agent 2: fetcher/parser/analyzer, Agent 3: recovery/delay-scheduler/admin/discovery, Agent 4: ui
- Use `run_in_background: true` and SendMessage to coordinate if needed
- Prefer 3-5 parallel agents over 1 large agent for multi-service tasks
