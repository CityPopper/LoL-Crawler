"""Lua script constants for sliding-window ZSET rate limiting.

Ported from lol-pipeline-common/src/lol_pipeline/_rate_limiter_data.py.
"""

from __future__ import annotations

# Atomic Lua script: checks both windows before admitting a token.
# KEYS[1] = short-window ZSET key, KEYS[2] = long-window ZSET key
# KEYS[3] = stored short limit key, KEYS[4] = stored long limit key
# ARGV[1] = now_ms, ARGV[2] = short limit fallback, ARGV[3] = long limit fallback,
# ARGV[4] = short window ms, ARGV[5] = long window ms, ARGV[6] = unique member ID
#
# Stored limit keys (KEYS[3]/KEYS[4]) are written by RiotClient after each
# successful API response. If present they override the ARGV fallback values so
# the limiter automatically adapts to the real API key limits.
# All key access uses the KEYS array for Redis Cluster compatibility (no CROSSSLOT).
LUA_RATE_LIMIT_SCRIPT = """
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

-- Time-spread check (long window only): spread requests across the window
-- rather than allowing all at the start. Only activates once enough time
-- has elapsed for the ideal count to exceed 0 (i.e. after one slot width).
if count_l > 0 then
    local oldest_l = redis.call("ZRANGE", key_l, 0, 0, "WITHSCORES")
    if #oldest_l >= 2 then
        local oldest_ts = tonumber(oldest_l[2])
        local elapsed = now - oldest_ts
        if elapsed > 0 and elapsed < win_l then
            local ideal = math.floor(elapsed / win_l * limit_l)
            if ideal > 0 and count_l > ideal then
                -- We are ahead of the ideal rate; hint: when next slot opens
                local next_slot = oldest_ts + math.ceil((count_l + 1) / limit_l * win_l)
                local wait = math.max(next_slot - now, 1)
                return -wait
            end
        end
    end
end

if count_s >= limit_s or count_l >= limit_l then
    -- Return negative wait hint: ms until the earliest slot opens.
    local wait_s = 0
    local wait_l = 0
    if count_s >= limit_s then
        local oldest_s = redis.call("ZRANGE", key_s, 0, 0, "WITHSCORES")
        if #oldest_s >= 2 then
            wait_s = tonumber(oldest_s[2]) + win_s - now
        end
    end
    if count_l >= limit_l then
        local oldest_l = redis.call("ZRANGE", key_l, 0, 0, "WITHSCORES")
        if #oldest_l >= 2 then
            wait_l = tonumber(oldest_l[2]) + win_l - now
        end
    end
    local wait = math.max(wait_s, wait_l, 1)
    return -wait  -- negative signals denial; absolute value = ms to wait
end

redis.call("ZADD", key_s, now, uid)
redis.call("ZADD", key_l, now, uid)
redis.call("PEXPIRE", key_s, win_s)
redis.call("PEXPIRE", key_l, win_l)
return 1
"""

