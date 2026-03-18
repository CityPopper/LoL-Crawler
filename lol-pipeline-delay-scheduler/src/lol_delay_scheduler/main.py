"""Delay Scheduler service — moves ready delayed messages to their target streams."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.redis_client import get_redis

_DELAYED_KEY = "delayed:messages"


async def _tick(r: aioredis.Redis, log: logging.Logger) -> None:
    now_ms = int(time.time() * 1000)
    members: list[Any] = await r.zrangebyscore(_DELAYED_KEY, 0, now_ms, withscores=False)
    if not members:
        return
    for member in members:
        try:
            fields: dict[str, str] = json.loads(member)
            env = MessageEnvelope.from_redis_fields(fields)
            await r.xadd(env.source_stream, env.to_redis_fields())  # type: ignore[arg-type]
            await r.zrem(_DELAYED_KEY, member)
            log.info(
                "dispatched delayed message",
                extra={"stream": env.source_stream, "id": env.id},
            )
        except Exception as exc:
            log.error("failed to dispatch member — removing", extra={"error": str(exc)})
            await r.zrem(_DELAYED_KEY, member)


async def main() -> None:
    """Delay Scheduler loop — polls delayed:messages every DELAY_SCHEDULER_INTERVAL_MS."""
    log = get_logger("delay-scheduler")
    cfg = Config()
    r = get_redis(cfg.redis_url)

    interval_s = cfg.delay_scheduler_interval_ms / 1000
    log.info("delay-scheduler started", extra={"interval_ms": cfg.delay_scheduler_interval_ms})
    try:
        while True:
            await _tick(r, log)
            await asyncio.sleep(interval_s)
    finally:
        await r.aclose()
