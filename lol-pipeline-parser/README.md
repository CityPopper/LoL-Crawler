# lol-pipeline-parser

Reads match IDs from `stream:parse`, loads the raw match JSON from the raw store, writes structured participant data to Redis, and publishes one `stream:analyze` message per participant.

## Behaviour

1. Consumes `stream:parse` via consumer group `parsers`
2. Reads raw match JSON from `RawStore` (Redis `raw:match:{match_id}`)
3. Validates required fields (`participants`, `gameStartTimestamp`)
4. Writes match metadata to `match:{match_id}` hash
5. For each participant: writes stats to `participant:{match_id}:{puuid}` and adds to `player:matches:{puuid}` sorted set (scored by game start timestamp)
6. Publishes one `stream:analyze` message per participant puuid

## Redis writes

| Key | Type | Contents |
|-----|------|----------|
| `match:{match_id}` | Hash | queue_id, game_mode, duration, status=parsed |
| `match:participants:{match_id}` | Set | All puuids in this match |
| `participant:{match_id}:{puuid}` | Hash | champion, k/d/a, gold, damage, items, role |
| `player:matches:{puuid}` | Sorted set | match_id → game_start timestamp |
| `match:status:parsed` | Set | All parsed match IDs |
