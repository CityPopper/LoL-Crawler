"""Stream constants and Lua script — extracted from streams.py."""

from __future__ import annotations

_DEFAULT_MAXLEN = 10_000

# Per-stream maxlen overrides.  Import these from consuming services to keep
# the policy in one place.
# ~20 MB buffer; trimmed IDs are re-discoverable via crawler re-crawl
MATCH_ID_STREAM_MAXLEN: int = 500_000
ANALYZE_STREAM_MAXLEN: int = 50_000  # 10x amplification from parser

# Atomic DLQ replay: XADD to target stream + XDEL from stream:dlq in one
# server round-trip.  Prevents duplicate replay if the process crashes between
# the two operations.
#
# KEYS[1] = target stream, KEYS[2] = stream:dlq
# ARGV[1] = DLQ entry ID to delete
# ARGV[2] = maxlen for the target stream ("0" means no trimming)
# Remaining ARGV pairs (3..N) = field, value, field, value, ... for XADD
_REPLAY_LUA = """
local stream  = KEYS[1]
local dlq     = KEYS[2]
local entry_id = ARGV[1]
local maxlen  = tonumber(ARGV[2])

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
redis.call("XDEL", dlq, entry_id)
return 1
"""

# Stream-to-maxlen mapping for replay.
_REPLAY_MAXLEN_MAP: dict[str, int] = {
    "stream:match_id": MATCH_ID_STREAM_MAXLEN,
    "stream:analyze": ANALYZE_STREAM_MAXLEN,
}
