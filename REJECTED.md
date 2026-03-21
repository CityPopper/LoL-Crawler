# Rejected Proposals

Items scrapped during the orchestrate-think debate phase. Review agents **must read this file before proposing new items**. Do not re-propose anything listed here.

---

## [CY1-D1] Make Delay Scheduler XADD+ZREM atomic via Lua script
**Proposed**: Wrap the XADD and ZREM in a single Lua script to make delayed message delivery atomic.
**Rejected because**: The non-atomicity is intentional. The design guarantees at-least-once delivery: if the process crashes between XADD and ZREM, the message stays in the sorted set and is re-delivered on the next tick. Making it atomic would create a window where the message is neither in the sorted set nor in the stream (exactly-once, but with a drop risk). The existing code has a comment documenting this intentional design.
**Do not re-propose**: Any form of "make delay-scheduler XADD+ZREM atomic", "Lua script for delayed dispatch", "transactional delayed delivery".

---

## [CY1-D2] Remove `system:halted` checks from individual handlers (DRY violation)
**Proposed**: The `system:halted` check appears in every message handler — extract it to a single wrapper/decorator to eliminate the duplication.
**Rejected because**: Defense-in-depth. A halt can be SET between the `run_consumer` loop check and the actual handler execution. Each handler checking independently ensures that a halt issued mid-batch is respected immediately, not just at the next loop iteration. This is an intentional multi-layer safety pattern, not accidental duplication.
**Do not re-propose**: "DRY up halted checks", "centralize system:halted guard", "move halt check to run_consumer", "decorator for halted state".

---

## [I2-D1] Swap Delay Scheduler Lua script order to ZREM-then-XADD
**Proposed**: Within the existing `_DISPATCH_LUA` script, do ZREM first so concurrent scheduler instances cannot both XADD (producing duplicates).
**Rejected because**: ZREM-then-XADD creates a message-loss window: if the process crashes after ZREM but before XADD, the message is permanently lost (already ACKed from stream:dlq by Recovery). The current XADD-then-ZREM ordering produces at-worst duplicate delivery, which downstream idempotency handles. The architecture docs at `03-streams.md:148` and `06-failure-resilience.md:47` explicitly document this as the intended failure mode. The multi-instance duplicate concern is a deployment error, not a code bug; the delay scheduler is a single-instance service by design.
**Do not re-propose**: "Swap XADD/ZREM order in delay scheduler Lua", "ZREM-first for atomic dispatch", "prevent multi-instance duplicates by reversing Lua operation order".

---

## [CY1-D3] Extract `seeded_at` hset into a shared helper function in common
**Proposed**: Several services write `seeded_at` to the same Redis hash field — extract a shared `write_seeded_at(r, puuid)` function into `lol-pipeline-common`.
**Rejected because**: Coupling risk exceeds duplication cost. Services must only know their own input/output contracts (per CLAUDE.md service isolation directive). A shared helper would create an implicit cross-service dependency on a field name constant. Using a shared constant (e.g., `SEEDED_AT_FIELD = "seeded_at"`) in common is acceptable, but a shared write function is not.
**Do not re-propose**: "shared seeded_at writer", "common helper for player hash fields", "extract Redis write helpers to common".
