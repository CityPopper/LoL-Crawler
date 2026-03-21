"""Shared helpers for Fetcher contract tests."""

import json
from pathlib import Path

_SERVICE_ROOT = Path(__file__).parent.parent.parent
_COMMON_ROOT = _SERVICE_ROOT.parent / "lol-pipeline-common"

PACTS_DIR = _SERVICE_ROOT / "pacts"
SCHEMAS_DIR = _COMMON_ROOT / "contracts" / "schemas"


def load_pact(filename: str) -> dict:
    return json.loads((PACTS_DIR / filename).read_text())


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
