"""IT-WF-01 through IT-WF-04 — Source waterfall integration tests.

Tests the WaterfallCoordinator integrated with the Fetcher handler,
using real Redis (testcontainers) and mocked HTTP sources.

IT-WF-01: Single Riot source success -> stream:parse published
IT-WF-02: Riot throttled -> all sources exhausted -> DLQ
IT-WF-03: BlobStore cache hit -> stream:parse published without HTTP call
IT-WF-04: blob_validation_failed -> immediate DLQ (no retry)
"""

from __future__ import annotations

import json

import pytest
import redis.asyncio as aioredis

from helpers import PUUID, REGION, consume_all, tlog
from lol_fetcher.main import _fetch_match
from lol_pipeline.config import Config
from lol_pipeline.models import DLQEnvelope, MessageEnvelope
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient
from lol_pipeline.sources.base import (
    MATCH,
    FetchContext,
    FetchResponse,
    FetchResult,
)
from lol_pipeline.sources.blob_store import BlobStore
from lol_pipeline.sources.coordinator import WaterfallCoordinator
from lol_pipeline.sources.registry import SourceEntry, SourceRegistry
from lol_pipeline.sources.riot.source import RiotExtractor
from lol_pipeline.streams import publish


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_MATCH_A = "NA1_12345"
_MATCH_B = "NA1_99999"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_envelope(
    match_id: str,
    cfg: Config,
    puuid: str = PUUID,
    region: str = REGION,
) -> MessageEnvelope:
    """Build a stream:match_id envelope for the fetcher."""
    return MessageEnvelope(
        source_stream="stream:match_id",
        type="match_id",
        payload={"match_id": match_id, "puuid": puuid, "region": region},
        max_attempts=cfg.max_attempts,
    )


def _valid_riot_blob() -> dict:
    """Minimal valid Riot-shaped blob (passes RiotExtractor.can_extract)."""
    return {
        "info": {
            "gameStartTimestamp": 1700000000000,
            "gameDuration": 1800,
            "gameMode": "CLASSIC",
            "queueId": 420,
            "platformId": "NA1",
            "participants": [],
        },
        "metadata": {
            "matchId": _MATCH_A,
            "participants": [],
        },
    }


class FakeSource:
    """A fake Source that records calls and returns a pre-configured FetchResponse."""

    def __init__(
        self,
        name: str,
        response: FetchResponse,
        supported_data_types: frozenset[str] | None = None,
    ) -> None:
        self._name = name
        self._response = response
        self._supported_data_types = supported_data_types or frozenset({MATCH})
        self.fetch_calls: list[tuple[FetchContext, str]] = []

    @property
    def name(self) -> str:
        return self._name

    @property
    def supported_data_types(self) -> frozenset[str]:
        return self._supported_data_types

    @property
    def required_context_keys(self) -> frozenset[str]:
        return frozenset()

    async def fetch(self, context: FetchContext, data_type: str) -> FetchResponse:
        self.fetch_calls.append((context, data_type))
        return self._response

    async def close(self) -> None:
        pass


def _build_coordinator_with_fake(
    fake_source: FakeSource,
    raw_store: RawStore,
    blob_store: BlobStore | None = None,
) -> WaterfallCoordinator:
    """Build a WaterfallCoordinator backed by a single FakeSource + RiotExtractor."""
    entry = SourceEntry(
        name=fake_source.name,
        source=fake_source,
        priority=0,
        primary_for=frozenset({MATCH}),
    )
    registry = SourceRegistry([entry])
    extractor = RiotExtractor()
    return WaterfallCoordinator(registry, blob_store, raw_store, [extractor])


