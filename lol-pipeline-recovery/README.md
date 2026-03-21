# lol-pipeline-recovery

Consumes `stream:dlq`, applies retry/backoff/archive logic, and requeues recoverable messages via `delayed:messages`.

## Behaviour

1. Consumes `stream:dlq` via consumer group `recovery` (continues even when `system:halted`)
2. Routes by `failure_code`:

| failure_code | Action |
|-------------|--------|
| `http_403` | Sets `system:halted=1`, archives immediately |
| `http_429` | Requeues to `delayed:messages` with `retry_after_ms` or exponential backoff; archives after `DLQ_MAX_ATTEMPTS` |
| `http_5xx` | Requeues with exponential backoff; archives after `DLQ_MAX_ATTEMPTS` |
| `http_404` | Discards (permanent) |
| `parse_error` | Archives for operator review |
| unknown | Archives for operator review |

## Backoff schedule

Indexed by `dlq_attempts`: 5s → 15s → 60s → 300s

## Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `DLQ_MAX_ATTEMPTS` | `3` | Max retries before archiving |
