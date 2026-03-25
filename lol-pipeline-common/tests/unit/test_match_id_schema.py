"""Unit tests for match_id_payload.json contract schema."""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA_PATH = (
    Path(__file__).parent.parent.parent / "contracts/schemas/payloads/match_id_payload.json"
)


@pytest.fixture
def schema():
    return json.loads(SCHEMA_PATH.read_text())


class TestMatchIdSchemaSource:
    def test_source_riot_is_valid(self, schema):
        """source='riot' is a valid match_id payload."""
        payload = {
            "match_id": "NA1_1234567890",
            "puuid": "abc-puuid",
            "region": "na1",
            "source": "riot",
        }
        jsonschema.validate(payload, schema)  # should not raise

    def test_source_opgg_is_valid(self, schema):
        """source='opgg' is a valid match_id payload."""
        payload = {
            "match_id": "NA1_1234567890",
            "puuid": "abc-puuid",
            "region": "na1",
            "source": "opgg",
        }
        jsonschema.validate(payload, schema)  # should not raise

    def test_source_invalid_enum_fails(self, schema):
        """source='unknown' is rejected (not in enum)."""
        payload = {
            "match_id": "NA1_1234567890",
            "puuid": "abc-puuid",
            "region": "na1",
            "source": "unknown",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(payload, schema)

    def test_source_is_optional(self, schema):
        """source field is optional — existing messages without it still pass."""
        payload = {
            "match_id": "NA1_1234567890",
            "puuid": "abc-puuid",
            "region": "na1",
        }
        jsonschema.validate(payload, schema)  # should not raise
