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

---

## [OT1-R1] Redis TimeSeries module for player stat trends
**Proposed**: Use Redis TimeSeries (TS.ADD/TS.RANGE) to track player statistics over time with automatic compaction rules, enabling trend charts and rolling averages.
**Rejected because**: Overkill at current scale. The pipeline ingests ~0.8 req/s (rate-limited dev API key), not millions of data points per second. TimeSeries is designed for high-frequency telemetry. At the current volume (hundreds of matches per player, not thousands per second), the metadata overhead per TimeSeries key exceeds the data itself. Existing sorted sets (`player:matches:{puuid}` scored by `game_start`) already support time-windowed queries. Requires upgrading to Redis 8.0+ (currently on 7-alpine), adding container/Lua compatibility risk for speculative value.
**Do not re-propose**: "Redis TimeSeries for stats", "TS.ADD for player trends", "time-series module for analytics", "automatic compaction rules for player data".

---

## [OT1-R2] Elasticsearch as secondary analytical store
**Proposed**: Add Elasticsearch alongside Redis to handle complex analytical queries (e.g., "all matches where champion X was played mid in patch 14.5") that Redis hashes and sorted sets cannot express efficiently.
**Rejected because**: Adds operational complexity with no demonstrated query need. The current Redis key schema (`champion:stats:{name}:{patch}:{role}`, `matchup:{A}:{B}:{position}:{patch}`) already serves every analytical query the UI performs. ES would require cluster management, index mappings, sync lag handling, and disk I/O — doubling infrastructure for a single-developer project. The 12-factor single-dependency simplicity of Redis-only is a deliberate architectural choice (documented in `09-design-comparison.md`).
**Do not re-propose**: "add Elasticsearch", "secondary analytical store", "full-text search on match data", "Kibana dashboards for match analytics".

---

## [OT1-R3] Vue.js / React SPA frontend
**Proposed**: Replace the FastAPI server-rendered HTML UI with a modern SPA frontend (Vue.js + Tailwind or React) for richer interactivity, filtering, and sorting.
**Rejected because**: Wrong time for a solo developer. The current server-rendered approach (2,596 LOC in `main.py` with inline CSS/JS) is crude but functional and requires zero build toolchain. An SPA would require: API endpoint extraction from the monolithic handler, CORS configuration, a Node.js build pipeline (vite/webpack), and maintaining two codebases (Python API + JS frontend). The maintenance burden outweighs the UX improvement for a personal tool with 1-2 users.
**Do not re-propose**: "SPA frontend", "React/Vue dashboard", "separate frontend app", "client-side rendering".

---

## [OT1-R4] MCP server to expose pipeline data to LLMs
**Proposed**: Expose the pipeline's Redis data as an MCP (Model Context Protocol) server so LLMs like Claude can query player stats and matchups conversationally.
**Rejected because**: Premature. Prerequisites not met: (1) no stable REST/GraphQL API surface exists — the UI renders HTML directly from Redis queries, (2) no authentication layer beyond the Riot API key, (3) insufficient data volume to make LLM queries meaningful. Building an MCP server on top of an unstable internal API creates a fragile integration. Revisit after the pipeline has a stable external API and has accumulated months of historical data.
**Do not re-propose**: "MCP server for pipeline", "LLM integration for stats", "expose Redis data to Claude/GPT", "conversational analytics API".

---

## [OT1-R5] Full OpenTelemetry distributed tracing
**Proposed**: Add OpenTelemetry SDK with `traceparent`/`tracestate` propagation in `MessageEnvelope`, deploy a Jaeger/Zipkin collector, and instrument all services for end-to-end trace visualization.
**Rejected because**: Overkill for a single-developer project. OTel requires: manual context propagation in every producer/consumer (Redis Streams has no native trace header support), a new `traceparent` field in the envelope (touching all PACT schemas), and a collector container (~500MB RAM — a 50% increase in infrastructure). Pipeline spans are minutes-to-hours long (due to rate limiting), which most tracing UIs handle poorly. A lightweight correlation ID (single UUID propagated through the pipeline, logged in structured JSON) provides 80% of the debugging value at 10% of the cost.
**Do not re-propose**: "OpenTelemetry tracing", "Jaeger/Zipkin integration", "distributed tracing with spans", "OTel instrumentation for Redis Streams".

---

