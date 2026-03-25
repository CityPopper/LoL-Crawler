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

**[H-1] Communication protocol — RESOLVED**
Decision: **standalone HTTP microservice**. New container; services call it over HTTP before every outbound API request.
Status: ✅ Resolved

---

### Agent-resolvable — RESOLVED

**[A-1] What to rip out vs. keep — RESOLVED**

**[A-2] Endpoint-specific throttling interface — RESOLVED**

**[A-3] Test isolation — RESOLVED**

**[A-4] Waterfall design impact — RESOLVED**

---

## Locked Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D-1 | Delete `_rate_limiter_data.py` and `rate_limiter.py` entirely | Lua script and acquire/wait logic move into the service; no client needs them |
| D-2 | Delete `test_rate_limiter.py`, `test_it07_rate_limit.py`, `test_it12_concurrent_rate_limit.py` | Coverage moves to the service's own test suite |
| D-3 | New `lol-pipeline-rate-limiter` service with FastAPI on port 8079 (internal Docker network only) | Standalone HTTP microservice per H-1 |
| D-4 | Single `POST /token/acquire` endpoint; `try_token()` calls it once, `wait_for_token()` loops with sleep | Client-side retry loop; server returns immediately with `{granted, retry_after_ms}` |
| D-5 | `POST /headers` endpoint: RiotClient reports raw rate-limit header strings; service parses + updates limits | Service is single owner of all rate-limit logic |
| D-6 | Bucket config via env vars; unknown `(source, endpoint)` → 404 (fail-loud) | All sources must be explicitly configured; no silent pass-through |
| D-7 | `riot:*` all share one global bucket; `opgg:summoner` and `opgg:games` are separate | Riot limits are per-key not per-endpoint; op.gg endpoints are independent |
| D-8 | Fail open if rate limiter unreachable (log warning, allow request) | DLQ/retry handles any resulting 429s; halting entire pipeline is worse |
| D-9 | Unit tests: `patch("module.wait_for_token", new_callable=AsyncMock)` — same pattern as today | Already dominant in 5/6 services; zero new infrastructure |
| D-10 | Integration tests: add `GenericContainer` for rate limiter alongside existing Redis container | IT-07 and IT-12 updated to go through HTTP instead of Lua EVAL |
| D-11 | `wait_for_token(source, endpoint)` and `try_token(source, endpoint)` — drop `r`, `key_prefix`, `limit_*` params | Service owns all state; clients become thin HTTP callers |
| D-12 | Fetcher pattern-A tests (real Lua via fakeredis/lupa) switch to pattern-B (AsyncMock patch) | Lua rate limiter no longer exists in-process after migration |
| D-13 | PRIN-CMN-07 (`_rate_limiter_data.py` constant divergence) resolved automatically by deletion | No divergence possible when only one copy of constants exists (in the service) |

---

## Implementation Tasks

_(see TODO.md — RL section)_
