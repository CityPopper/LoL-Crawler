"""Standard consumer service loop — halt-check → consume → dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_ORDER
from lol_pipeline.streams import ack, consume, nack_to_dlq

# Handler receives (msg_id, envelope); r is captured in the closure.
MessageHandler = Callable[[str, MessageEnvelope], Awaitable[None]]

_MAX_HANDLER_RETRIES = int(os.getenv("MAX_HANDLER_RETRIES", "3"))

# TTL for Redis-backed retry counters: 7 days.
_RETRY_KEY_TTL = 604800


def _retry_key(stream: str, msg_id: str) -> str:
    """Build the Redis key for a message's retry counter."""
    return f"consumer:retry:{stream}:{msg_id}"


async def _incr_retry(r: aioredis.Redis, stream: str, msg_id: str) -> int:
    """Increment the Redis-backed retry counter and return the new value.

    INCR and EXPIRE are batched in a single pipeline so a crash between them
    cannot leave the key without a TTL.  Using ``transaction=False`` (no
    MULTI/EXEC) is sufficient — both commands execute in sequence without
    interruption on single-node Redis, and the TTL is refreshed on every retry
    (key expires ``_RETRY_KEY_TTL`` seconds after the *last* attempt).
    """
    key = _retry_key(stream, msg_id)
    async with r.pipeline(transaction=False) as pipe:
        pipe.incr(key)
        pipe.expire(key, _RETRY_KEY_TTL)
        results: list[int] = await pipe.execute()
    return results[0]


async def _clear_retry(r: aioredis.Redis, stream: str, msg_id: str) -> None:
    """Delete the Redis-backed retry counter for a message."""
    await r.delete(_retry_key(stream, msg_id))


async def _handle_with_retry(  # noqa: PLR0913
    r: aioredis.Redis,
    stream: str,
    group: str,
    msg_id: str,
    envelope: MessageEnvelope,
    handler: MessageHandler,
    log: logging.Logger,
    max_retries: int = _MAX_HANDLER_RETRIES,
) -> None:
    """Call handler; on repeated crashes for the same msg_id, nack to DLQ.

    Retry counts are stored in Redis (``consumer:retry:{stream}:{msg_id}``)
    so they survive service restarts — poison messages cannot loop forever.
    """
    try:
        await handler(msg_id, envelope)
        await _clear_retry(r, stream, msg_id)
    except Exception as exc:
        count = await _incr_retry(r, stream, msg_id)
        if count >= max_retries:
            log.error(
                "handler crashed %d times — sending to DLQ",
                count,
                extra={"msg_id": msg_id},
            )
            try:
                await nack_to_dlq(
                    r,
                    envelope,
                    failure_code="handler_crash",
                    failed_by="run_consumer",
                    original_message_id=msg_id,
                )
                await ack(r, stream, group, msg_id)
                await _clear_retry(r, stream, msg_id)
            except Exception:
                log.exception(
                    "nack_to_dlq failed — ACKing to prevent infinite retry loop",
                    extra={"msg_id": msg_id},
                )
                try:
                    await ack(r, stream, group, msg_id)
                except Exception:
                    log.exception(
                        "emergency ACK also failed — message stuck in PEL",
                        extra={"msg_id": msg_id},
                    )
        else:
            log.exception(
                "handler error (%d/%d) [%s]",
                count,
                max_retries,
                type(exc).__name__,
                extra={"msg_id": msg_id},
            )


async def _dispatch_batch(
    r: aioredis.Redis,
    stream: str,
    group: str,
    messages: list[tuple[str, MessageEnvelope]],
    handler: MessageHandler,
    log: logging.Logger,
    shutdown_check: Callable[[], bool],
) -> None:
    """Dispatch a batch of messages to the handler with retry logic."""
    try:
        for msg_id, envelope in messages:
            if shutdown_check():
                log.info("shutdown mid-batch — skipping remaining")
                break
            await _handle_with_retry(
                r,
                stream,
                group,
                msg_id,
                envelope,
                handler,
                log,
            )
    except (RedisError, OSError):
        log.exception("dispatch error — retrying on next cycle")
        await asyncio.sleep(1)


async def run_consumer(
    r: aioredis.Redis,
    stream: str,
    group: str,
    consumer: str,
    handler: MessageHandler,
    log: logging.Logger,
    autoclaim_min_idle_ms: int | None = None,
) -> None:
    """Run the standard consumer loop until system:halted is set.

    Checks system:halted before each batch, then calls consume() and
    dispatches each message to handler.  Caller is responsible for
    closing r and any other resources in a try/finally block.
    """
    shutdown = False

    def _sigterm_handler() -> None:
        nonlocal shutdown
        shutdown = True
        log.info("SIGTERM received — shutting down gracefully")

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)

    idle_polls = 0
    while True:
        if shutdown:
            log.info("shutdown flag set — exiting")
            break
        if await r.get("system:halted"):
            log.critical("system halted — exiting")
            break
        try:
            messages = await consume(
                r,
                stream,
                group,
                consumer,
                autoclaim_min_idle_ms=autoclaim_min_idle_ms,
            )
        except (RedisError, OSError):
            log.exception("consume error — retrying in 1s")
            await asyncio.sleep(1)
            continue
        if len(messages) > 1:
            messages.sort(
                key=lambda m: PRIORITY_ORDER.get(m[1].priority, 0),
                reverse=True,
            )
        if messages:
            idle_polls = 0
        else:
            idle_polls += 1
            if idle_polls % 12 == 1:
                log.debug("waiting for messages", extra={"stream": stream})
        await _dispatch_batch(
            r,
            stream,
            group,
            messages,
            handler,
            log,
            shutdown_check=lambda: shutdown,
        )
