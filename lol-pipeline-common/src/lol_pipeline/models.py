"""MessageEnvelope and DLQEnvelope — pipeline message contracts."""

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


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

    def to_redis_fields(self) -> dict[str, str]:
        return {
            "id": self.id,
            "source_stream": self.source_stream,
            "type": self.type,
            "payload": json.dumps(self.payload),
            "attempts": str(self.attempts),
            "max_attempts": str(self.max_attempts),
            "enqueued_at": self.enqueued_at,
            "dlq_attempts": str(self.dlq_attempts),
            "priority": self.priority,
        }

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> MessageEnvelope:
        return cls(
            id=fields["id"],
            source_stream=fields["source_stream"],
            type=fields["type"],
            payload=json.loads(fields["payload"]),
            attempts=int(fields["attempts"]),
            max_attempts=int(fields["max_attempts"]),
            enqueued_at=fields["enqueued_at"],
            dlq_attempts=int(fields.get("dlq_attempts", "0")),
            priority=fields.get("priority", "normal"),
        )


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

    def to_redis_fields(self) -> dict[str, str]:
        return {
            "id": self.id,
            "source_stream": self.source_stream,
            "type": self.type,
            "payload": json.dumps(self.payload),
            "attempts": str(self.attempts),
            "max_attempts": str(self.max_attempts),
            "failure_code": self.failure_code,
            "failure_reason": self.failure_reason,
            "failed_by": self.failed_by,
            "original_stream": self.original_stream,
            "original_message_id": self.original_message_id,
            "failed_at": self.failed_at,
            "enqueued_at": self.enqueued_at,
            "dlq_attempts": str(self.dlq_attempts),
            "retry_after_ms": "null" if self.retry_after_ms is None else str(self.retry_after_ms),
            "priority": self.priority,
        }

    @classmethod
    def from_redis_fields(cls, fields: dict[str, str]) -> DLQEnvelope:
        ram = fields.get("retry_after_ms", "null")
        return cls(
            id=fields["id"],
            source_stream=fields["source_stream"],
            type=fields["type"],
            payload=json.loads(fields["payload"]),
            attempts=int(fields["attempts"]),
            max_attempts=int(fields["max_attempts"]),
            failure_code=fields["failure_code"],
            failure_reason=fields.get("failure_reason", ""),
            failed_by=fields.get("failed_by", ""),
            original_stream=fields.get("original_stream", ""),
            original_message_id=fields.get("original_message_id", ""),
            failed_at=fields["failed_at"],
            enqueued_at=fields["enqueued_at"],
            retry_after_ms=None if ram == "null" else int(ram),
            dlq_attempts=int(fields.get("dlq_attempts", "0")),
            priority=fields.get("priority", "normal"),
        )
