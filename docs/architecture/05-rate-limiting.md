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
-- KEYS[1] = "ratelimit:short"   (1-second sliding window ZSET)
-- KEYS[2] = "ratelimit:long"    (2-minute sliding window ZSET)
-- ARGV[1] = now_ms              (current epoch in milliseconds)
-- ARGV[2] = short_limit_fallback (20)
-- ARGV[3] = long_limit_fallback  (100)
-- ARGV[4] = short_window_ms     (1000)
-- ARGV[5] = long_window_ms      (120000)
-- ARGV[6] = uid                 (UUID — unique per call)
--
-- Returns: 1 (token granted) or 0 (denied)
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

local stored_s = redis.call("GET", "ratelimit:limits:short")
local stored_l = redis.call("GET", "ratelimit:limits:long")
local limit_s = (stored_s and tonumber(stored_s)) or tonumber(ARGV[2])
local limit_l = (stored_l and tonumber(stored_l)) or tonumber(ARGV[3])

redis.call("ZREMRANGEBYSCORE", key_s, "-inf", now - win_s)
redis.call("ZREMRANGEBYSCORE", key_l, "-inf", now - win_l)

local count_s = redis.call("ZCARD", key_s)
local count_l = redis.call("ZCARD", key_l)

if count_s >= limit_s or count_l >= limit_l then
    return 0
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
script once via `EVAL` and returns `True` (granted) or `False` (denied). The Lua script
returns `1` or `0`; the Python wrapper converts to `bool`.

`wait_for_token(r, key_prefix, limit_per_second)` wraps `acquire_token()` in a polling
loop with a fixed **50ms** sleep between attempts. It blocks until a token is acquired.

Both functions accept:
- `r` — async Redis connection
- `key_prefix` — default `"ratelimit"` (keys become `{prefix}:short` and `{prefix}:long`)
- `limit_per_second` — overrides the 1-second window cap (default: 20)

```python
async def acquire_token(
    r: aioredis.Redis,
    key_prefix: str = "ratelimit",
    limit_per_second: int = 20,
) -> bool:
    """Return True and record a token if within both rate windows; False otherwise."""
    now_ms = int(time.time() * 1000)
    uid = str(uuid.uuid4())
    result = await r.eval(
        _LUA_RATE_LIMIT_SCRIPT,
        2,
        f"{key_prefix}:short",
        f"{key_prefix}:long",
        now_ms,
        limit_per_second,  # ARGV[2]: short limit fallback
        100,               # ARGV[3]: long limit fallback
        1000,              # ARGV[4]: short window ms
        120000,            # ARGV[5]: long window ms
        uid,               # ARGV[6]: unique member ID
    )
    return int(result) == 1


async def wait_for_token(
    r: aioredis.Redis,
    key_prefix: str = "ratelimit",
    limit_per_second: int = 20,
) -> None:
    """Block until a rate limit token is acquired, polling every 50ms."""
    while not await acquire_token(r, key_prefix, limit_per_second):
        await asyncio.sleep(0.05)
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
