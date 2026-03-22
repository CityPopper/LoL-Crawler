"""Shared helpers for Common library contract tests."""

from pathlib import Path

from lol_pipeline.contracts.test_helpers import (
    load_consumer_pact as _load_consumer_pact,
)
from lol_pipeline.contracts.test_helpers import load_schema, to_redis_format

__all__ = ["load_consumer_pact", "load_schema", "to_redis_format"]

_COMMON_ROOT = Path(__file__).parent.parent.parent

# Common is a provider — consumer pacts live in sibling service repos
_PIPELINE_ROOT = _COMMON_ROOT.parent
_CONSUMER_PACTS = {
    "recovery": _PIPELINE_ROOT / "lol-pipeline-recovery" / "pacts",
    "delay-scheduler": _PIPELINE_ROOT / "lol-pipeline-delay-scheduler" / "pacts",
}


def load_consumer_pact(consumer: str, filename: str) -> dict:
    return _load_consumer_pact(_CONSUMER_PACTS, consumer, filename)
