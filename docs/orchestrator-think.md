# Orchestrator "Think" Process

When the user says "think" (or tells the orchestrator to "think"), execute this loop **5 times**:

1. **All agents review the entire codebase** and present improvements they would like in `TODO.md`
2. **Break large/complex tasks into smaller steps** in `TODO.md`
3. **All agents review the proposed TODOs and can present objections.** For each contested item, agents debate and propose compromises. If supermajority (>=2/3) is reached, the item proceeds. If not, send it back for another round of debate. After **3 failed debate rounds** on the same item, scrap it entirely and move on.
4. **Implement the steps**, removing completed tasks as you go
5. **Once all tasks are done**, repeat from step 1

## Concurrency

Run **all agents in parallel** simultaneously — maximize parallelism at every step.

- In step 1: spawn one agent per service/domain area concurrently (reviewer, security, performance, database, devops, etc.)
- In step 3: spawn debate agents in parallel; each contested item runs its own debate sub-thread concurrently
- In step 4: spawn one implementer agent per task/file concurrently; do not serialize implementation
- Never wait for one agent to finish before starting another if they are independent
- Aim for the maximum number of concurrent agents the system supports at each step
