"""Shared constants and helpers for integration tests."""

from __future__ import annotations

import copy
import logging
import sys
from pathlib import Path
from typing import Any

import redis.asyncio as aioredis

# ---------------------------------------------------------------------------
# Ensure all service packages are importable
# ---------------------------------------------------------------------------
_ROOT = Path(__file__).parent.parent.parent
for name in [
    "lol-pipeline-common",
    "lol-pipeline-seed",
    "lol-pipeline-crawler",
    "lol-pipeline-fetcher",
    "lol-pipeline-parser",
    "lol-pipeline-analyzer",
    "lol-pipeline-recovery",
    "lol-pipeline-delay-scheduler",
    "lol-pipeline-discovery",
]:
    src = _ROOT / name / "src"
    if src.exists() and str(src) not in sys.path:
        sys.path.insert(0, str(src))

from lol_pipeline.models import MessageEnvelope  # noqa: E402
from lol_pipeline.streams import consume  # noqa: E402

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
FIXTURES = _ROOT / "lol-pipeline-common" / "tests" / "fixtures"
PUUID = "test-puuid-0001"
GAME_NAME = "TestPlayer"
TAG_LINE = "NA1"
REGION = "na1"
MATCH_ID = "NA1_1234567890"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def tlog(name: str) -> logging.Logger:
    """Return a test logger."""
    return logging.getLogger(f"test.{name}")


async def consume_all(
    r: aioredis.Redis,
    stream: str,
    group: str,
    consumer: str,
    count: int = 100,
) -> list[tuple[str, MessageEnvelope]]:
    """Consume all available messages from a stream (short block)."""
    return await consume(r, stream, group, consumer, count=count, block=500)


def make_match(
    base: dict[str, Any], match_id: str, game_start_offset: int = 0
) -> dict[str, Any]:
    """Clone a match fixture with a different matchId and unique gameStartTimestamp."""
    m = copy.deepcopy(base)
    m["metadata"]["matchId"] = match_id
    if game_start_offset:
        m["info"]["gameStartTimestamp"] = (
            base["info"]["gameStartTimestamp"] + game_start_offset
        )
    return m
