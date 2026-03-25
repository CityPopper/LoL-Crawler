# Parallel TDD Pattern

A three-phase workflow where the main agent writes an interface contract first, then `developer` and `tester` are spawned simultaneously — developer implements, tester writes black-box tests, both against the same contract. Reconciliation merges the outputs.

Not a replacement for sequential TDD — a complement for the right class of task.

---

## When to Use vs Sequential

| Use Parallel | Stay Sequential |
|---|---|
| Modifying or extending an **existing** function | Creating a **new** module (interface undefined) |
| Behavior is unambiguous in the spec | Multiple valid interpretations need human input |
| Task has 3+ distinct test scenarios | Single-scenario tasks (overhead > savings) |
| Test coverage gap work on existing code | New features with new Redis key schemas |

---

## Phase 1 — Interface Spec (main agent, sequential)

Before spawning parallel agents, the main agent produces an interface spec file at `_spec_{task_slug}.py` in the service root. Three parts:

**a) A `typing.Protocol` class** — machine-verifiable by mypy:

```python
from typing import Protocol
from redis.asyncio import Redis
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.config import Config

class CrawlHandler(Protocol):
    async def _crawl_player(
        self, r: Redis, riot: RiotClient, cfg: Config,
        msg_id: str, env: MessageEnvelope,
    ) -> None: ...
```

**b) Behavioral docstring** — what the tester reads to derive test cases without seeing implementation:

```
Behavior:
  - Fetches match IDs from Riot API with pagination
  - Early exit: if ALL match IDs on a page already exist in
    player:matches:{puuid} (ZSCORE check), stop pagination
  - Publishes only unseen match IDs to stream:match_id
  - Always sets player:{puuid}.last_crawled_at on exit
  - Logs "early exit" at INFO level, including the page number

Error handling:
  - AuthError → propagates (caller sets system:halted)
  - RateLimitError → nack_to_dlq with retry_after_ms
  - NotFoundError → log and return, no publish
```

**c) A `NotImplementedError` stub** — lets the tester import and type-check without seeing implementation:

```python
async def _crawl_player(r, riot, cfg, msg_id, env):
    raise NotImplementedError
```

The spec file must pass `mypy --strict` before Phase 2 begins.

---

## Phase 2 — Parallel Fork

Spawn both agents simultaneously with `isolation: "worktree"`.

**Tester prompt template:**

> You are in black-box mode. Read the interface spec at `{spec_path}`. Do NOT read the implementation file `main.py`. Write pytest tests covering: {scenario list from TODO task}. Import from the stub. Use fixtures in `tests/conftest.py`. Assert on behavioral outcomes (Redis state, stream contents) — never on internal method calls. Confirm each test fails against the stub with `NotImplementedError`, not `ImportError` or `TypeError`. A wrong failure reason means the spec or test is broken.

**Developer prompt template:**

> You are in implementation-only mode. Read the interface spec at `{spec_path}`. Do NOT read test files. Implement the function in `{module_path}` to satisfy the Protocol and behavioral docstring. Do not write or modify any test files. Run `mypy src/` before returning to confirm type compatibility. Then apply your Code Review checklist (`## Code Review` in your agent file) and fix any violations before returning.

---

## Phase 3 — Reconciliation (main agent, sequential)

1. Merge tester's test files into developer's worktree
2. Run `mypy lol-pipeline-{service}/src/` — catches interface mismatches at the type level
3. Run `pytest lol-pipeline-{service}/tests/unit/ -v` — catches behavioral mismatches
4. Classify each failure:

| Failure type | Symptom | Fix |
|---|---|---|
| Interface mismatch | `ImportError`, `TypeError`, wrong arg count | Update spec; re-spawn the diverging agent |
| Behavioral disagreement | Both outputs reasonable but incompatible | Main agent picks correct interpretation per architecture docs; losing agent adjusts |
| Genuine bug | Test asserts correct behavior, implementation wrong | Re-spawn developer with failing test output |

5. Max 2 reconciliation rounds. If unresolved, fall back to sequential TDD.

---

## Phase 4 — Refactor (developer, sequential)

Developer refactors the implementation while keeping all tests green. Tester does not participate in this phase.

---

## Reference

Related patterns: `docs/patterns/feedback-pattern.md` (design decisions before implementation), `docs/patterns/prod-pattern.md` (challenge decisions after implementation).
