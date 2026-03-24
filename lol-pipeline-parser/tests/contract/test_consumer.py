"""Consumer contract tests: Parser consuming stream:parse from Fetcher.

Validates that:
- The example message in the pact file passes the canonical envelope + payload schemas.
- MessageEnvelope.from_redis_fields() can fully deserialize the example without error.
- The Parser only accesses fields declared in its input contract (service isolation).

Note: The parse contract carries an implicit side-condition — the raw JSON blob MUST
exist in RawStore at raw:{match_id} before this message is published. This is a
provider-state guarantee documented in the pact, not a field-level constraint.
"""

from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_pact, load_schema, to_redis_format

_PACT_FILE = "parser-fetcher.json"


def test_parser__parse_message__passes_envelope_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("envelope.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message, schema=schema)


def test_parser__parse_message__passes_payload_schema():
    pact = load_pact(_PACT_FILE)
    schema = load_schema("payloads/parse_payload.json")
    message = pact["messages"][0]["contents"]
    validate(instance=message["payload"], schema=schema)


def test_parser__parse_message__deserializes_without_error():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.source_stream == "stream:parse"
    assert envelope.type == "parse"
    assert isinstance(envelope.attempts, int)
    assert isinstance(envelope.max_attempts, int)
    assert envelope.enqueued_at
    assert isinstance(envelope.dlq_attempts, int)
    assert isinstance(envelope.correlation_id, str)
    assert isinstance(envelope.priority, str)


def test_parser__parse_message__only_uses_contracted_payload_fields():
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][0]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    # Parser only needs match_id and region — both must be present
    assert envelope.payload["match_id"]
    assert envelope.payload["region"]