## [OT1-R6] Per-service circuit breakers (aiobreaker/pybreaker)
**Proposed**: Add circuit breaker libraries (aiobreaker or pybreaker) to each service for transient Riot API failure isolation, beyond the global `system:halted` flag.
**Rejected because**: Already covered by existing multi-layer failure handling. `RiotClient` in `riot_api.py` already has a circuit breaker (5 consecutive 5xx → 30s open state). `run_consumer` in `service.py` has 3-tier failure handling (in-process retry → DLQ escalation → sleep-and-retry on RedisError). The delay scheduler has its own per-member circuit breaker (`_circuit_open`, `_MAX_MEMBER_FAILURES=10`, `_CIRCUIT_OPEN_TTL_S=300`). Adding per-service circuit breakers would create overlapping failure domains. Risk: a per-service breaker could mask a 403 that should trigger `system:halted`, preventing the global halt from propagating. Additionally, aiobreaker is unmaintained (no PyPI release in 12+ months).
**Do not re-propose**: "per-service circuit breakers", "aiobreaker integration", "pybreaker for API resilience", "circuit breaker per worker".

---

## [OT1-R7] Priority-aware rate limit token allocation
**Proposed**: Modify the dual-window Lua rate limiter to allocate tokens preferentially to high-priority messages (manual seeds) over normal-priority messages (discovery backfill).
**Rejected because**: Negligible benefit at current scale. At 20 req/s with 1-10 workers, the contention window is small — a high-priority request waits at most one 50ms polling cycle for a token, which is imperceptible. Priority is already handled at the consumer level (`service.py` sorts batches by `PRIORITY_ORDER`), affecting processing order rather than API call order. Making the Lua rate limiter priority-aware would require separate token buckets or weighted queuing, adding complexity to the most performance-critical code path. Starvation prevention would add further complexity.
**Do not re-propose**: "priority-aware rate limiter", "weighted token allocation", "priority queue for API calls", "separate rate limit buckets by priority".

---

## [OT1-R8] Redis 8.x upgrade and XACKDEL adoption
**Proposed**: Upgrade from `redis:7-alpine` to Redis 8.2+ to adopt `XACKDEL` (atomic acknowledge-and-delete) for simplified stream entry lifecycle, and `XTRIM ... ACKED` for safe compaction.
**Rejected because**: Too early for production adoption. Redis 8.x is very new (released 2025-2026). The marginal benefit of XACKDEL (replacing separate XACK + eventual MAXLEN trim) does not justify an early-adopter major version upgrade. The atomic DLQ replay already uses a Lua script (`_REPLAY_LUA`) for the critical path. Existing `redis-py` library support for 8.x features may be incomplete. Wait until Redis 8.x is widely deployed and the Python client fully supports new commands.
**Do not re-propose**: "upgrade to Redis 8", "XACKDEL adoption", "XTRIM ACKED", "Redis 8 stream features".

---

## [OT1-R9] NATS JetStream as Redis Streams replacement
**Proposed**: Replace Redis Streams with NATS JetStream for message delivery, keeping Redis only for data storage. NATS offers built-in exactly-once delivery, consumer acknowledgment, and replay.
**Rejected because**: Adding a second infrastructure component when Redis handles everything is the wrong direction. The pipeline already depends on Redis for data storage (hashes, sorted sets, strings), rate limiting (sorted sets + Lua), delayed messages (sorted set), and streams. Running NATS alongside Redis doubles operational complexity for a single-developer project. The design comparison document (`09-design-comparison.md`) correctly identifies Redis Streams as the right fit for a single-API, rate-limited, 1-10 worker scale.
**Do not re-propose**: "NATS JetStream", "replace Redis Streams with NATS", "separate message broker", "Kafka/NATS/RabbitMQ migration".

---

## [OT1-R10] Win prediction ML model from champion select
**Proposed**: Train a machine learning model to predict match outcomes from champion draft composition, using stored matchup and champion stats data.
**Rejected because**: Wrong product for this pipeline. Draft-only prediction caps at ~56% accuracy (LoLDraftAI benchmark) — barely above coin flip. The 87.9% accuracy claims (LeagueOfPredictions) include player history features, not just champion composition. The pipeline's curated dataset (seeded players, specific ranks/regions) is biased and too small for generalizable model training. Win prediction requires a separate ML pipeline (feature engineering, model training, validation, serving, per-patch retraining) — a fundamentally different discipline from data pipeline engineering. The pipeline does not operate during champion select, so predictions have zero actionable value.
**Do not re-propose**: "win prediction model", "ML for match outcomes", "draft win probability", "train model on match data".

---

## [OT1-R11] Smurf / anomaly detection system
**Proposed**: Detect smurf accounts by comparing player performance metrics against rank-bracket expected values, flagging statistical outliers.
**Rejected because**: Zero value for a solo user — you know if you are smurfing. Proper detection requires rank population distributions (per-rank average KDA, CS/min, win rate) that the pipeline does not collect (it stores individual `player:rank:{puuid}` hashes, not aggregate rank statistics). Without a reference population, "anomaly" is undefined. False positive risk is high (naturally improving players look identical to smurfs). The "smurf" label is loaded — if ever revisited, frame as "performance outlier" flags instead.
**Do not re-propose**: "smurf detection", "anomaly detection system", "identify boosted accounts", "rank vs performance mismatch detector".
