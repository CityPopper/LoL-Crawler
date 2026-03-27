# RATE-DYN — Dynamic Rate Limiter Feature Design (v3)

## Agreed decisions

| # | Decision |
|---|---|
| 1 | Option B — dynamic discovery ported into our Python service (not replace) |
| 2 | OP.GG in scope; uses static defaults (no headers), halving on 429 |
| 3 | Priority decoupled from stream — numeric weight adjusts retry polling interval |
| 4 | Rate limiter is **generic**: domains are config, not hardcoded code paths |
| 5 | Two domain kinds: `header_aware` (learns limits from response headers) and `static` (uses configured defaults) |
| 6 | `api_key` deferred — rate-limiter should not hold credentials it does not use; future proxy feature |
| 7 | Riot is NOT a special case — just another registered domain |
| 8 | UI/pipeline budget split is advisory (soft); see "UI Reservation" section |
| 9 | Cooling-off is always domain-level, not sub-bucket-level |
| 10 | `/status` auth-exempt; observability > security since it exposes no secrets |

---

## Core model: Domain Registry

A **domain** is any external service the pipeline calls. Each domain has:

```python
@dataclass
class Domain:
    name: str                # "riot:americas", "opgg", "some-future-api"
    short_limit: int         # default short-window cap (env-configured or header-discovered)
    short_window_ms: int
    long_limit: int          # default long-window cap
    long_window_ms: int
    header_aware: bool       # True → limits updated from response headers via POST /headers
    has_method_limits: bool  # True → use LUA_RATE_LIMIT_METHOD_SCRIPT (8 keys)
    ui_pct: float            # fraction of long budget reserved for UI (0.0–1.0); 0 = no split
```

**No `api_key` field.** The rate-limiter does not hold credentials. If a proxy layer is added
later, it will manage its own key storage.

**`has_method_limits`** replaces the current `if source.startswith("riot")` branch in
`main.py`. When `True`, `POST /token/acquire` uses `LUA_RATE_LIMIT_METHOD_SCRIPT` (8 KEYS)
to check both app-level and per-endpoint buckets atomically. When `False`, it uses
`LUA_RATE_LIMIT_SCRIPT` (4 KEYS). This is the generic equivalent — no per-domain branching.

**Domain name validation:** Names must match `^[a-z0-9:_-]+$`. Validated at startup; rejected
with a clear error on mismatch (crash-fast — do not register an invalid domain).

Domains are registered at startup from environment variables using a structured naming
convention (no hardcoded `if source == "riot":` branches anywhere).

### Example env config (new scheme)
```
# Domain: riot:americas (header_aware, method-level limits)
DOMAIN_RIOT_AMERICAS_SHORT_LIMIT=18
DOMAIN_RIOT_AMERICAS_LONG_LIMIT=90
DOMAIN_RIOT_AMERICAS_HEADER_AWARE=true
DOMAIN_RIOT_AMERICAS_HAS_METHOD_LIMITS=true
DOMAIN_RIOT_AMERICAS_UI_PCT=0.0

# Domain: opgg (static, no method-level limits)
DOMAIN_OPGG_SHORT_LIMIT=2
DOMAIN_OPGG_LONG_LIMIT=240
DOMAIN_OPGG_HEADER_AWARE=false
DOMAIN_OPGG_HAS_METHOD_LIMITS=false
DOMAIN_OPGG_UI_PCT=0.20

# A future third-party API
DOMAIN_SOMEAPI_SHORT_LIMIT=5
DOMAIN_SOMEAPI_LONG_LIMIT=300
DOMAIN_SOMEAPI_HEADER_AWARE=false
DOMAIN_SOMEAPI_HAS_METHOD_LIMITS=false
DOMAIN_SOMEAPI_UI_PCT=0.0
```

All domains share the same code paths — no per-domain conditional branches.

---

## API (all generic — no domain-specific endpoints)

### `POST /token/acquire`
```json
{"domain": "riot:americas", "endpoint": "match", "priority": 0, "is_ui": false}
```
- `priority`: integer weight (default 0); higher = shorter retry wait (see DYN-3)
- `is_ui`: draws from UI reservation of the domain budget
- Returns `{"granted": true}` or `{"granted": false, "retry_after_ms": N}`
- When denied, `retry_after_ms` is **scaled by priority** (see DYN-3 section below)

### `POST /headers`
```json
{"domain": "riot:americas", "rate_limit": "20:1,100:120", "rate_limit_count": "5:1,40:120"}
```
- No-op (200 OK) for `header_aware=false` domains
- Parses limits, stores in `ratelimit:{domain}:limits:short/long` with 1-hour TTL
- After TTL expiry, Lua falls back to config defaults (self-healing)

