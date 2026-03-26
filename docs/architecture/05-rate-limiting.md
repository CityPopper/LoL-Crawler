# Rate Limiting

## Design

One global sliding-window rate limiter shared across all worker processes via Redis.
Enforces Riot dev-key limits, with a configurable per-second cap:

| Window   | Limit                      | Redis Key         | Configurable?    |
|----------|----------------------------|-------------------|------------------|
| 1 second | `API_RATE_LIMIT_PER_SECOND` (default: 20) | `ratelimit:short` | Yes — env var |
| 2 minutes| 100 req/2m (Riot hard cap) | `ratelimit:long`  | No               |

Set `API_RATE_LIMIT_PER_SECOND` in `.env` to throttle below Riot's limit (e.g., when sharing
an API key across multiple deployments, or for conservative backfill).

Both windows are checked **atomically** in a single Lua script with four KEYS. This prevents
a race condition where two workers each pass one window check but together exceed the limit.

Any service that calls the Riot API must call `acquire_token()` before each request.
Workers that cannot acquire a token calculate the required sleep time and retry; they do
**not** hold a stream ACK while sleeping.

---

## Lua Script

```lua
-- KEYS[1] = "ratelimit:short"        (1-second sliding window ZSET)
-- KEYS[2] = "ratelimit:long"         (2-minute sliding window ZSET)
-- KEYS[3] = "ratelimit:limits:short" (dynamic short limit — written by RiotClient)
-- KEYS[4] = "ratelimit:limits:long"  (dynamic long limit — written by RiotClient)
-- ARGV[1] = now_ms                   (current epoch in milliseconds)
-- ARGV[2] = short_limit_fallback     (20)
-- ARGV[3] = long_limit_fallback      (100)
-- ARGV[4] = short_window_ms          (1000)
-- ARGV[5] = long_window_ms           (120000)
-- ARGV[6] = uid                      (UUID — unique per call)
--
-- Returns: 1 (token granted) or negative int whose absolute value is
-- estimated wait ms until next slot opens (denial)
--
-- Dynamic limits: reads ratelimit:limits:short / ratelimit:limits:long
-- (written by RiotClient after each API response). Falls back to ARGV
-- values when no stored limits exist.

local key_s = KEYS[1]
local key_l = KEYS[2]
local now     = tonumber(ARGV[1])
local win_s   = tonumber(ARGV[4])
local win_l   = tonumber(ARGV[5])
local uid     = ARGV[6]

local stored_s = redis.call("GET", KEYS[3])
local stored_l = redis.call("GET", KEYS[4])
local limit_s = (stored_s and tonumber(stored_s)) or tonumber(ARGV[2])
local limit_l = (stored_l and tonumber(stored_l)) or tonumber(ARGV[3])
if limit_s < 1 then limit_s = tonumber(ARGV[2]) end
if limit_l < 1 then limit_l = tonumber(ARGV[3]) end

redis.call("ZREMRANGEBYSCORE", key_s, "-inf", now - win_s)
redis.call("ZREMRANGEBYSCORE", key_l, "-inf", now - win_l)

local count_s = redis.call("ZCARD", key_s)
local count_l = redis.call("ZCARD", key_l)

if count_s >= limit_s or count_l >= limit_l then
    local wait_s = 0
    local wait_l = 0
    if count_s >= limit_s then
        local oldest_s = redis.call("ZRANGE", key_s, 0, 0, "WITHSCORES")
        if #oldest_s >= 2 then wait_s = tonumber(oldest_s[2]) + win_s - now end
    end
    if count_l >= limit_l then
        local oldest_l = redis.call("ZRANGE", key_l, 0, 0, "WITHSCORES")
        if #oldest_l >= 2 then wait_l = tonumber(oldest_l[2]) + win_l - now end
    end
    local wait = math.max(wait_s, wait_l, 1)
    return -wait  -- negative signals denial; absolute value = ms to wait
end

redis.call("ZADD", key_s, now, uid)
redis.call("ZADD", key_l, now, uid)
redis.call("PEXPIRE", key_s, win_s)
redis.call("PEXPIRE", key_l, win_l)
return 1
```

---

## Python Usage

`acquire_token(r, key_prefix, limit_per_second)` is **non-blocking**: it calls the Lua
script once via `EVAL` and returns an `int` — `1` on success, or a negative int whose
absolute value is the estimated wait time in milliseconds until the next slot opens.

`wait_for_token(r, key_prefix, limit_per_second)` wraps `acquire_token()` and uses the
**adaptive** wait hint from the Lua script to sleep precisely until the next slot opens,
plus jitter (10-50% of wait time) to prevent thundering herd. It blocks until a token is
acquired, or raises `TimeoutError` after `max_wait_s` (default: 60s).

Both functions accept:
- `r` — async Redis connection
- `key_prefix` — default `"ratelimit"` (keys become `{prefix}:short` and `{prefix}:long`)
- `limit_per_second` — overrides the 1-second window cap (default: 20)

See `lol-pipeline-common/src/lol_pipeline/rate_limiter.py` for the current implementation.
Key signatures:
```python
async def acquire_token(r, key_prefix="ratelimit", limit_per_second=20) -> int
async def wait_for_token(r, key_prefix="ratelimit", limit_per_second=20, max_wait_s=60.0) -> None
```

**Caller pattern** (e.g., inside `riot_api.py` before each HTTP call):

```python
await wait_for_token(r)
# proceed with API call
```

**Dynamic limits:** The Lua script reads `ratelimit:limits:short` and `ratelimit:limits:long`
keys from Redis on every invocation. These are written by `RiotClient` after each successful
API response (parsed from the `X-App-Rate-Limit` header). When present, they override the
fallback values passed as ARGV. This means the limiter automatically adapts to the real API
key limits without a restart — for example, when upgrading from a dev key (20/s, 100/2m) to
a production key (higher limits).

---

## Notes

- `req_id` must be unique per call (UUID). If the same `req_id` is submitted twice, the
  Sorted Set treats it as the same member and updates its score rather than double-counting.
  Always generate a fresh UUID per `acquire_token()` call.
- `PEXPIRE` on the Sorted Set keys is a safety net: if all workers go idle, the keys expire
  and clean themselves up. The ZREMRANGEBYSCORE at the top of the script is the primary
  cleanup mechanism during active use.
- Workers check `system:halted` **before** calling `acquire_token()`. A halted worker does
  not consume rate-limit tokens.

---

## Waterfall Integration

The source waterfall (see `docs/architecture/10-source-waterfall.md`) uses `try_token()` from `lol-pipeline-common/src/lol_pipeline/rate_limiter_client.py` rather than `wait_for_token()`. `try_token()` makes a single non-blocking HTTP call to the rate-limiter service and immediately returns `True` (granted) or `False` (denied) without sleeping. This is intentional: when the Riot rate limit is saturated the `WaterfallCoordinator` should fall through to the next source without stalling the worker. `RiotSource.fetch()` calls `try_token(source="riot", endpoint="match")` before each Riot API request; a `False` return maps to `FetchResult.THROTTLED` and causes the coordinator to move to the next registered source.