# ---------------------------------------------------------------------------
# IT-WF-01: Single Riot source success -> stream:parse published
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wf01__riot_success__publishes_to_stream_parse(
    r: aioredis.Redis,
    cfg: Config,
) -> None:
    """Riot source returns SUCCESS with valid blob -> stream:parse message + raw:match stored."""
    log = tlog("it-wf-01")
    raw_store = RawStore(r)

    blob = _valid_riot_blob()
    fake_riot = FakeSource(
        name="riot",
        response=FetchResponse(
            result=FetchResult.SUCCESS,
            raw_blob=json.dumps(blob).encode(),
            data=blob,
            available_data_types=frozenset({MATCH}),
        ),
    )
    coordinator = _build_coordinator_with_fake(fake_riot, raw_store)

    # Seed stream:match_id
    env = _make_envelope(_MATCH_A, cfg)
    await publish(r, "stream:match_id", env)

    # Consume and process
    riot = RiotClient("test-api-key", r=r)
    try:
        msgs = await consume_all(r, "stream:match_id", "fetchers", "wf01")
        assert len(msgs) == 1
        msg_id, envelope = msgs[0]
        await _fetch_match(
            r, riot, raw_store, cfg, msg_id, envelope, log, coordinator=coordinator
        )
    finally:
        await riot.close()

    # --- Assertions ---
    # 1. stream:parse has a message with match_id
    parse_msgs = await consume_all(r, "stream:parse", "parsers", "wf01")
    assert len(parse_msgs) == 1
    _, parse_env = parse_msgs[0]
    assert parse_env.payload["match_id"] == _MATCH_A

    # 2. raw:match:{match_id} exists in Redis
    assert await r.exists(f"raw:match:{_MATCH_A}") == 1

    # 3. match:{match_id} status is "fetched"
    assert await r.hget(f"match:{_MATCH_A}", "status") == "fetched"

    # 4. FakeSource.fetch() was called exactly once
    assert len(fake_riot.fetch_calls) == 1


# ---------------------------------------------------------------------------
# IT-WF-02: Riot throttled -> all sources exhausted -> DLQ
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wf02__riot_throttled__routes_to_dlq(
    r: aioredis.Redis,
    cfg: Config,
) -> None:
    """Riot source returns THROTTLED -> all_exhausted -> DLQ with retry_after_ms."""
    log = tlog("it-wf-02")
    raw_store = RawStore(r)

    fake_riot = FakeSource(
        name="riot",
        response=FetchResponse(
            result=FetchResult.THROTTLED,
            retry_after_ms=5000,
        ),
    )
    coordinator = _build_coordinator_with_fake(fake_riot, raw_store)

    # Seed stream:match_id
    env = _make_envelope(_MATCH_A, cfg)
    await publish(r, "stream:match_id", env)

    # Consume and process
    riot = RiotClient("test-api-key", r=r)
    try:
        msgs = await consume_all(r, "stream:match_id", "fetchers", "wf02")
        assert len(msgs) == 1
        msg_id, envelope = msgs[0]
        await _fetch_match(
            r, riot, raw_store, cfg, msg_id, envelope, log, coordinator=coordinator
        )
    finally:
        await riot.close()

    # --- Assertions ---
    # 1. stream:parse has NO new messages
    parse_msgs = await consume_all(r, "stream:parse", "parsers", "wf02")
    assert len(parse_msgs) == 0

    # 2. DLQ has a message
    dlq_entries = await r.xrange("stream:dlq")
    assert len(dlq_entries) >= 1

    # 3. DLQ envelope has retry_after_ms=5000 and correct failure_code
    dlq_fields = dlq_entries[-1][1]  # last entry
    dlq_env = DLQEnvelope.from_redis_fields(dlq_fields)
    assert dlq_env.retry_after_ms == 5000
    assert dlq_env.failure_code == "http_429"
    assert dlq_env.payload["match_id"] == _MATCH_A

    # 4. raw:match:{match_id} does NOT exist
    assert await r.exists(f"raw:match:{_MATCH_A}") == 0


