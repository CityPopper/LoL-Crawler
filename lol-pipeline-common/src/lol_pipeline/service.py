"""Standard consumer service loop — halt-check → consume → dispatch."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis

from lol_pipeline.models import MessageEnvelope
from lol_pipeline.streams import ack, consume, nack_to_dlq

# Handler receives (msg_id, envelope); r is captured in the closure.
MessageHandler = Callable[[str, MessageEnvelope], Awaitable[None]]

_MAX_HANDLER_RETRIES = 3


async def _handle_with_retry(
    r: aioredis.Redis,
    stream: str,
    group: str,
    msg_id: str,
    envelope: MessageEnvelope,
    handler: MessageHandler,
    log: logging.Logger,
    failures: dict[str, int],
    max_retries: int = _MAX_HANDLER_RETRIES,
) -> None:
    """Call handler; on repeated crashes for the same msg_id, nack to DLQ."""
    try:
        await handler(msg_id, envelope)
        failures.pop(msg_id, None)
    except Exception:
        count = failures.get(msg_id, 0) + 1
        failures[msg_id] = count
        if count >= max_retries:
            log.error(
                "handler crashed %d times — sending to DLQ",
                count,
                extra={"msg_id": msg_id},
            )
            await nack_to_dlq(
                r, envelope,
                failure_code="handler_crash",
                failed_by="run_consumer",
                original_message_id=msg_id,
            )
            await ack(r, stream, group, msg_id)
            failures.pop(msg_id, None)
        else:
            log.exception(
                "handler error (%d/%d)", count, max_retries,
                extra={"msg_id": msg_id},
            )


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

    Checks system:halted before each batch, then calls consume() (which drains
    the consumer's own PEL before reading new messages) and dispatches each
    message to handler.  Caller is responsible for closing r and any other
    resources in a try/finally block.
    """
    idle_polls = 0
    handler_failures: dict[str, int] = {}
    while True:
        if await r.get("system:halted"):
            log.critical("system halted — exiting")
            break
        try:
            messages = await consume(
                r, stream, group, consumer,
                autoclaim_min_idle_ms=autoclaim_min_idle_ms,
            )
        except Exception:
            log.exception("consume error — retrying in 1s")
            await asyncio.sleep(1)
            continue
        if messages:
            idle_polls = 0
        else:
            idle_polls += 1
            if idle_polls % 12 == 1:  # ~60s at 5s block
                log.debug("waiting for messages", extra={"stream": stream})
        for msg_id, envelope in messages:
            await _handle_with_retry(
                r, stream, group, msg_id, envelope, handler, log, handler_failures,
            )
