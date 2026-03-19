"""Delay Scheduler service — moves ready delayed messages to their target streams."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import time
from typing import Any

import redis.asyncio as aioredis
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.redis_client import get_redis
from redis.exceptions import RedisError

_DELAYED_KEY = "delayed:messages"

_BATCH_SIZE = 100


async def _tick(r: aioredis.Redis, log: logging.Logger) -> None:
    now_ms = int(time.time() * 1000)
    while True:
        members: list[Any] = await r.zrangebyscore(
            _DELAYED_KEY,
            0,
            now_ms,
            start=0,
            num=_BATCH_SIZE,
            withscores=False,
        )
        if not members:
            return
        dispatched = 0
        for member in members:
            try:
                fields: dict[str, str] = json.loads(member)
                env = MessageEnvelope.from_redis_fields(fields)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.error("corrupt delayed member — removing", extra={"error": str(exc)})
                await r.zrem(_DELAYED_KEY, member)
                dispatched += 1
                continue
            try:
                # NOTE: XADD-then-ZREM is intentionally non-atomic (at-least-once).
                # If the process crashes between XADD and ZREM, the message is
                # duplicated (delivered to the stream but remains in the ZSET for
                # re-delivery on restart). This is acceptable because all downstream
                # consumers are idempotent. A Lua script could make this atomic but
                # adds complexity for marginal benefit.
                await r.xadd(
                    env.source_stream,
                    env.to_redis_fields(),  # type: ignore[arg-type]
                    maxlen=10_000,
                    approximate=True,
                )
                await r.zrem(_DELAYED_KEY, member)
                dispatched += 1
                log.info(
                    "dispatched delayed message",
                    extra={"stream": env.source_stream, "id": env.id},
                )
            except (RedisError, OSError) as exc:
                log.error(
                    "Redis error dispatching — will retry",
                    extra={"error": str(exc), "id": env.id},
                )
        if not dispatched:
            return


async def main() -> None:
    """Delay Scheduler loop — polls delayed:messages every DELAY_SCHEDULER_INTERVAL_MS."""
    shutdown_event = asyncio.Event()

    log = get_logger("delay-scheduler")
    cfg = Config()
    r = get_redis(cfg.redis_url)

    import contextlib

    loop = asyncio.get_event_loop()
    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGTERM, shutdown_event.set)

    interval_s = cfg.delay_scheduler_interval_ms / 1000
    log.info("delay-scheduler started", extra={"interval_ms": cfg.delay_scheduler_interval_ms})
    try:
        while not shutdown_event.is_set():
            try:
                await _tick(r, log)
            except (RedisError, OSError):
                log.exception("Redis error — retrying in 1s")
                await asyncio.sleep(1)
                continue
            await asyncio.sleep(interval_s)
        log.info("SIGTERM received — shutting down gracefully")
    finally:
        await r.aclose()