# ---------------------------------------------------------------------------
# IT-WF-03: BlobStore cache hit -> stream:parse without HTTP call
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wf03__blob_cache_hit__publishes_without_http(
    r: aioredis.Redis,
    cfg: Config,
    tmp_path,
) -> None:
    """Pre-written blob on disk -> stream:parse published, source.fetch() never called."""
    log = tlog("it-wf-03")
    raw_store = RawStore(r)

    # Pre-write a blob to BlobStore disk: {source_name}/{platform}/{match_id}.json
    blob = _valid_riot_blob()
    blob["metadata"]["matchId"] = _MATCH_B
    blob_store = BlobStore(str(tmp_path))
    await blob_store.write("riot", _MATCH_B, json.dumps(blob).encode())

    # Verify the blob file was written
    blob_path = tmp_path / "riot" / "NA1" / f"{_MATCH_B}.json"
    assert blob_path.exists()

    # Create a FakeSource that should NOT be called
    fake_riot = FakeSource(
        name="riot",
        response=FetchResponse(
            result=FetchResult.SUCCESS,
            raw_blob=b"should-not-be-used",
        ),
    )
    coordinator = _build_coordinator_with_fake(
        fake_riot, raw_store, blob_store=blob_store
    )

    # Seed stream:match_id
    env = _make_envelope(_MATCH_B, cfg)
    await publish(r, "stream:match_id", env)

    # Consume and process
    riot = RiotClient("test-api-key", r=r)
    try:
        msgs = await consume_all(r, "stream:match_id", "fetchers", "wf03")
        assert len(msgs) == 1
        msg_id, envelope = msgs[0]
        await _fetch_match(
            r, riot, raw_store, cfg, msg_id, envelope, log, coordinator=coordinator
        )
    finally:
        await riot.close()

    # --- Assertions ---
    # 1. stream:parse has a message (served from blob cache)
    parse_msgs = await consume_all(r, "stream:parse", "parsers", "wf03")
    assert len(parse_msgs) == 1
    _, parse_env = parse_msgs[0]
    assert parse_env.payload["match_id"] == _MATCH_B

    # 2. RiotSource.fetch() was never called (blob cache served the request)
    assert len(fake_riot.fetch_calls) == 0

    # 3. raw:match:{match_id} was populated (coordinator writes to raw_store on cache hit)
    assert await r.exists(f"raw:match:{_MATCH_B}") == 1


# ---------------------------------------------------------------------------
# IT-WF-04: blob_validation_failed -> immediate DLQ (no retry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_wf04__blob_validation_failed__immediate_dlq(
    r: aioredis.Redis,
    cfg: Config,
) -> None:
    """Source returns SUCCESS with bad blob -> can_extract fails -> DLQ with max_attempts=1."""
    log = tlog("it-wf-04")
    raw_store = RawStore(r)

    # Return a blob that will fail RiotExtractor.can_extract() (missing "info" and "metadata")
    bad_blob = {"bad": "blob", "no_info": True}
    fake_riot = FakeSource(
        name="riot",
        response=FetchResponse(
            result=FetchResult.SUCCESS,
            raw_blob=json.dumps(bad_blob).encode(),
            data=bad_blob,
            available_data_types=frozenset({MATCH}),
        ),
    )
    coordinator = _build_coordinator_with_fake(fake_riot, raw_store)

    # Seed stream:match_id
    env = _make_envelope(_MATCH_A, cfg)
    await publish(r, "stream:match_id", env)

    # Consume and process
    riot = RiotClient("test-api-key", r=r)
    try:
        msgs = await consume_all(r, "stream:match_id", "fetchers", "wf04")
        assert len(msgs) == 1
        msg_id, envelope = msgs[0]
        await _fetch_match(
            r, riot, raw_store, cfg, msg_id, envelope, log, coordinator=coordinator
        )
    finally:
        await riot.close()

    # --- Assertions ---
    # 1. stream:parse has NO messages
    parse_msgs = await consume_all(r, "stream:parse", "parsers", "wf04")
    assert len(parse_msgs) == 0

    # 2. DLQ has a message
    dlq_entries = await r.xrange("stream:dlq")
    assert len(dlq_entries) >= 1

    # 3. DLQ envelope has max_attempts=1 (immediate archive, no retry cycles)
    dlq_fields = dlq_entries[-1][1]
    dlq_env = DLQEnvelope.from_redis_fields(dlq_fields)
    assert dlq_env.max_attempts == 1
    assert dlq_env.failure_code == "blob_validation_failed"
    assert dlq_env.payload["match_id"] == _MATCH_A

    # 4. raw:match:{match_id} does NOT exist (bad blob was not persisted)
    assert await r.exists(f"raw:match:{_MATCH_A}") == 0

    # 5. The inbound message was ACKed (not left in PEL)
    pending_info = await r.xpending("stream:match_id", "fetchers")
    assert pending_info["pending"] == 0
