"""Integration test fixtures — testcontainers Redis."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import redis.asyncio as aioredis
from testcontainers.redis import RedisContainer

# Ensure helpers.py is importable
sys.path.insert(0, str(Path(__file__).parent))

from helpers import FIXTURES  # noqa: E402, F401


@pytest.fixture(scope="session")
def redis_container():
    """Start a Redis 7 container for the entire test session."""
    with RedisContainer("redis:7-alpine") as container:
        yield container


@pytest.fixture
async def r(redis_container) -> AsyncGenerator[aioredis.Redis, None]:
    """Per-test async Redis client; FLUSHALL after each test for isolation."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    client = aioredis.from_url(f"redis://{host}:{port}/0", decode_responses=True)
    yield client
    await client.flushall()
    await client.aclose()


@pytest.fixture
def cfg(redis_container):
    """Config pointed at the test container with safe defaults."""
    host = redis_container.get_container_host_ip()
    port = redis_container.get_exposed_port(6379)
    os.environ["RIOT_API_KEY"] = "test-api-key"
    os.environ["REDIS_URL"] = f"redis://{host}:{port}/0"
    os.environ.pop("MATCH_DATA_DIR", None)
    from lol_pipeline.config import Config

    return Config()


@pytest.fixture
def match_normal() -> dict[str, Any]:
    return json.loads((FIXTURES / "match_normal.json").read_text())


@pytest.fixture
def account_data() -> dict[str, Any]:
    return json.loads((FIXTURES / "account.json").read_text())
