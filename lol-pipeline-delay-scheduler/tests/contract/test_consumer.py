"""Consumer contract tests: Delay Scheduler consuming delayed:messages from lol-pipeline-common.

Validates that:
- Each example delayed message passes the envelope schema.
- MessageEnvelope.from_redis_fields() deserializes each example without error.
- The Delay Scheduler only accesses fields it needs: source_stream and the full envelope
  for re-publishing. It does NOT inspect payload contents (service isolation).
"""

import pytest
from jsonschema import validate
from lol_pipeline.models import MessageEnvelope

from .conftest import load_pact, load_schema, to_redis_format

_PACT_FILE = "delay-scheduler-common.json"

_VALID_SOURCE_STREAMS = {"stream:puuid", "stream:match_id", "stream:parse", "stream:analyze"}


def test_delay_scheduler__all_example_messages_present():
    pact = load_pact(_PACT_FILE)
    assert len(pact["messages"]) >= 2, "Pact must contain at least 2 delayed message examples"


@pytest.mark.parametrize("msg_index", [0, 1])
def test_delay_scheduler__delayed_message__passes_envelope_schema(msg_index):
    pact = load_pact(_PACT_FILE)
    schema = load_schema("envelope.json")
    message = pact["messages"][msg_index]["contents"]
    validate(instance=message, schema=schema)


@pytest.mark.parametrize("msg_index", [0, 1])
def test_delay_scheduler__delayed_message__deserializes_without_error(msg_index):
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][msg_index]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    assert envelope.source_stream in _VALID_SOURCE_STREAMS
    assert envelope.type
    assert envelope.id
    assert isinstance(envelope.attempts, int)
    assert isinstance(envelope.max_attempts, int)
    assert envelope.enqueued_at


@pytest.mark.parametrize("msg_index", [0, 1])
def test_delay_scheduler__delayed_message__source_stream_is_valid_target(msg_index):
    pact = load_pact(_PACT_FILE)
    contents = pact["messages"][msg_index]["contents"]
    envelope = MessageEnvelope.from_redis_fields(to_redis_format(contents))
    # Delay Scheduler publishes to source_stream — must be a real pipeline stream
    assert envelope.source_stream in _VALID_SOURCE_STREAMS