### `POST /cooling-off`
```json
{"domain": "opgg", "delay_ms": 5000}
```
- Sets **domain-level** cooling-off key `ratelimit:{domain}:cooling_off`
  (not per sub-bucket — both pipeline and UI sub-buckets check the same domain-level key;
  this prevents UI traffic from bypassing a 429)
- Also halves stored limits and sets `ratelimit:{domain}:halved = 1`

### `POST /cooling-off/reset`
```json
{"domain": "opgg"}
```
- Clears halved flag, resets limits to header-discovered value or config default

### `GET /status`
```json
{
  "domains": {
    "riot:americas": {"short_limit": 18, "long_limit": 90, "halved": false, "halve_count": 0, "header_aware": true, "has_method_limits": true},
    "opgg":          {"short_limit": 1,  "long_limit": 60, "halved": true,  "halve_count": 2, "header_aware": false, "has_method_limits": false, "halved_at": 1743105600}
  }
}
```

**Security:**
- `api_key` is explicitly excluded from any serialized domain object returned by `/status`
  (future-proof even if the field is added later)
- `/status` stays auth-exempt — it exposes only operational counters, no secrets

---

## 429 Halving logic

On `POST /cooling-off`:
1. Read `ratelimit:{domain}:limits:short` and `ratelimit:{domain}:limits:long`
   (fall back to domain config defaults if not yet set)
2. `new_short = max(1, current_short // 2)`, same for long
3. **Wrap the SET operations in `MULTI/EXEC`** (Redis pipeline with `transaction=True`)
   to prevent torn reads of the short/long pair:
   ```python
   async with r.pipeline(transaction=True) as pipe:
       pipe.set(f"ratelimit:{domain}:limits:short", new_short, ...)
       pipe.set(f"ratelimit:{domain}:limits:long", new_long, ...)
       await pipe.execute()
   ```
   The Lua script's two-key read is already atomic; the write side must match.
4. `SET ratelimit:{domain}:halved 1` (no TTL)
5. `INCR ratelimit:{domain}:halve_count`
6. `SET ratelimit:{domain}:halved_at {epoch_s}` (no TTL)
7. Cooling-off key set as before (`ratelimit:{domain}:cooling_off`, domain-level)

### Auto-recovery by domain kind

| Domain kind | Halved-limit TTL | Recovery mechanism |
|---|---|---|
| `static` | **1800s (30 min)** | After TTL expiry, Lua falls back to config defaults automatically |
| `header_aware` | No TTL needed | Self-heals via `POST /headers` — each successful API response writes fresh limits with 1-hour TTL; halved values are overwritten on the next header update |

For `static` domains, the halving SET operations include `ex=1800`:
```python
pipe.set(f"ratelimit:{domain}:limits:short", new_short, ex=1800)
pipe.set(f"ratelimit:{domain}:limits:long", new_long, ex=1800)
```
After 30 minutes without further 429s, the keys expire and the Lua script falls back to the
configured defaults. No manual intervention or reset endpoint call required.

The Lua script already reads `ratelimit:{domain}:limits:short/long` and uses them —
halved limits take effect on the next token request with no code changes to the Lua script.

---

## UI Reservation (generic)

For domains with `ui_pct > 0`:
- Long budget split: `ui_long = floor(long_limit * ui_pct)`, `pipeline_long = long_limit - ui_long`
- Short budget: pipeline gets full short limit; UI gets `max(1, floor(short_limit * ui_pct))`
- `is_ui=true` draws from the UI sub-bucket; `is_ui=false` draws from pipeline sub-bucket
- Redis keys: `ratelimit:{domain}:pipeline:short/long` and `ratelimit:{domain}:ui:short/long`
- Implemented in the generic token-acquire path — no per-domain branching

### Soft-split trade-off

**The budget split is advisory only.** Pipeline and UI each have their own ZSET keys, but
the combined sum is NOT enforced against the upstream limit. This means a simultaneous spike
from both sub-buckets can exceed the upstream limit by at most `ui_long` tokens. This is the
design trade-off accepted in exchange for simplicity — avoiding cross-bucket Lua coordination
or a shared parent ZSET.

In practice this is acceptable because:
- `ui_long` is small (e.g., 20 for OP.GG) relative to the upstream budget
- A brief over-limit burst triggers a 429 from the upstream, which the cooling-off mechanism
  catches and applies domain-wide (both sub-buckets respect the same cooling-off key)

### Domain-level cooling-off

