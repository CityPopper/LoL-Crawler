"""Shared helpers for Crawler contract tests."""

from pathlib import Path

from lol_pipeline.contracts.test_helpers import load_pact as _load_pact
from lol_pipeline.contracts.test_helpers import load_schema, to_redis_format

__all__ = ["load_pact", "load_schema", "to_redis_format"]

_PACTS_DIR = Path(__file__).parent.parent.parent / "pacts"


def load_pact(filename: str) -> dict:
    return _load_pact(_PACTS_DIR, filename)
