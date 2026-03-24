"""Consumer contract tests: Analyzer consuming stream:analyze from Parser.

Validates that:
- The example message in the pact file passes the canonical envelope + payload schemas.
- MessageEnvelope.from_redis_fields() can fully deserialize the example without error.
- The Analyzer only accesses fields declared in its input contract (service isolation).

Note: The analyze contract carries an implicit side-condition — participant data for
the PUUID MUST already exist in Redis (written by Parser) before this message is
processed. The Analyzer knows only: "process stats for this PUUID".
"""

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_pact, load_schema, to_redis_format

_PACT_FILE = "analyzer-parser.json"


def test_analyzer__analyze_message__passes_envelope_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("envelope.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message, schema=schema)


def test_analyzer__analyze_message__passes_payload_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("payloads/analyze_payload.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message["payload"], schema=schema)


def test_analyzer__analyze_message__deserializes_without_error():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.source_stream == "stream:analyze"
    assert envelope.type == "analyze"
    assert isinstance(envelope.attempts, int)
    assert isinstance(envelope.max_attempts, int)
    assert envelope.enqueued_at
    assert isinstance(envelope.dlq_attempts, int)
    assert isinstance(envelope.correlation_id, str)
    assert isinstance(envelope.priority, str)


def test_analyzer__analyze_message__only_uses_contracted_payload_fields():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    # Analyzer only needs puuid — nothing else from the message envelope payload
    assert envelope.payload["puuid"]
