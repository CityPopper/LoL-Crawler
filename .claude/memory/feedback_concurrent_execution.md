---
name: Always execute concurrently when possible
description: For any task with independent subtasks, always spawn multiple agents or run commands in parallel — never sequential when concurrent is safe
type: feedback
---

Always find concurrent execution opportunities and use them. Never run things sequentially when they can safely run in parallel.

**Why:** User explicitly set this as a standing rule for all agents and all tasks. Sequential execution wastes time on independent work.

**How to apply:**
- Multi-service tasks: group services into batches (~50-75 tests or ~2-4 services per agent), spawn one agent per batch simultaneously
- Profile first if needed to determine batch sizes, then spawn fix agents in parallel
- Use `run_in_background: true` for agents whose results aren't needed immediately
- For shell commands: chain independent commands with `&` and `wait`, or run tool calls in parallel in a single message
- Only go sequential when a later step genuinely depends on an earlier step's output
- Before spawning a single large agent, ask: can this be split into N parallel agents?
