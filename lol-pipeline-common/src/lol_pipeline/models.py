"""MessageEnvelope and DLQEnvelope — pipeline message contracts."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Shared serialization helpers — DRY base for both envelope classes
# ---------------------------------------------------------------------------


def _common_to_redis(obj: MessageEnvelope | DLQEnvelope) -> dict[str, str]:
    """Serialize the fields shared by both envelope types."""
    return {
        "id": obj.id,
        "source_stream": obj.source_stream,
        "type": obj.type,
        "payload": json.dumps(obj.payload),
        "attempts": str(obj.attempts),
        "max_attempts": str(obj.max_attempts),
        "enqueued_at": obj.enqueued_at,
        "dlq_attempts": str(obj.dlq_attempts),
        "priority": obj.priority,
        "correlation_id": obj.correlation_id,
        "defer_count": str(obj.defer_count),
    }


def _common_from_redis(fields: dict[str, str]) -> dict[str, Any]:
    """Deserialize the fields shared by both envelope types."""
    return {
        "id": fields["id"],
        "source_stream": fields["source_stream"],
        "type": fields["type"],
        "payload": json.loads(fields["payload"]),
        "attempts": int(fields["attempts"]),
        "max_attempts": int(fields["max_attempts"]),
        "enqueued_at": fields["enqueued_at"],
        "dlq_attempts": int(fields.get("dlq_attempts", "0")),
        "priority": fields.get("priority", "normal"),
        "correlation_id": fields.get("correlation_id", ""),
        "defer_count": int(fields.get("defer_count", "0")),
    }


# ---------------------------------------------------------------------------
# Envelope dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MessageEnvelope:
    """A message moving through a pipeline stream."""

    source_stream: str
    type: str
    payload: dict[str, Any]
    max_attempts: int
    id: str = field(default_factory=_new_id)
    attempts: int = 0
    enqueued_at: str = field(default_factory=_now_iso)
    dlq_attempts: int = 0
    priority: str = "normal"
    correlation_id: str = ""
    defer_count: int = 0

    def to_redis_fields(self) -> dict[str, str]:
        return _common_to_redis(self)

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> MessageEnvelope:
        return cls(**_common_from_redis(fields))


@dataclass
class DLQEnvelope:
    """A failed message on the dead-letter queue."""

    source_stream: str  # always "stream:dlq"
    type: str  # always "dlq"
    payload: dict[str, Any]
    attempts: int
    max_attempts: int
    failure_code: str
    failure_reason: str
    failed_by: str
    original_stream: str
    original_message_id: str
    id: str = field(default_factory=_new_id)
    failed_at: str = field(default_factory=_now_iso)
    enqueued_at: str = field(default_factory=_now_iso)
    retry_after_ms: int | None = None
    dlq_attempts: int = 0
    priority: str = "normal"
    correlation_id: str = ""
    defer_count: int = 0

    def to_redis_fields(self) -> dict[str, str]:
        base = _common_to_redis(self)
        base.update(
            {
                "failure_code": self.failure_code,
                "failure_reason": self.failure_reason,
                "failed_by": self.failed_by,
                "original_stream": self.original_stream,
                "original_message_id": self.original_message_id,
                "failed_at": self.failed_at,
                "retry_after_ms": (
                    "null" if self.retry_after_ms is None else str(self.retry_after_ms)
                ),
            }
        )
        return base

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> DLQEnvelope:
        base = _common_from_redis(fields)
        ram = fields.get("retry_after_ms", "null")
        base.update(
            {
                "failure_code": fields["failure_code"],
                "failure_reason": fields.get("failure_reason", ""),
                "failed_by": fields.get("failed_by", ""),
                "original_stream": fields["original_stream"],
                "original_message_id": fields["original_message_id"],
                "failed_at": fields["failed_at"],
                "retry_after_ms": None if ram == "null" else int(ram),
            }
        )
        return cls(**base)


def make_replay_envelope(dlq: DLQEnvelope, max_attempts: int) -> MessageEnvelope:
    """Reconstruct a MessageEnvelope from a DLQEnvelope for replay."""
    original_type = dlq.original_stream.removeprefix("stream:")
    return MessageEnvelope(
        source_stream=dlq.original_stream,
        type=original_type,
        payload=dlq.payload,
        max_attempts=max_attempts,
        enqueued_at=dlq.enqueued_at,
        dlq_attempts=dlq.dlq_attempts,
        priority=dlq.priority,
        correlation_id=dlq.correlation_id,
        defer_count=dlq.defer_count,
    )
