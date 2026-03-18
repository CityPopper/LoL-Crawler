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

Both windows are checked **atomically** in a single Lua script with two KEYS. This prevents
a race condition where two workers each pass one window check but together exceed the limit.

Any service that calls the Riot API must call `acquire_token()` before each request.
Workers that cannot acquire a token calculate the required sleep time and retry; they do
**not** hold a stream ACK while sleeping.

---

## Lua Script

```lua
-- KEYS[1] = "ratelimit:short"
-- KEYS[2] = "ratelimit:long"
-- ARGV[1] = now_ms         (current epoch in milliseconds)
-- ARGV[2] = short_window   (1000)
-- ARGV[3] = long_window    (120000)
-- ARGV[4] = short_limit    (20)
-- ARGV[5] = long_limit     (100)
-- ARGV[6] = req_id         (UUID — unique per call)
--
-- Returns: {allowed, oldest_short_ms, oldest_long_ms}
--   allowed = 1 → token granted
--   allowed = 0 → denied; sleep time = max(
--       short_window - (now - oldest_short_ms),
--       long_window  - (now - oldest_long_ms)
--   ) + buffer

local now          = tonumber(ARGV[1])
local short_window = tonumber(ARGV[2])
local long_window  = tonumber(ARGV[3])
local short_limit  = tonumber(ARGV[4])
local long_limit   = tonumber(ARGV[5])
local req_id       = ARGV[6]

-- Remove expired entries from both windows
redis.call('ZREMRANGEBYSCORE', KEYS[1], 0, now - short_window)
redis.call('ZREMRANGEBYSCORE', KEYS[2], 0, now - long_window)

local short_count = redis.call('ZCARD', KEYS[1])
local long_count  = redis.call('ZCARD', KEYS[2])

if short_count < short_limit and long_count < long_limit then
    -- Grant token: record in both windows
    redis.call('ZADD', KEYS[1], now, req_id)
    redis.call('PEXPIRE', KEYS[1], short_window)
    redis.call('ZADD', KEYS[2], now, req_id)
    redis.call('PEXPIRE', KEYS[2], long_window)
    return {1, -1, -1}
end

-- Denied: return oldest scores so caller can compute sleep duration
local oldest_short = -1
local oldest_long  = -1
if short_count >= short_limit then
    local s = redis.call('ZRANGE', KEYS[1], 0, 0, 'WITHSCORES')
    oldest_short = tonumber(s[2])
end
if long_count >= long_limit then
    local l = redis.call('ZRANGE', KEYS[2], 0, 0, 'WITHSCORES')
    oldest_long = tonumber(l[2])
end
return {0, oldest_short, oldest_long}
```

---

## Python Usage

`acquire_token(r, key_prefix, limit_per_second)` is **non-blocking**: it calls the Lua
script once and returns True/False immediately. `wait_for_token()` wraps it in a polling
loop (50ms interval). Both accept `limit_per_second` to override the default of 20.

Services pass `cfg.api_rate_limit_per_second` from `Config` (sourced from `API_RATE_LIMIT_PER_SECOND`):

```python
# rate_limiter.py — module-level state (redis client + lua SHA loaded at startup)
_redis: Redis | None = None
_lua_sha: str | None = None

async def acquire_token() -> tuple[bool, float]:
    """Try to acquire one rate-limit token.

    Returns:
        (True, 0.0)              — token granted; proceed immediately.
        (False, wait_seconds)    — denied; caller should sleep wait_seconds then retry.

    wait_seconds already includes a 50ms buffer to avoid boundary races.
    """
    BUFFER_MS = 50
    now_ms = int(time.time() * 1000)
    req_id = str(uuid4())
    result = await _redis.evalsha(
        _lua_sha,
        2,
        "ratelimit:short",
        "ratelimit:long",
        now_ms, 1000, 120000, 20, 100, req_id
    )
    allowed, oldest_short, oldest_long = result
    if allowed:
        return True, 0.0

    sleep_ms = 0
    if oldest_short >= 0:
        sleep_ms = max(sleep_ms, 1000 - (now_ms - oldest_short) + BUFFER_MS)
    if oldest_long >= 0:
        sleep_ms = max(sleep_ms, 120000 - (now_ms - oldest_long) + BUFFER_MS)
    return False, sleep_ms / 1000
```

**Caller pattern** (e.g., inside `riot_api.py` before each HTTP call):

```python
while True:
    allowed, wait_seconds = await acquire_token()
    if allowed:
        break
    await asyncio.sleep(wait_seconds)
# proceed with API call
```

The Lua script is loaded at worker startup via `SCRIPT LOAD` and called via `EVALSHA` for
efficiency. The SHA is stored on the module; if Redis is restarted and the script cache is
cleared, the module reloads it automatically on `NOSCRIPT` error.

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
