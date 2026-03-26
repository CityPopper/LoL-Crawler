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
