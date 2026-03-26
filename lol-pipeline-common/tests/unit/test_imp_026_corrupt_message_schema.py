"""IMP-026: dlq_envelope.json schema includes 'corrupt_message' failure code."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import jsonschema.validators


class TestCorruptMessageInSchema:
    def test_corrupt_message_in_failure_code_enum(self):
        """The failure_code enum in dlq_envelope.json includes 'corrupt_message'."""
        schema_path = (
            Path(__file__).parent.parent.parent / "contracts" / "schemas" / "dlq_envelope.json"
        )
        schema = json.loads(schema_path.read_text())
        enum_values = schema["properties"]["failure_code"]["enum"]
        assert "corrupt_message" in enum_values

    def test_corrupt_message_dlq_entry_validates(self):
        """A DLQ entry with failure_code 'corrupt_message' passes schema validation."""
        schema_dir = Path(__file__).parent.parent.parent / "contracts" / "schemas"
        dlq_schema = json.loads((schema_dir / "dlq_envelope.json").read_text())
        envelope_schema = json.loads((schema_dir / "envelope.json").read_text())

        # Build a store mapping $id to schema content for $ref resolution
        store = {
            dlq_schema["$id"]: dlq_schema,
            envelope_schema["$id"]: envelope_schema,
        }
        resolver = jsonschema.RefResolver.from_schema(
            dlq_schema,
            store=store,
        )

        entry = {
            "id": "12345678-1234-1234-1234-123456789012",
            "source_stream": "stream:dlq",
            "type": "dlq",
            "payload": {"raw": "corrupt data"},
            "attempts": 0,
            "max_attempts": 5,
            "enqueued_at": "2024-01-01T00:00:00+00:00",
            "failure_code": "corrupt_message",
            "failure_reason": "deserialization failed",
            "failed_at": "2024-01-01T00:00:01+00:00",
            "failed_by": "run_consumer",
            "dlq_attempts": 0,
            "retry_after_ms": None,
            "original_stream": "stream:parse",
            "original_message_id": "123-0",
            "priority": "normal",
            "correlation_id": "",
        }
        # Should not raise
        jsonschema.validate(instance=entry, schema=dlq_schema, resolver=resolver)
