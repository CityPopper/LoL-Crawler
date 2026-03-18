# lol-pipeline-crawler

Reads PUUIDs from `stream:puuid`, fetches all match IDs from Riot Match-v5 API, and publishes each new match ID to `stream:match_id`.

## Behaviour

1. Consumes `stream:puuid` via consumer group `crawlers`
2. Loads already-known match IDs from `player:matches:{puuid}` (sorted set)
3. Paginates Riot API (`GET /lol/match/v5/matches/by-puuid/{puuid}/ids`) in pages of 100
4. Stops pagination early when a full page contains only known IDs
5. Publishes each new match ID to `stream:match_id`
6. Records `last_crawled_at` on `player:{puuid}` hash

## Rate limiting

Uses `wait_for_token()` before every API call. Limit configurable via `API_RATE_LIMIT_PER_SECOND` (default 20).

## Error handling

| Error | Action |
|-------|--------|
| `AuthError` (401/403) | Sets `system:halted=1`, leaves message in PEL |
| `RateLimitError` (429) | Nacks to DLQ with `retry_after_ms` |
| `ServerError` (5xx) | Nacks to DLQ |
