# TODO — Open Work Items

---

## RL-PROXY-1 — FEATURE: Rate-limiter as HTTP proxy (cosmic-radiance model)

**Severity:** High — architectural — prevents cascading 429s at the root

**Motivation:** Today the fetcher calls `wait_for_token()` before making Riot API calls directly. If the rate-limiter HTTP service is briefly unreachable (e.g. on restart), `wait_for_token` **fails open** — all N fetcher workers fire simultaneously → Riot rate-limits all of them → thousands of DLQ entries. The cooling-off mechanism mitigates cascades *after* the first 429, but does not prevent the initial burst from a fail-open startup race.

The root fix is to make the rate-limiter the **sole caller** of the Riot API. Fetchers never call Riot directly; they send fetch requests to the rate-limiter service, which queues them, throttles, fires, and returns the response. A down rate-limiter returns 503 → fetchers defer → no direct Riot traffic is possible without the rate-limiter being healthy.

**Reference implementation:** [cosmic-radiance](https://github.com/DarkIntaqt/cosmic-radiance) — Go HTTP proxy, single-goroutine main loop, per-(platform × endpoint) ring-buffer queues, proactive time-spread, multi-key rotation, priority lanes.

**RL-PROXY-1c ✅ DONE** — Fail-closed `wait_for_token` retry loop. `RATE_LIMITER_CONNECT_RETRIES` env var (default 3).

**Remaining sub-tasks:**

- **RL-PROXY-1a:** Add method-level buckets — `ratelimit:{source}:{endpoint}:short/long`. Prevents match-v5 flood from starving summoner-v4.
- **RL-PROXY-1b:** Add proactive time-spreading to Lua: only grant if `count <= elapsed_ms / window_ms * limit`.
- **RL-PROXY-1d:** Full proxy endpoint + migrate `RiotClient` to route through it.

**Proposed architecture (RL-PROXY-1d):**

```
Fetcher → POST http://rate-limiter/proxy/fetch
              { region, path, priority?, correlation_id }
          ← blocks (long-poll, up to 90s)
          ← { status_code, body, headers }

Rate-limiter /proxy/fetch:
  - Enqueues into per-(region × endpoint) priority queue
  - Main loop: time-spread dequeue → fire Riot → return response
  - On 429: set LockedUntil for (region × endpoint), return 429
  - On success: extract X-App-Rate-Limit headers, update stored limits inline
```

---
