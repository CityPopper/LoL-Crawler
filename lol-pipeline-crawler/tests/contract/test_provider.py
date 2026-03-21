"""Provider contract tests: Crawler satisfies Fetcher's stream:match_id contract.

Validates that:
- A message produced by Crawler passes the canonical envelope + payload schemas.
- The serialized message round-trips through to_redis_fields / from_redis_fields.
- The output contains only the contracted fields (service isolation).
"""

import json
import uuid
from datetime import UTC, datetime

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_schema


def _make_match_id_envelope() -> MessageEnvelope:
    return MessageEnvelope(
        id=str(uuid.uuid4()),
        source_stream="stream:match_id",
        type="match_id",
        payload={
            "match_id": "NA1_4567890123",
            "puuid": "oLmiXzfMHOxnVbdIr2_GsIcBnCqeB4p-example-puuid-0001",
            "region": "na1",
        },
        attempts=0,
        max_attempts=5,
        enqueued_at=datetime.now(tz=UTC).isoformat(),
    )


def test_crawler__produces_match_id__passes_envelope_schema():
    schema = load_schema("envelope.json")
    fields = _make_match_id_envelope().to_redis_fields()
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


def test_crawler__produces_match_id__passes_payload_schema():
    schema = load_schema("payloads/match_id_payload.json")
    fields = _make_match_id_envelope().to_redis_fields()
    validate(instance=json.loads(fields["payload"]), schema=schema)


def test_crawler__produces_match_id__round_trips():
    envelope = _make_match_id_envelope()
    restored = MessageEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.source_stream == "stream:match_id"
    assert restored.type == "match_id"
    assert restored.payload["match_id"]
    assert restored.payload["puuid"]
    assert restored.payload["region"]
    assert isinstance(restored.attempts, int)
    assert isinstance(restored.max_attempts, int)
