"""Unit tests for fetcher helpers — _set_match_status and _constants (PRIN-FET-01)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from lol_fetcher._constants import GROUP, IN_STREAM, OUT_STREAM
from lol_fetcher.main import _set_match_status


@pytest.fixture
async def r():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield redis
    await redis.flushall()
    await redis.aclose()


class TestFetcherConstants:
    """PRIN-FET-01: Stream names and group are defined in _constants.py."""

    def test_in_stream__value(self):
        assert IN_STREAM == "stream:match_id"

    def test_out_stream__value(self):
        assert OUT_STREAM == "stream:parse"

    def test_group__value(self):
        assert GROUP == "fetchers"


class TestSetMatchStatus:
    """PRIN-FET-01: _set_match_status helper sets hash field and TTL."""

    async def test_set_match_status__sets_status_field(self, r):
        await _set_match_status(r, "NA1_100", "fetched", 3600)
        status = await r.hget("match:NA1_100", "status")
        assert status == "fetched"

    async def test_set_match_status__sets_ttl(self, r):
        await _set_match_status(r, "NA1_100", "fetched", 3600)
        ttl = await r.ttl("match:NA1_100")
        assert 0 < ttl <= 3600

    async def test_set_match_status__different_status_value(self, r):
        await _set_match_status(r, "NA1_200", "not_found", 7200)
        status = await r.hget("match:NA1_200", "status")
        assert status == "not_found"

    async def test_set_match_status__overwrites_existing(self, r):
        await _set_match_status(r, "NA1_100", "fetched", 3600)
        await _set_match_status(r, "NA1_100", "not_found", 3600)
        status = await r.hget("match:NA1_100", "status")
        assert status == "not_found"
