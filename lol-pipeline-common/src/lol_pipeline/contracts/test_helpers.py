"""Shared helpers for contract tests across all pipeline services.

Provides:
- ``load_schema``         — load a canonical JSON schema from ``contracts/schemas/``
- ``load_pact``           — load a consumer pact JSON from a service's ``pacts/`` dir
- ``load_consumer_pact``  — load a consumer pact from another service (provider tests)
- ``to_redis_format``     — convert a typed pact example to flat Redis string format
"""

from __future__ import annotations

import json
from pathlib import Path

# contracts/schemas/ lives alongside src/ inside lol-pipeline-common/
_COMMON_ROOT = Path(__file__).resolve().parent.parent.parent.parent
SCHEMAS_DIR = _COMMON_ROOT / "contracts" / "schemas"


def load_schema(relative_path: str) -> dict:  # type: ignore[type-arg]
    """Load a canonical JSON schema by path relative to ``contracts/schemas/``."""
    return json.loads((SCHEMAS_DIR / relative_path).read_text())  # type: ignore[no-any-return]


def load_pact(pacts_dir: Path, filename: str) -> dict:  # type: ignore[type-arg]
    """Load a consumer pact JSON file from a service's ``pacts/`` directory."""
    return json.loads((pacts_dir / filename).read_text())  # type: ignore[no-any-return]


def load_consumer_pact(
    consumer_pacts: dict[str, Path],
    consumer: str,
    filename: str,
) -> dict:  # type: ignore[type-arg]
    """Load a consumer pact from another service (used in provider contract tests)."""
    return json.loads(  # type: ignore[no-any-return]
        (consumer_pacts[consumer] / filename).read_text()
    )


def to_redis_format(contents: dict) -> dict:  # type: ignore[type-arg]
    """Convert a typed pact example message to flat Redis string format.

    - ``payload`` values are JSON-encoded.
    - ``None`` becomes the string ``"null"``.
    - Everything else becomes ``str(value)``.
    """
    result: dict[str, str] = {}
    for key, value in contents.items():
        if key == "payload":
            result[key] = json.dumps(value)
        elif value is None:
            result[key] = "null"
        else:
            result[key] = str(value)
    return result
