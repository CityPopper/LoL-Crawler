"""
End-to-end tests for the LoL Match Intelligence Pipeline.

Requires:
- A running stack (just up)
- A valid RIOT_API_KEY in .env
- Internet access to Riot API

Run with:  just e2e

These tests use Pwnerer#1337 as the canonical test subject.
They are slow (up to 5 minutes for a full round-trip) and hit the real Riot API.
"""

from __future__ import annotations

import asyncio
import os
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import pytest

_DEPLOY_ENV = Path(__file__).parent.parent.parent / ".env"
if _DEPLOY_ENV.exists():
    for _line in _DEPLOY_ENV.read_text().splitlines():
        _line = _line.strip()
        if "=" in _line and not _line.startswith("#"):
            _k, _, _v = _line.partition("=")
            os.environ.setdefault(_k.strip(), _v.strip())

_COMMON_SRC = Path(__file__).parent.parent.parent / "lol-pipeline-common" / "src"
if str(_COMMON_SRC) not in sys.path:
    sys.path.insert(0, str(_COMMON_SRC))

from lol_pipeline.config import Config  # noqa: E402
from lol_pipeline.models import MessageEnvelope  # noqa: E402
from lol_pipeline.redis_client import get_redis  # noqa: E402
from lol_pipeline.riot_api import RiotClient  # noqa: E402
from lol_pipeline.streams import publish  # noqa: E402

_GAME_NAME = "Pwnerer"
_TAG_LINE = "1337"
_REGION = "na1"
_POLL_INTERVAL_S = 5
_MAX_WAIT_S = 300  # 5 minutes — covers crawl + fetch + parse + analyze


def _redis_url() -> str:
    """Return Redis URL suitable for running outside Docker."""
    url = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
    return url.replace("redis://redis:", "redis://localhost:")


@pytest.mark.asyncio
@pytest.mark.e2e
async def test_seed_publishes_to_stream() -> None:
    """Seeding a player appends a message to stream:puuid."""
    cfg = Config()
    r = get_redis(_redis_url())
    riot = RiotClient(cfg.riot_api_key)
    try:
        account = await riot.get_account_by_riot_id(_GAME_NAME, _TAG_LINE, _REGION)
        puuid: str = account["puuid"]

        before = await r.xlen("stream:puuid")  # type: ignore[misc]

        # Clear cooldown so seed is not skipped
        await r.hdel(f"player:{puuid}", "seeded_at", "last_crawled_at")  # type: ignore[misc]

        now_iso = datetime.now(tz=UTC).isoformat()
        await r.hset(  # type: ignore[misc]
            f"player:{puuid}",
            mapping={"game_name": _GAME_NAME, "tag_line": _TAG_LINE, "region": _REGION, "seeded_at": now_iso},
        )
        envelope = MessageEnvelope(
            source_stream="stream:puuid",
            type="puuid",
            payload={"puuid": puuid, "game_name": _GAME_NAME, "tag_line": _TAG_LINE, "region": _REGION},
            max_attempts=cfg.max_attempts,
        )
        await publish(r, "stream:puuid", envelope)

        after = await r.xlen("stream:puuid")  # type: ignore[misc]
        assert after > before, "stream:puuid did not grow"
    finally:
        await r.aclose()
        await riot.close()


@pytest.mark.asyncio
@pytest.mark.e2e
@pytest.mark.slow
async def test_full_pipeline_produces_stats() -> None:
    """Full pipeline: seed → crawl → fetch → parse → analyze → stats in Redis."""
    cfg = Config()
    r = get_redis(_redis_url())
    riot = RiotClient(cfg.riot_api_key)
    try:
        account = await riot.get_account_by_riot_id(_GAME_NAME, _TAG_LINE, _REGION)
        puuid: str = account["puuid"]

        # Clear state so pipeline re-processes
        await r.hdel(f"player:{puuid}", "seeded_at", "last_crawled_at")  # type: ignore[misc]
        await r.delete(f"player:stats:{puuid}")
        await r.set(f"player:stats:cursor:{puuid}", "0")

        # Publish to stream:puuid (same as seed service would do)
        envelope = MessageEnvelope(
            source_stream="stream:puuid",
            type="puuid",
            payload={"puuid": puuid, "game_name": _GAME_NAME, "tag_line": _TAG_LINE, "region": _REGION},
            max_attempts=cfg.max_attempts,
        )
        await publish(r, "stream:puuid", envelope)

        # Poll until stats appear or timeout
        deadline = time.time() + _MAX_WAIT_S
        stats: dict[str, str] = {}
        while time.time() < deadline:
            stats = await r.hgetall(f"player:stats:{puuid}")  # type: ignore[misc]
            if stats.get("total_games"):
                break
            await asyncio.sleep(_POLL_INTERVAL_S)

        assert stats, (
            f"No stats for {_GAME_NAME}#{_TAG_LINE} after {_MAX_WAIT_S}s — check: just logs crawler"
        )
        assert int(stats["total_games"]) > 0
        assert "kda" in stats
        assert "win_rate" in stats
        assert "avg_kills" in stats

    finally:
        await r.aclose()
        await riot.close()