# Dual-bucket method-level rate limiting script.
# Checks BOTH app-level and per-endpoint buckets atomically.
# KEYS[1..4] = app-level (short, long, limits:short, limits:long)
# KEYS[5..8] = method-level (short, long, limits:short, limits:long)
# ARGV[1] = now_ms, ARGV[2] = app_short_limit_fallback, ARGV[3] = app_long_limit_fallback
# ARGV[4] = short_window_ms, ARGV[5] = long_window_ms, ARGV[6] = uid
# ARGV[7] = method_short_limit_fallback, ARGV[8] = method_long_limit_fallback
LUA_RATE_LIMIT_METHOD_SCRIPT = """
local app_key_s = KEYS[1]
local app_key_l = KEYS[2]
local mth_key_s = KEYS[5]
local mth_key_l = KEYS[6]
local now   = tonumber(ARGV[1])
local win_s = tonumber(ARGV[4])
local win_l = tonumber(ARGV[5])
local uid   = ARGV[6]

-- Resolve app-level limits (stored override > fallback)
local stored_as = redis.call("GET", KEYS[3])
local stored_al = redis.call("GET", KEYS[4])
local app_limit_s = (stored_as and tonumber(stored_as)) or tonumber(ARGV[2])
local app_limit_l = (stored_al and tonumber(stored_al)) or tonumber(ARGV[3])
if app_limit_s < 1 then app_limit_s = tonumber(ARGV[2]) end
if app_limit_l < 1 then app_limit_l = tonumber(ARGV[3]) end

-- Resolve method-level limits (stored override > fallback)
local stored_ms = redis.call("GET", KEYS[7])
local stored_ml = redis.call("GET", KEYS[8])
local mth_limit_s = (stored_ms and tonumber(stored_ms)) or tonumber(ARGV[7])
local mth_limit_l = (stored_ml and tonumber(stored_ml)) or tonumber(ARGV[8])
if mth_limit_s < 1 then mth_limit_s = tonumber(ARGV[7]) end
if mth_limit_l < 1 then mth_limit_l = tonumber(ARGV[8]) end

-- Prune expired entries from all 4 ZSETs
redis.call("ZREMRANGEBYSCORE", app_key_s, "-inf", now - win_s)
redis.call("ZREMRANGEBYSCORE", app_key_l, "-inf", now - win_l)
redis.call("ZREMRANGEBYSCORE", mth_key_s, "-inf", now - win_s)
redis.call("ZREMRANGEBYSCORE", mth_key_l, "-inf", now - win_l)

local app_count_s = redis.call("ZCARD", app_key_s)
local app_count_l = redis.call("ZCARD", app_key_l)
local mth_count_s = redis.call("ZCARD", mth_key_s)
local mth_count_l = redis.call("ZCARD", mth_key_l)

-- Time-spread check (long window only, app-level)
if app_count_l > 0 then
    local oldest_l = redis.call("ZRANGE", app_key_l, 0, 0, "WITHSCORES")
    if #oldest_l >= 2 then
        local oldest_ts = tonumber(oldest_l[2])
        local elapsed = now - oldest_ts
        if elapsed > 0 and elapsed < win_l then
            local ideal = math.floor(elapsed / win_l * app_limit_l)
            if ideal > 0 and app_count_l > ideal then
                local next_slot = oldest_ts + math.ceil((app_count_l + 1) / app_limit_l * win_l)
                local wait = math.max(next_slot - now, 1)
                return -wait
            end
        end
    end
end

-- Time-spread check (long window only, method-level)
if mth_count_l > 0 then
    local oldest_l = redis.call("ZRANGE", mth_key_l, 0, 0, "WITHSCORES")
    if #oldest_l >= 2 then
        local oldest_ts = tonumber(oldest_l[2])
        local elapsed = now - oldest_ts
        if elapsed > 0 and elapsed < win_l then
            local ideal = math.floor(elapsed / win_l * mth_limit_l)
            if ideal > 0 and mth_count_l > ideal then
                local next_slot = oldest_ts + math.ceil((mth_count_l + 1) / mth_limit_l * win_l)
                local wait = math.max(next_slot - now, 1)
                return -wait
            end
        end
    end
end

-- Hard-cap check: deny if ANY bucket is full
local denied = false
local max_wait = 0

if app_count_s >= app_limit_s then
    denied = true
    local oldest = redis.call("ZRANGE", app_key_s, 0, 0, "WITHSCORES")
    if #oldest >= 2 then
        max_wait = math.max(max_wait, tonumber(oldest[2]) + win_s - now)
    end
end
if app_count_l >= app_limit_l then
    denied = true
    local oldest = redis.call("ZRANGE", app_key_l, 0, 0, "WITHSCORES")
    if #oldest >= 2 then
        max_wait = math.max(max_wait, tonumber(oldest[2]) + win_l - now)
    end
end
if mth_count_s >= mth_limit_s then
    denied = true
    local oldest = redis.call("ZRANGE", mth_key_s, 0, 0, "WITHSCORES")
    if #oldest >= 2 then
        max_wait = math.max(max_wait, tonumber(oldest[2]) + win_s - now)
    end
end
if mth_count_l >= mth_limit_l then
    denied = true
    local oldest = redis.call("ZRANGE", mth_key_l, 0, 0, "WITHSCORES")
    if #oldest >= 2 then
        max_wait = math.max(max_wait, tonumber(oldest[2]) + win_l - now)
    end
end

if denied then
    return -math.max(max_wait, 1)
end

-- Both app and method have capacity: add to all 4 ZSETs
redis.call("ZADD", app_key_s, now, uid)
redis.call("ZADD", app_key_l, now, uid)
redis.call("ZADD", mth_key_s, now, uid)
redis.call("ZADD", mth_key_l, now, uid)
redis.call("PEXPIRE", app_key_s, win_s)
redis.call("PEXPIRE", app_key_l, win_l)
redis.call("PEXPIRE", mth_key_s, win_s)
redis.call("PEXPIRE", mth_key_l, win_l)
return 1
"""
