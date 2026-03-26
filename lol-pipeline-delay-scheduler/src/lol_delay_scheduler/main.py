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
from lol_pipeline.constants import DELAYED_MESSAGES_KEY
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.redis_client import get_redis
from redis.exceptions import RedisError

from lol_delay_scheduler._circuit_breaker import (
    _cb as _cb,
)
from lol_delay_scheduler._circuit_breaker import (
    _circuit_open as _circuit_open,
)
from lol_delay_scheduler._circuit_breaker import (
    _is_circuit_open as _is_circuit_open,
)
from lol_delay_scheduler._circuit_breaker import (
    _member_failures as _member_failures,
)
from lol_delay_scheduler._circuit_breaker import (
    _record_failure as _record_failure,
)
from lol_delay_scheduler._circuit_breaker import (
    _record_success as _record_success,
)
from lol_delay_scheduler._circuit_breaker import (
    init_circuit_config,
)
from lol_delay_scheduler._constants import _DISPATCH_LUA as _DISPATCH_LUA
from lol_delay_scheduler._helpers import (
    _is_envelope_id,
)
from lol_delay_scheduler._helpers import (
    _maxlen_for_stream as _maxlen_for_stream,
)

# Batch size — sourced from Config (single source of truth); overridden at startup
# via _init_circuit_config() when env vars customise the value.
_BATCH_SIZE: int = Config.model_fields["delay_scheduler_batch_size"].default

# Maximum batches per _tick invocation to prevent monopolising the event loop.
_MAX_BATCHES_PER_TICK: int = 10

# IMP-067: Backpressure ratio — skip dispatch when stream length exceeds this
# fraction of its MAXLEN.  Overridden at startup from Config.
_BACKPRESSURE_RATIO: float = Config.model_fields[
    "delay_scheduler_backpressure_ratio"
].default

# Defer interval (ms) when backpressure is detected — re-check after 10s.
_BACKPRESSURE_DEFER_MS: int = 10_000


def _init_circuit_config(cfg: Config) -> None:
    """Seed module-level circuit-breaker thresholds and batch size from Config."""
    global _BATCH_SIZE, _BACKPRESSURE_RATIO
    init_circuit_config(
        max_failures=cfg.delay_scheduler_max_member_failures,
        open_ttl_s=cfg.delay_scheduler_circuit_open_ttl_s,
    )
    _BATCH_SIZE = cfg.delay_scheduler_batch_size
    _BACKPRESSURE_RATIO = cfg.delay_scheduler_backpressure_ratio


async def _is_stream_backpressured(
    r: aioredis.Redis,
    stream: str,
    log: logging.Logger,
) -> bool:
    """Return True if *stream* length exceeds its MAXLEN * backpressure ratio.

    IMP-067: Prevents XADD dispatch from silently dropping unread entries via
    MAXLEN trimming when the consumer is slow.
    """
    ml = _maxlen_for_stream(stream)
    if ml is None or ml <= 0:
        return False
    threshold = int(ml * _BACKPRESSURE_RATIO)
    depth: int = await r.xlen(stream)
    if depth >= threshold:
        log.warning(
            "backpressure — target stream too deep, deferring dispatch",
            extra={"stream": stream, "depth": depth, "threshold": threshold},
        )
        return True
    return False


async def _resolve_member(
    r: aioredis.Redis,
    member: str,
    log: logging.Logger,
) -> MessageEnvelope | None:
    """Deserialize a ZSET member into a MessageEnvelope.

    RDB-5: members can be either an envelope ID (new format) or a full JSON
    blob (legacy format).  For ID members the envelope data is fetched from
    ``delayed:envelope:{id}``.

    Returns ``None`` when the member is corrupt and has been removed.
    """
    if _is_envelope_id(member):
        data: str | None = await r.hget(f"delayed:envelope:{member}", "data")  # type: ignore[misc]
        if data is None:
            log.error(
                "delayed envelope hash missing for ID member — removing",
                extra={"member": member},
            )
            await r.zrem(DELAYED_MESSAGES_KEY, member)
            _record_success(member)
            return None
        try:
            fields: dict[str, str] = json.loads(data)
            return MessageEnvelope.from_redis_fields(fields)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.error(
                "corrupt delayed envelope hash — removing",
                extra={"error": str(exc), "member": member},
            )
            await r.zrem(DELAYED_MESSAGES_KEY, member)
            await r.delete(f"delayed:envelope:{member}")
            _record_success(member)
            return None
    else:
        # Legacy format: full JSON blob as member
        try:
            fields = json.loads(member)
            return MessageEnvelope.from_redis_fields(fields)
        except (json.JSONDecodeError, KeyError, ValueError) as exc:
            log.error("corrupt delayed member — removing", extra={"error": str(exc)})
            await r.zrem(DELAYED_MESSAGES_KEY, member)
            _record_success(member)
            return None


def _build_dispatch_args(
    member: str,
    ml: int | None,
    fields: dict[str, str],
) -> list[str]:
    """Build the flat ARGV list for the dispatch Lua script."""
    flat_args: list[str] = [member, str(ml if ml is not None else 0)]
    for k, v in fields.items():
        flat_args.append(str(k))
        flat_args.append(str(v))
    return flat_args


async def _execute_dispatch_lua(
    r: aioredis.Redis,
    stream: str,
    flat_args: list[str],
) -> int:
    """Execute the atomic XADD+ZREM Lua script and return its result."""
    result: int = await r.eval(  # type: ignore[misc]
        _DISPATCH_LUA,
        2,
        stream,
        DELAYED_MESSAGES_KEY,
        *flat_args,
    )
    return result


async def _dispatch_member(
    r: aioredis.Redis,
    member: str,
    env: MessageEnvelope,
    log: logging.Logger,
) -> None:
    """Dispatch a single resolved envelope via Lua and log the result."""
    redis_fields = env.to_redis_fields()
    ml = _maxlen_for_stream(env.source_stream)
    flat_args = _build_dispatch_args(member, ml, redis_fields)
    result = await _execute_dispatch_lua(r, env.source_stream, flat_args)
    _record_success(member)
    # Clean up envelope hash for ID-based members (RDB-5)
    if _is_envelope_id(member):
        await r.delete(f"delayed:envelope:{member}")
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


async def _tick(r: aioredis.Redis, log: logging.Logger) -> None:
    now_ms = int(time.time() * 1000)
    for _batch_num in range(_MAX_BATCHES_PER_TICK):
        members: list[Any] = await r.zrangebyscore(
            DELAYED_MESSAGES_KEY,
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
                future_ms = int(time.time() * 1000) + (_cb.open_ttl_s * 1000)
                await r.zadd(DELAYED_MESSAGES_KEY, {member: future_ms}, xx=True)
                dispatched += 1
                continue
            env = await _resolve_member(r, member, log)
            if env is None:
                dispatched += 1
                continue
            # IMP-067: Check backpressure before dispatching to avoid silent
            # MAXLEN trimming of unread entries in the target stream.
            if await _is_stream_backpressured(r, env.source_stream, log):
                defer_ms = int(time.time() * 1000) + _BACKPRESSURE_DEFER_MS
                await r.zadd(DELAYED_MESSAGES_KEY, {member: defer_ms}, xx=True)
                dispatched += 1
                continue
            try:
                await _dispatch_member(r, member, env, log)
                dispatched += 1
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
    _init_circuit_config(cfg)
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
