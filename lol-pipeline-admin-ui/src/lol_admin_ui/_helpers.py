"""Shared helpers for admin-ui route handlers.

Pure Redis operations extracted from main.py (PRIN-AUI-01) so that
route handlers stay thin and helpers remain independently testable.
"""

from __future__ import annotations

from redis.asyncio import Redis


async def list_dlq_entries(r: Redis) -> list[dict[str, str]]:
    """Return all DLQ entries as flat dicts with ``id`` field included."""
    raw_entries: list[tuple[str, dict[str, str]]] = await r.xrange("stream:dlq")
    entries: list[dict[str, str]] = []
    for entry_id, fields in raw_entries:
        entry: dict[str, str] = {"id": entry_id, **fields}
        entries.append(entry)
    return entries


async def replay_entry(
    r: Redis,
    message_id: str,
) -> tuple[str, str] | None:
    """Replay a single DLQ entry back to its original stream.

    Returns ``(entry_id, original_stream)`` on success, ``None`` when the
    message is not found.  Raises ``ValueError`` when the entry has no
    ``original_stream`` field.
    """
    entries: list[tuple[str, dict[str, str]]] = await r.xrange(
        "stream:dlq", min=message_id, max=message_id, count=1
    )
    if not entries:
        return None

    entry_id, fields = entries[0]
    original_stream = fields.get("original_stream", "")
    if not original_stream:
        raise ValueError("no original_stream field")

    publish_fields: dict[str, str] = {k: v for k, v in fields.items() if k != "original_stream"}
    await r.xadd(original_stream, publish_fields)  # type: ignore[arg-type]
    await r.xdel("stream:dlq", entry_id)
    return entry_id, original_stream


async def clear_dlq(r: Redis) -> None:
    """Remove all entries from stream:dlq."""
    await r.delete("stream:dlq")


async def set_system_halted(r: Redis) -> None:
    """Set the system:halted flag in Redis."""
    await r.set("system:halted", "1")


async def clear_system_halted(r: Redis) -> None:
    """Remove the system:halted flag from Redis."""
    await r.delete("system:halted")
