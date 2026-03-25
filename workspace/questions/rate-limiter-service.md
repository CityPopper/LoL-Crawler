# Rate Limiter Service

_Created: 2026-03-25_

## Context

All external API calls (Riot API, op.gg, future sources like u.gg) currently use separate, disconnected rate limiting logic:
- Riot API: Lua dual-window rate limiter in `lol-pipeline-common/src/lol_pipeline/rate_limiter.py` + `_rate_limiter_data.py`
- op.gg: planned 1 req/s limit in the waterfall design (via `try_token()`)
- Future sources: no mechanism

User requirement: unified rate-limiting service with endpoint-specific throttling; rip out existing code and replace. Tests must also respect rate limiting.

## Questions

### Architecture

**[H-1] Communication protocol — BLOCKING design**
Options:
- **Redis-native** (extend existing): all services call Redis Lua scripts directly; rate limit configs loaded from a Redis key rather than hardcoded. Zero new containers, zero added latency per API call. Easy migration from existing `wait_for_token()` / `try_token()`.
- **Standalone HTTP/gRPC microservice**: new container; services make an HTTP call before every outbound API request. Language-agnostic, independently observable/scalable, but adds one network hop per Riot/op.gg call and is a new single point of failure.

Status: ⏳ Awaiting human answer

---

### Agent-resolvable (proceeding immediately)

**[A-1] What to rip out vs. keep**
Audit existing rate limiting code: `rate_limiter.py`, `_rate_limiter_data.py`, Riot API header parsing in `riot_api.py`, op.gg 1 req/s planned in waterfall design. What is safe to remove vs. must be adapted?
→ architect + developer agents

**[A-2] Endpoint-specific throttling interface**
What does the API look like? Per-source (riot, opgg), per-endpoint (matchv5, summoner), or per-key-type (short-window, long-window)?
→ architect agent

**[A-3] Test isolation**
How do tests respect rate limiting without making real API calls and without needing a running rate limiter service? Current pattern: fakeredis + mock HTTP. New pattern: ?
→ tester agent

**[A-4] Waterfall design impact**
The waterfall design (`workspace/design-source-waterfall.md`) specifies `try_token()` against the existing Lua rate limiter. If the rate limiter becomes a service, how does `try_token()` change?
→ architect agent

---

## Locked Decisions

_(empty — populated after H-1 answered)_

---

## Implementation Tasks

_(empty — moved to TODO.md after decisions locked)_
