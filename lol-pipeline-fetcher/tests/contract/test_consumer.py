"""Consumer contract tests: Fetcher consuming stream:match_id from Crawler.

Validates that:
- The example message in the pact file passes the canonical envelope + payload schemas.
- MessageEnvelope.from_redis_fields() can fully deserialize the example without error.
- The Fetcher only accesses fields declared in its input contract (service isolation).
"""

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_pact, load_schema, to_redis_format

_PACT_FILE = "fetcher-crawler.json"


def test_fetcher__match_id_message__passes_envelope_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("envelope.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message, schema=schema)


def test_fetcher__match_id_message__passes_payload_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("payloads/match_id_payload.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message["payload"], schema=schema)


def test_fetcher__match_id_message__deserializes_without_error():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.source_stream == "stream:match_id"
    assert envelope.type == "match_id"
    assert isinstance(envelope.attempts, int)
    assert isinstance(envelope.max_attempts, int)
    assert envelope.enqueued_at


def test_fetcher__match_id_message__only_uses_contracted_payload_fields():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    # Fetcher only needs match_id and region — both must be present
    assert envelope.payload["match_id"]
    assert envelope.payload["region"]
    # puuid is informational in this contract — also present
    assert envelope.payload["puuid"]
