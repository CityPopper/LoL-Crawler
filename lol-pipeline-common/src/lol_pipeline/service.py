"""Standard consumer service loop — halt-check → consume → dispatch."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal
from collections.abc import Awaitable, Callable

import redis.asyncio as aioredis
from redis.exceptions import RedisError

from lol_pipeline._service_data import (
    _MAX_HANDLER_RETRIES,
    _MAX_NACK_ATTEMPTS,
    _RETRY_KEY_PREFIX,
)
from lol_pipeline._service_data import (
    _RETRY_KEY_TTL as _RETRY_KEY_TTL,
)
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_ORDER
from lol_pipeline.streams import ack, consume, nack_to_dlq

# Handler receives (msg_id, envelope); r is captured in the closure.
MessageHandler = Callable[[str, MessageEnvelope], Awaitable[None]]


# ---------------------------------------------------------------------------
# RetryTracker — Redis-backed per-message retry counter
# ---------------------------------------------------------------------------


class RetryTracker:
    """Manages Redis-backed retry counters for consumer messages.

    Each message's retry count is stored at ``{prefix}:{stream}:{msg_id}``
    with a configurable TTL so that counters expire after prolonged inactivity.
    """

    def __init__(
        self,
        prefix: str = _RETRY_KEY_PREFIX,
        ttl: int = _RETRY_KEY_TTL,
    ) -> None:
        self._prefix = prefix
        self._ttl = ttl

    def key(self, stream: str, msg_id: str) -> str:
        """Build the Redis key for a message's retry counter."""
        return f"{self._prefix}:{stream}:{msg_id}"

    async def incr(self, r: aioredis.Redis, stream: str, msg_id: str) -> int:
        """Increment the retry counter and return the new value.

        INCR and EXPIRE are wrapped in a MULTI/EXEC transaction so a crash
        between them cannot leave the key without a TTL.
        """
        k = self.key(stream, msg_id)
        async with r.pipeline(transaction=True) as pipe:
            pipe.incr(k)
            pipe.expire(k, self._ttl)
            results: list[int] = await pipe.execute()
        return results[0]

    async def clear(self, r: aioredis.Redis, stream: str, msg_id: str) -> None:
        """Delete the retry counter for a message."""
        await r.delete(self.key(stream, msg_id))


# Module-level singleton used by the handler/dispatch layer.
_tracker = RetryTracker()


# Public aliases so existing imports and tests still work.
def _retry_key(stream: str, msg_id: str) -> str:
    """Build the Redis key for a message's retry counter."""
    return _tracker.key(stream, msg_id)


async def _incr_retry(r: aioredis.Redis, stream: str, msg_id: str) -> int:
    """Increment the Redis-backed retry counter and return the new value."""
    return await _tracker.incr(r, stream, msg_id)


async def _clear_retry(r: aioredis.Redis, stream: str, msg_id: str) -> None:
    """Delete the Redis-backed retry counter for a message."""
    await _tracker.clear(r, stream, msg_id)


async def _nack_with_fallback(
    r: aioredis.Redis,
    stream: str,
    group: str,
    msg_id: str,
    envelope: MessageEnvelope,
    log: logging.Logger,
) -> None:
    """Attempt nack_to_dlq + ACK; on failure, emergency-ACK to prevent PEL loops."""
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


async def _handle_failure(  # noqa: PLR0913
    r: aioredis.Redis,
    stream: str,
    group: str,
    msg_id: str,
    envelope: MessageEnvelope,
    exc: Exception,
    log: logging.Logger,
    max_retries: int,
) -> None:
    """Process a handler failure: increment retry, nack to DLQ if threshold reached."""
    count = await _incr_retry(r, stream, msg_id)
    if count >= max_retries:
        nack_failures = count - max_retries
        if nack_failures >= _MAX_NACK_ATTEMPTS:
            log.error(
                "nack_to_dlq failed %d times — abandoning message",
                nack_failures,
                extra={"msg_id": msg_id, "stream": stream},
            )
            await _clear_retry(r, stream, msg_id)
            return
        log.error(
            "handler crashed %d times — sending to DLQ",
            count,
            extra={"msg_id": msg_id},
        )
        await _nack_with_fallback(r, stream, group, msg_id, envelope, log)
    else:
        log.exception(
            "handler error (%d/%d) [%s]",
            count,
            max_retries,
            type(exc).__name__,
            extra={"msg_id": msg_id},
        )


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
        await _handle_failure(r, stream, group, msg_id, envelope, exc, log, max_retries)


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


def _install_sigterm_handler(log: logging.Logger) -> Callable[[], bool]:
    """Install a SIGTERM handler and return a callable that checks the shutdown flag."""
    shutdown_flag: list[bool] = [False]

    def _sigterm_handler() -> None:
        shutdown_flag[0] = True
        log.info("SIGTERM received — shutting down gracefully")

    loop = asyncio.get_running_loop()
    with contextlib.suppress(NotImplementedError, OSError):
        loop.add_signal_handler(signal.SIGTERM, _sigterm_handler)

    return lambda: shutdown_flag[0]


async def _should_exit(
    r: aioredis.Redis, log: logging.Logger, is_shutdown: Callable[[], bool]
) -> bool:
    """Return True if the consumer should stop (shutdown flag or system:halted)."""
    if is_shutdown():
        log.info("shutdown flag set — exiting")
        return True
    if await r.get("system:halted"):
        log.critical("system halted — exiting")
        return True
    return False


def _log_idle(idle_polls: int, log: logging.Logger, stream: str) -> int:
    """Increment idle poll counter and log periodically. Return new count."""
    idle_polls += 1
    if idle_polls % 12 == 1:
        log.debug("waiting for messages", extra={"stream": stream})
    return idle_polls


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
    is_shutdown = _install_sigterm_handler(log)
    idle_polls = 0
    while True:
        if await _should_exit(r, log, is_shutdown):
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
        idle_polls = 0 if messages else _log_idle(idle_polls, log, stream)
        await _dispatch_batch(
            r,
            stream,
            group,
            messages,
            handler,
            log,
            shutdown_check=is_shutdown,
        )
