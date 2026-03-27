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

## RATE-DYN-2 — FEATURE: Generic domain registry (rate-limiter foundation)

**Sub-feature of RATE-DYN. Implement first — DYN-1 and DYN-3 depend on this.**

Replace the hardcoded `if source == "opgg":` / `source.startswith("riot")` switch in the rate-limiter with a generic `Domain` dataclass and env-var-driven registry.

**Domain dataclass:**
```python
@dataclass
class Domain:
    name: str                # e.g. "riot:americas", "opgg"
    short_limit: int
    short_window_ms: int
    long_limit: int
    long_window_ms: int
    header_aware: bool       # True → limits updated from X-App-Rate-Limit headers
    has_method_limits: bool  # True → LUA_RATE_LIMIT_METHOD_SCRIPT (8 keys)
    ui_pct: float            # 0.0–1.0; fraction of long budget reserved for UI
```

**Env var convention:** `DOMAIN_{KEY}_{PROPERTY}` where KEY is the domain name uppercased with `:` replaced by `_` (e.g. `riot:americas` → `DOMAIN_RIOT_AMERICAS_SHORT_LIMIT=18`). Known properties: `SHORT_LIMIT`, `LONG_LIMIT`, `SHORT_WINDOW_MS`, `LONG_WINDOW_MS`, `HEADER_AWARE`, `HAS_METHOD_LIMITS`, `UI_PCT`.

**Domain name validation:** Must match `^[a-z0-9:_-]+$`; crash-fast at startup on invalid name.

**Generic acquire path:** `POST /token/acquire {"domain": ..., "endpoint": ..., "priority": 0, "is_ui": false}` dispatches to `LUA_RATE_LIMIT_METHOD_SCRIPT` when `domain.has_method_limits` else `LUA_RATE_LIMIT_SCRIPT`. No per-domain `if` branches.

**UI sub-bucket:** When `ui_pct > 0`, `is_ui=true` draws from `ratelimit:{domain}:ui:short/long`; `is_ui=false` draws from `ratelimit:{domain}:pipeline:short/long`. Split is soft (advisory only — combined sum not enforced).

**Cooling-off key:** Always `ratelimit:{domain}:cooling_off` (domain-level, shared across sub-buckets).

**Files:** `lol-pipeline-rate-limiter/src/lol_rate_limiter/config.py`, `main.py`, `.env`, `.env.example`

**TDD checklist:**
- [ ] **Red:** Write failing tests for `load_domains_from_env` (parses env vars, validates names, rejects invalid), generic acquire dispatch (method-level vs non-method-level), UI sub-bucket routing, domain-level cooling-off
- [ ] **Green:** Implement `Domain` dataclass, `load_domains_from_env`, generic acquire path in `main.py`
- [ ] **Refactor:** Remove all per-domain `if` branches; ensure no hardcoded domain names remain

---

## RATE-DYN-1 — FEATURE: 429 halving + persistent state + UI banner

**Depends on RATE-DYN-2 being merged first.**

When any domain receives a real 429 (`POST /cooling-off`), halve its stored limits atomically and persist in Redis. Show a red banner in the UI when any domain is halved.

**Halving logic (on `POST /cooling-off`):**
1. Read current stored limits (fall back to domain config if not yet set)
2. `new = max(1, current // 2)` for both short and long
3. Write both in a single `MULTI/EXEC` pipeline (atomic)
4. `static` domains: write halved limits with `ex=1800` (30-min auto-recovery)
5. `header_aware` domains: no TTL (self-heal via next `POST /headers` write)
6. Set `ratelimit:{domain}:halved = 1`, `INCR ratelimit:{domain}:halve_count`, `SET ratelimit:{domain}:halved_at {epoch_s}` (no TTL on these flags)

**Reset:** `POST /cooling-off/reset {"domain": ...}` clears halved flag + halve_count + halved_at, deletes stored limit keys (Lua falls back to config).

**`GET /status` additions:** Return per-domain `{halved, halve_count, halved_at, short_limit, long_limit, header_aware, has_method_limits}`. `api_key` MUST NOT appear in any serialized domain.

**UI banner:** On every page load, the UI calls `/status` on the rate-limiter (or the existing `/health` enriched with `halved_sources`). When any domain has `halved=true`, render a prominent red banner:
> ⚠ Rate limits halved for: {domain} ({halve_count}× — since {halved_at}). Admin can reset with `just admin rate-reset {domain}`.

**Files:** `lol-pipeline-rate-limiter/src/lol_rate_limiter/main.py`, `lol-pipeline-ui/src/lol_ui/main.py`, `lol-pipeline-ui/src/lol_ui/templates/`

**TDD checklist:**
- [ ] **Red:** Write failing tests for atomic halving (MULTI/EXEC), static-domain TTL (1800s), header_aware self-healing, reset endpoint, `/status` response shape, api_key exclusion
- [ ] **Green:** Implement halving in `POST /cooling-off`; add `POST /cooling-off/reset`; expand `/status`; add UI banner template and route logic
- [ ] **Refactor:** Extract halving logic into a helper; ensure banner renders only when halved

---

## RATE-DYN-3 — FEATURE: Priority-weighted retry hints + remove stream deferral

**Depends on RATE-DYN-2 being merged first. Can be parallelised with RATE-DYN-1.**

Add numeric priority to token acquisition. Higher priority → shorter retry wait → more frequent polling → statistically gets tokens first. Remove Redis-stream-level deferral from the fetcher.

**Priority scaling (in `_token.py` or `main.py` acquire response):**
```python
scaled_wait = max(10, int(base_wait * max(0.1, 1.0 - priority / 200.0 * 0.9)))
```
- priority=200 → 10% of base wait (polls ~10× more often)
- priority=0 → 100% of base wait (unchanged)
- priority=-10 → ~104.5% of base wait

**Client changes (`rate_limiter_client.py`):**
- `wait_for_token(domain, endpoint, *, priority=0, is_ui=False)` — adds `priority` and `is_ui` to POST body; uses `retry_after_ms` from response directly (server already scaled it)
- `try_token(domain, endpoint, *, priority=0, is_ui=False)` — same additions
- Rename `source` parameter to `domain` across both functions

**Fetcher changes (`lol_fetcher/main.py`):**
- Pass `envelope.priority` as `priority` to `wait_for_token`
- Remove `defer_message`, `has_priority_players`, `_has_priority_cached` — stream-level deferral is replaced by differential polling frequency
- Remove the `is_low_priority` / `_has_priority_cached` guard block entirely

**Crawler changes:** Pass priority from envelope to `wait_for_token`.

**Files:** `lol-pipeline-rate-limiter/src/lol_rate_limiter/_token.py`, `lol-pipeline-common/src/lol_pipeline/rate_limiter_client.py`, `lol-pipeline-fetcher/src/lol_fetcher/main.py`, `lol-pipeline-crawler/src/...`

**TDD checklist:**
- [ ] **Red:** Write failing tests for priority scaling formula (priority=200→10%, priority=0→100%, priority=-10→104.5%), client `priority`/`is_ui` param threading, fetcher no longer calls `has_priority_players`
- [ ] **Green:** Add priority scaling to acquire response; update client; update fetcher and crawler
- [ ] **Refactor:** Confirm no references to `defer_message`/`has_priority_players` remain anywhere

---
