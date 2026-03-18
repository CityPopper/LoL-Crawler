# lol-pipeline-fetcher

Reads match IDs from `stream:match_id`, fetches the full match JSON from Riot Match-v5 API, stores it in the raw store, and publishes to `stream:parse`.

## Behaviour

1. Consumes `stream:match_id` via consumer group `fetchers`
2. Checks raw store for existing blob (idempotent — re-publishes to `stream:parse` if already stored)
3. Calls `GET /lol/match/v5/matches/{match_id}` with rate limiting
4. Stores raw JSON in `RawStore` (Redis key `raw:match:{match_id}`)
5. Publishes `MessageEnvelope` with type `parse` to `stream:parse`

## Rate limiting

Uses `wait_for_token()` before every API call. Limit configurable via `API_RATE_LIMIT_PER_SECOND` (default 20).

## Error handling

| Error | Action |
|-------|--------|
| `NotFoundError` (404) | Marks match as `not_found`, acks |
| `AuthError` (401/403) | Sets `system:halted=1`, leaves message in PEL |
| `RateLimitError` (429) | Nacks to DLQ with `retry_after_ms` |
| `ServerError` (5xx/network) | Nacks to DLQ |
