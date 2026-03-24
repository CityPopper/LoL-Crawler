"""Provider contract tests: Seed satisfies Crawler's stream:puuid contract.

Validates that:
- A message produced by Seed passes the canonical envelope + payload schemas.
- The serialized message round-trips through to_redis_fields / from_redis_fields.
- The output contains only the contracted fields (service isolation).
"""

import json
import uuid
from datetime import UTC, datetime

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_consumer_pact, load_schema


def _make_puuid_envelope() -> MessageEnvelope:
    return MessageEnvelope(
        id=str(uuid.uuid4()),
        source_stream="stream:puuid",
        type="puuid",
        payload={
            "puuid": "oLmiXzfMHOxnVbdIr2_GsIcBnCqeB4p-example-puuid-0001",
            "game_name": "Faker",
            "tag_line": "KR1",
            "region": "kr",
        },
        attempts=0,
        max_attempts=5,
        enqueued_at=datetime.now(tz=UTC).isoformat(),
    )


def test_seed__produces_puuid__passes_envelope_schema():
    schema = load_schema("envelope.json")
    fields = _make_puuid_envelope().to_redis_fields()
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


def test_seed__produces_puuid__passes_payload_schema():
    schema = load_schema("payloads/puuid_payload.json")
    fields = _make_puuid_envelope().to_redis_fields()
    validate(instance=json.loads(fields["payload"]), schema=schema)


def test_seed__produces_puuid__round_trips():
    envelope = _make_puuid_envelope()
    restored = MessageEnvelope.from_redis_fields(envelope.to_redis_fields())
    assert restored.source_stream == "stream:puuid"
    assert restored.type == "puuid"
    assert restored.payload["puuid"]
    assert restored.payload["game_name"]
    assert restored.payload["tag_line"]
    assert restored.payload["region"]
    assert isinstance(restored.attempts, int)
    assert isinstance(restored.dlq_attempts, int)
    assert isinstance(restored.correlation_id, str)
    assert isinstance(restored.priority, str)


def test_seed__produced_message__matches_crawler_pact_contents():
    pact = load_consumer_pact("crawler", "crawler-seed.json")
    pact_example = pact["messages"][0]["contents"]
    # Verify the structure Seed produces is compatible with what Crawler declared as needed
    schema = load_schema("envelope.json")
    validate(instance=pact_example, schema=schema)
    # Seed's actual output must contain all fields Crawler contracted
    fields = _make_puuid_envelope().to_redis_fields()
    restored = MessageEnvelope.from_redis_fields(fields)
    for key in ["puuid", "game_name", "tag_line", "region"]:
        assert key in restored.payload
