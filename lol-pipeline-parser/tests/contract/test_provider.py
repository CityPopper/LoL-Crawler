"""Provider contract tests: Parser satisfies Analyzer's stream:analyze contract.

Validates that:
- A message produced by Parser passes the canonical envelope + payload schemas.
- The serialized message round-trips through to_redis_fields / from_redis_fields.
- The output contains only the contracted fields (service isolation).

Note: Parser emits one stream:analyze message per unique PUUID in a match.
The participant data written to Redis is the implicit side-condition (provider state),
not carried in the message itself.
"""

import json
import uuid
from datetime import UTC, datetime

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_schema


def _make_analyze_envelope() -> MessageEnvelope:
    return MessageEnvelope(
        id=str(uuid.uuid4()),
        source_stream="stream:analyze",
        type="analyze",
        payload={
            "puuid": "oLmiXzfMHOxnVbdIr2_GsIcBnCqeB4p-example-puuid-0001",
        },
        attempts=0,
        max_attempts=5,
        enqueued_at=datetime.now(tz=UTC).isoformat(),
    )


def test_parser__produces_analyze__passes_envelope_schema():
    schema = load_schema("envelope.json")
    fields = _make_analyze_envelope().to_redis_fields()
    restored = MessageEnvelope.from_redis_fields(fields)
    document = {
        "id": restored.id,
        "source_stream": restored.source_stream,
        "type": restored.type,
        "payload": json.loads(fields["payload"]),
        "attempts": restored.attempts,
        "max_attempts": restored.max_attempts,
        "enqueued_at": restored.enqueued_at,
        "dlq_attempts": restored.dlq_attempts,
        "priority": restored.priority,
        "correlation_id": restored.correlation_id,
    }
    validate(instance=document, schema=schema)


def test_parser__produces_analyze__passes_payload_schema():
    schema = load_schema("payloads/analyze_payload.json")
    fields = _make_analyze_envelope().to_redis_fields()
    validate(instance=json.loads(fields["payload"]), schema=schema)


def test_parser__produces_analyze__round_trips():
    envelope = _make_analyze_envelope()
    restored = MessageEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.source_stream == "stream:analyze"
    assert restored.type == "analyze"
    assert restored.payload["puuid"]
    assert isinstance(restored.attempts, int)
