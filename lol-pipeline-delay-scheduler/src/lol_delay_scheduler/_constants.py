"""Delay Scheduler constants and Lua script — extracted from main.py."""

from __future__ import annotations

from lol_pipeline.streams import (
    ANALYZE_STREAM_MAXLEN,
    MATCH_ID_STREAM_MAXLEN,
)

# Per-stream maxlen policy.  Streams not listed here use DEFAULT_STREAM_MAXLEN.
_STREAM_MAXLEN: dict[str, int | None] = {
    "stream:match_id": MATCH_ID_STREAM_MAXLEN,
    "stream:analyze": ANALYZE_STREAM_MAXLEN,
}

# Atomic XADD + ZREM: dispatch a delayed message and remove from the ZSET in one
# server round-trip.  Prevents duplicate delivery if the process crashes between
# the two operations.
#
# KEYS[1] = target stream, KEYS[2] = delayed:messages ZSET key
# ARGV[1] = ZSET member to remove
# ARGV[2] = maxlen for the stream ("0" means no trimming)
# Remaining ARGV pairs (3..N) = field, value, field, value, ...  for XADD
_DISPATCH_LUA = """
local stream = KEYS[1]
local zkey   = KEYS[2]
local member = ARGV[1]
local maxlen = tonumber(ARGV[2])

-- Guard: member must still exist in the ZSET.
-- If a prior run completed XADD but crashed before ZREM, the member may have
-- been removed by a subsequent successful dispatch.  Skip the XADD to prevent
-- duplicate delivery.
local exists = redis.call("ZSCORE", zkey, member)
if not exists then
    return 0
end

local n = #ARGV
local fields = {}
for i = 3, n, 2 do
    fields[#fields + 1] = ARGV[i]
    fields[#fields + 1] = ARGV[i + 1]
end

if maxlen and maxlen > 0 then
    redis.call("XADD", stream, "MAXLEN", "~", maxlen, "*", unpack(fields))
else
    redis.call("XADD", stream, "*", unpack(fields))
end
redis.call("ZREM", zkey, member)
return 1
"""