Both pipeline and UI sub-buckets check the same `ratelimit:{domain}:cooling_off` key
(domain-level, not sub-bucket-level). When upstream returns 429:
- The cooling-off key is set at the domain level
- Both `is_ui=true` and `is_ui=false` requests are blocked
- This prevents UI traffic from bypassing a 429 that the pipeline triggered (or vice versa)

---

## Priority-Weighted Retry Hints (DYN-3)

### Model

Priority ordering is achieved through **differential polling frequency** — no server-side
queues, no held connections, no in-process state. The mechanism:

1. `POST /token/acquire` accepts an optional `priority: int` (default 0)
2. When denied, the server scales `retry_after_ms` inversely with priority:
   ```python
   scaled_wait = max(10, int(base_wait * max(0.1, 1.0 - priority / 200.0 * 0.9)))
   ```
3. `wait_for_token` uses the scaled `retry_after_ms` returned in the response

### Priority effect on polling frequency

| Priority | Scale factor | Effect |
|---|---|---|
| 200 | 0.10 (10% of base wait) | Polls ~10x more often; gets token first |
| 20 | 0.91 | Slightly faster than normal |
| 0 | 1.00 | Normal polling (100% base wait) |
| -10 | 1.045 | Polls slightly less often |

### Why this works

Higher-priority callers poll more frequently, so they are statistically first to find an open
slot. Lower-priority callers poll less frequently, yielding slots to higher-priority callers
without explicit coordination.

### Properties

- **Zero state loss on restart** — no in-memory queues to drain
- **No held connections** — standard request/response; no long-poll timeouts
- **Lua atomicity unchanged** — the Lua script returns the base wait; Python scales it
- **No stream-level deferral** — `defer_message`, `has_priority_players`, and
  `_has_priority_cached` in the fetcher are removed; priority ordering emerges from
  differential polling frequency

### Priority values (unchanged from current constants)
```
PRIORITY_MANUAL_20PLUS = 200   # CLI manual seed with >20 match history
PRIORITY_MANUAL_20     = 20    # CLI manual seed
0                              # normal crawl (default)
-10                            # discovery / background
```

### Client changes
```python
# Before
await wait_for_token("riot:americas", "match")

# After — priority flows from MessageEnvelope
await wait_for_token("riot:americas", "match", priority=envelope.priority)
```

`wait_for_token` reads the `retry_after_ms` from the HTTP response and sleeps for that
duration (already scaled by the server). No client-side scaling logic needed.

---

## Files touched

| File | Sub-feature | Change |
|---|---|---|
| `lol-pipeline-rate-limiter/src/lol_rate_limiter/config.py` | all | Replace hardcoded source lists with domain registry; add `has_method_limits`, domain name validation (`^[a-z0-9:_-]+$`); remove `api_key` |
| `lol-pipeline-rate-limiter/src/lol_rate_limiter/main.py` | all | Generic endpoints; halving in cooling-off (MULTI/EXEC); UI split in acquire; `/status` expansion (exclude `api_key`); priority scaling in acquire response; domain-level cooling-off key |
| `lol-pipeline-rate-limiter/src/lol_rate_limiter/_token.py` | DYN-3 | Accept `priority` param; scale `retry_after_ms` inversely before returning; static-domain halving with TTL 1800s |
| `lol-pipeline-common/src/lol_pipeline/rate_limiter_client.py` | DYN-3 | Add `priority`, `is_ui` params to client; `wait_for_token` uses server-returned `retry_after_ms` directly |
| `lol-pipeline-fetcher/src/lol_fetcher/main.py` | DYN-3 | Pass priority from envelope; remove `defer_message`, `has_priority_players`, `_has_priority_cached` |
| `lol-pipeline-crawler/src/...` | DYN-3 | Pass priority from envelope |
| `lol-pipeline-ui/src/lol_ui/main.py` | DYN-1 | Halved banner via /status |
| `lol-pipeline-ui/src/lol_ui/templates/` | DYN-1 | Banner HTML/CSS |
| `.env` / `.env.example` | all | New DOMAIN_* scheme; OP.GG 240 long; UI 20%; no API_KEY in domain config |

## Implementation order

1. **DYN-2**: Domain registry + generic config (foundation for everything else)
   - Domain dataclass with `has_method_limits`, no `api_key`
   - Domain name validation at startup
   - Generic acquire path dispatching on `has_method_limits`
2. **DYN-1**: Halving + /status expansion + UI banner
   - MULTI/EXEC atomic halving writes
   - Static-domain halving TTL (1800s)
   - Domain-level cooling-off key
   - `/status` excludes `api_key` from serialized domains
3. **DYN-3**: Priority-weighted retry hints
   - Priority scaling formula in `_token.py`
   - Client `wait_for_token` uses server-returned `retry_after_ms`
   - Remove stream-level deferral from fetcher
