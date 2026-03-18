"""Shared helpers for Seed contract tests."""

import json
from pathlib import Path

_SERVICE_ROOT = Path(__file__).parent.parent.parent
_COMMON_ROOT = _SERVICE_ROOT.parent / "lol-pipeline-common"

# Seed is a provider — consumer pacts live in sibling service repos
CONSUMER_PACTS = {
    "crawler": _SERVICE_ROOT.parent / "lol-pipeline-crawler" / "pacts",
}
SCHEMAS_DIR = _COMMON_ROOT / "contracts" / "schemas"


def load_consumer_pact(consumer: str, filename: str) -> dict:
    return json.loads((CONSUMER_PACTS[consumer] / filename).read_text())


def load_schema(relative_path: str) -> dict:
    return json.loads((SCHEMAS_DIR / relative_path).read_text())


def to_redis_format(contents: dict) -> dict:
    """Convert a typed pact example message to flat Redis string format."""
    result = {}
    for key, value in contents.items():
        if key == "payload":
            result[key] = json.dumps(value)
        elif value is None:
            result[key] = "null"
        else:
            result[key] = str(value)
    return result
