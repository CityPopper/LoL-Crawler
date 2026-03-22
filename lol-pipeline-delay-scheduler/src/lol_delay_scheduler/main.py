"""Delay Scheduler service — moves ready delayed messages to their target streams."""

from __future__ import annotations

import asyncio
import contextlib
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
from lol_pipeline.streams import _DEFAULT_MAXLEN
from redis.exceptions import RedisError

from lol_delay_scheduler._data import (
    _BATCH_SIZE,
    _CIRCUIT_OPEN_TTL_S,
    _DELAYED_KEY,
    _MAX_MEMBER_FAILURES,
    _STREAM_MAXLEN,
)
from lol_delay_scheduler._data import (
    _DISPATCH_LUA as _DISPATCH_LUA,
)

# Per-member failure tracking (module-level, survives across ticks).
_member_failures: dict[str, int] = {}
# Circuit-open members: member → time.monotonic() when circuit was opened.
_circuit_open: dict[str, float] = {}


def _maxlen_for_stream(stream: str) -> int | None:
    """Return the maxlen policy for *stream* (None = no trimming)."""
    return _STREAM_MAXLEN.get(stream, _DEFAULT_MAXLEN)


def _is_circuit_open(member: str) -> bool:
    """Return True if *member* is in the circuit-open set and TTL has not expired."""
    opened_at = _circuit_open.get(member)
    if opened_at is None:
        return False
    if time.monotonic() - opened_at >= _CIRCUIT_OPEN_TTL_S:
        # TTL expired — allow a single retry; reset failure counter
        del _circuit_open[member]
        _member_failures.pop(member, None)
        return False
    return True


def _record_failure(member: str, log: logging.Logger) -> None:
    """Increment failure count; open circuit after _MAX_MEMBER_FAILURES."""
    count = _member_failures.get(member, 0) + 1
    _member_failures[member] = count
    if count >= _MAX_MEMBER_FAILURES:
        _circuit_open[member] = time.monotonic()
        log.warning(
            "circuit opened for member after %d failures — skipping for %ds",
            count,
            _CIRCUIT_OPEN_TTL_S,
            extra={"member_preview": member[:80]},
        )


def _record_success(member: str) -> None:
    """Clear failure state on successful dispatch."""
    _member_failures.pop(member, None)
    _circuit_open.pop(member, None)


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
            if _is_circuit_open(member):
                # Push member to future so it doesn't starve other ready messages.
                future_ms = int(time.time() * 1000) + (_CIRCUIT_OPEN_TTL_S * 1000)
                await r.zadd(_DELAYED_KEY, {member: future_ms}, xx=True)
                dispatched += 1
                continue
            try:
                fields: dict[str, str] = json.loads(member)
                env = MessageEnvelope.from_redis_fields(fields)
            except (json.JSONDecodeError, KeyError, ValueError) as exc:
                log.error("corrupt delayed member — removing", extra={"error": str(exc)})
                await r.zrem(_DELAYED_KEY, member)
                _record_success(member)
                dispatched += 1
                continue
            try:
                # Atomic XADD + ZREM via Lua — no duplicate delivery on crash.
                redis_fields = env.to_redis_fields()
                ml = _maxlen_for_stream(env.source_stream)
                flat_args: list[str] = [member, str(ml if ml is not None else 0)]
                for k, v in redis_fields.items():
                    flat_args.append(str(k))
                    flat_args.append(str(v))
                result = await r.eval(  # type: ignore[misc]
                    _DISPATCH_LUA,
                    2,
                    env.source_stream,
                    _DELAYED_KEY,
                    *flat_args,
                )
                _record_success(member)
                dispatched += 1
                if result == 0:
                    log.info(
                        "skipped duplicate dispatch — member already removed",
                        extra={"stream": env.source_stream, "id": env.id},
                    )
                else:
                    log.info(
                        "dispatched delayed message",
                        extra={"stream": env.source_stream, "id": env.id},
                    )
            except (RedisError, OSError) as exc:
                _record_failure(member, log)
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

    loop = asyncio.get_running_loop()
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
