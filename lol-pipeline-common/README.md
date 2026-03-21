# lol-pipeline-common

Shared library used by all pipeline services. Provides config, models, stream operations, Redis client, rate limiter, raw store, and Riot API client.

## Modules

| Module | Purpose |
|--------|---------|
| `config.py` | Pydantic-settings `Config` — all env vars |
| `log.py` | Structured JSON logger (`get_logger`) |
| `redis_client.py` | `get_redis(url)` — async decode_responses=True client |
| `models.py` | `MessageEnvelope`, `DLQEnvelope` |
| `streams.py` | `publish`, `consume`, `ack`, `nack_to_dlq` |
| `service.py` | `run_consumer` — standard consumer loop with halt-check and error handling |
| `rate_limiter.py` | Lua sliding-window rate limiter; `wait_for_token(r, limit_per_second=20)` |
| `raw_store.py` | `RawStore` — write-once Redis store for raw match JSON blobs |
| `riot_api.py` | `RiotClient` — async Riot API with typed exceptions |

## Key notes

- `consume()` drains PEL before blocking for new messages; handles Redis 7 empty-PEL quirk
- `nack_to_dlq()` requires `failed_by` and `original_message_id` as keyword args
- All async files use `from __future__ import annotations` (deferred annotation eval for redis-py)
- `aioredis.Redis` is not generic — never use `Redis[str]`
