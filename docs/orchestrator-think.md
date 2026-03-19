# Orchestrator "Think" Process

When the user says "think" (or tells the orchestrator to "think"), execute this loop **5 times**:

1. **All agents review the entire codebase** and present improvements they would like in `TODO.md`
2. **Break large/complex tasks into smaller steps** in `TODO.md`
3. **All agents review the proposed TODOs and can present objections.** For each contested item, agents debate and propose compromises. If supermajority (>=2/3) is reached, the item proceeds. If not, send it back for another round of debate. After **3 failed debate rounds** on the same item, scrap it entirely and move on.
4. **Implement the steps**, removing completed tasks as you go
5. **Once all tasks are done**, repeat from step 1

## Concurrency limit

Run at most **2 agents in parallel** at a time. Batch agents into groups of 2, wait for each batch to complete before launching the next, until all agents have been processed.
