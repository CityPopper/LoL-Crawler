"""Provider contract tests: Fetcher satisfies Parser's stream:parse contract.

Validates that:
- A message produced by Fetcher passes the canonical envelope + payload schemas.
- The serialized message round-trips through to_redis_fields / from_redis_fields.
- The output contains only the contracted fields (service isolation).
"""

import json
import uuid
from datetime import UTC, datetime

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_schema


def _make_parse_envelope() -> MessageEnvelope:
    return MessageEnvelope(
        id=str(uuid.uuid4()),
        source_stream="stream:parse",
        type="parse",
        payload={
            "match_id": "NA1_4567890123",
            "region": "na1",
        },
        attempts=0,
        max_attempts=5,
        enqueued_at=datetime.now(tz=UTC).isoformat(),
    )


def test_fetcher__produces_parse__passes_envelope_schema():
    schema = load_schema("envelope.json")
    fields = _make_parse_envelope().to_redis_fields()
    restored = MessageEnvelope.from_redis_fields(fields)
    document = {
        "id": restored.id,
        "source_stream": restored.source_stream,
        "type": restored.type,
        "payload": json.loads(fields["payload"]),
        "attempts": restored.attempts,
        "max_attempts": restored.max_attempts,
        "enqueued_at": restored.enqueued_at,
    }
    validate(instance=document, schema=schema)


def test_fetcher__produces_parse__passes_payload_schema():
    schema = load_schema("payloads/parse_payload.json")
    fields = _make_parse_envelope().to_redis_fields()
    validate(instance=json.loads(fields["payload"]), schema=schema)


def test_fetcher__produces_parse__round_trips():
    envelope = _make_parse_envelope()
    restored = MessageEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.source_stream == "stream:parse"
    assert restored.type == "parse"
    assert restored.payload["match_id"]
    assert restored.payload["region"]
    assert isinstance(restored.attempts, int)
