"""Analyzer constants and Lua scripts — extracted from main.py."""

from __future__ import annotations

_IN_STREAM = "stream:analyze"
_GROUP = "analyzers"

# Atomic lock-release: only delete if we still own it.
_RELEASE_LOCK_LUA = """
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
"""

# Atomic lock-refresh: only extend TTL if we still own the lock.
# Returns 1 if refreshed, 0 if ownership lost.
_REFRESH_LOCK_LUA = """
local val = redis.call("GET", KEYS[1])
if val == ARGV[1] then
    return redis.call("PEXPIRE", KEYS[1], ARGV[2])
else
    return 0
end
"""

# V15-1: Atomic stats update + ownership check.
# KEYS[1] = lock_key, KEYS[2] = stats_key, KEYS[3] = cursor_key
# Optional: KEYS[4] = champs_key, KEYS[5] = roles_key
# ARGV[1] = worker_id, ARGV[2] = lock_ttl_ms
# ARGV[3] = win, ARGV[4] = kills, ARGV[5] = deaths, ARGV[6] = assists
# ARGV[7] = cursor_score, ARGV[8] = champion_name (or ""), ARGV[9] = role (or "")
_PROCESS_MATCH_LUA = """
local lock_key  = KEYS[1]
local stats_key = KEYS[2]
local cursor_key = KEYS[3]
local champs_key = KEYS[4]
local roles_key  = KEYS[5]
local worker_id = ARGV[1]
local ttl_ms    = tonumber(ARGV[2])

if redis.call("GET", lock_key) ~= worker_id then
    return 0
end

redis.call("HINCRBY", stats_key, "total_games", 1)
redis.call("HINCRBY", stats_key, "total_wins", tonumber(ARGV[3]))
redis.call("HINCRBY", stats_key, "total_kills", tonumber(ARGV[4]))
redis.call("HINCRBY", stats_key, "total_deaths", tonumber(ARGV[5]))
redis.call("HINCRBY", stats_key, "total_assists", tonumber(ARGV[6]))

if ARGV[8] ~= "" then
    redis.call("ZINCRBY", champs_key, 1, ARGV[8])
end
if ARGV[9] ~= "" then
    redis.call("ZINCRBY", roles_key, 1, ARGV[9])
end

redis.call("SET", cursor_key, ARGV[7])
redis.call("PEXPIRE", lock_key, ttl_ms)
return 1
"""


# Champion aggregate stats: atomic HINCRBY + index update + patch list.
# KEYS[1] = champion:stats:{name}:{patch}:{role}
# KEYS[2] = champion:index:{patch}
# KEYS[3] = patch:list
# ARGV[1] = win (0/1), ARGV[2] = kills, ARGV[3] = deaths, ARGV[4] = assists
# ARGV[5] = gold, ARGV[6] = cs, ARGV[7] = damage, ARGV[8] = vision
# ARGV[9] = champion_name:role (index member)
# ARGV[10] = game_start_epoch (patch:list score)
# ARGV[11] = patch_string
# ARGV[12] = ttl_seconds
# ARGV[13] = double_kills, ARGV[14] = triple_kills
# ARGV[15] = quadra_kills, ARGV[16] = penta_kills
_UPDATE_CHAMPION_LUA = """
local stats_key = KEYS[1]
local index_key = KEYS[2]
local patch_key = KEYS[3]
local ttl = tonumber(ARGV[12])

redis.call("HINCRBY", stats_key, "games", 1)
redis.call("HINCRBY", stats_key, "wins", tonumber(ARGV[1]))
redis.call("HINCRBY", stats_key, "kills", tonumber(ARGV[2]))
redis.call("HINCRBY", stats_key, "deaths", tonumber(ARGV[3]))
redis.call("HINCRBY", stats_key, "assists", tonumber(ARGV[4]))
redis.call("HINCRBY", stats_key, "gold", tonumber(ARGV[5]))
redis.call("HINCRBY", stats_key, "cs", tonumber(ARGV[6]))
redis.call("HINCRBY", stats_key, "damage", tonumber(ARGV[7]))
redis.call("HINCRBY", stats_key, "vision", tonumber(ARGV[8]))
redis.call("HINCRBY", stats_key, "double_kills", tonumber(ARGV[13]))
redis.call("HINCRBY", stats_key, "triple_kills", tonumber(ARGV[14]))
redis.call("HINCRBY", stats_key, "quadra_kills", tonumber(ARGV[15]))
redis.call("HINCRBY", stats_key, "penta_kills", tonumber(ARGV[16]))
redis.call("EXPIRE", stats_key, ttl)
redis.call("ZINCRBY", index_key, 1, ARGV[9])
redis.call("EXPIRE", index_key, ttl)
redis.call("ZADD", patch_key, "NX", tonumber(ARGV[10]), ARGV[11])
redis.call("EXPIRE", patch_key, ttl)
return 1
"""
