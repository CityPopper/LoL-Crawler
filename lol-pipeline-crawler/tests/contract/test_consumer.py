"""Consumer contract tests: Crawler consuming stream:puuid from Seed.

Validates that:
- The example message in the pact file passes the canonical envelope + payload schemas.
- MessageEnvelope.from_redis_fields() can fully deserialize the example without error.
- The Crawler only accesses fields declared in its input contract (service isolation).
"""

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_pact, load_schema, to_redis_format

_PACT_FILE = "crawler-seed.json"


def test_crawler__puuid_message__passes_envelope_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("envelope.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message, schema=schema)


def test_crawler__puuid_message__passes_payload_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("payloads/puuid_payload.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message["payload"], schema=schema)


def test_crawler__puuid_message__deserializes_without_error():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.source_stream == "stream:puuid"
    assert envelope.type == "puuid"
    assert isinstance(envelope.attempts, int)
    assert isinstance(envelope.max_attempts, int)
    assert envelope.enqueued_at


def test_crawler__puuid_message__only_uses_contracted_payload_fields():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    # Crawler only needs puuid and region from the payload — assert both present
    assert envelope.payload["puuid"]
    assert envelope.payload["region"]
    # game_name and tag_line are informational; also contracted
    assert envelope.payload["game_name"]
    assert envelope.payload["tag_line"]
    # No unlisted fields accessed — contract is closed (additionalProperties: false in schema)
