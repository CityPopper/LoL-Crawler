"""Integration test fixtures — testcontainers Redis + rate-limiter service."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from typing import Any, AsyncGenerator

import pytest
import redis.asyncio as aioredis
from testcontainers.core.container import DockerContainer
from testcontainers.core.waiting_utils import wait_for_logs
from testcontainers.redis import RedisContainer

# Ensure helpers.py is importable
sys.path.insert(0, str(Path(__file__).parent))

from helpers import FIXTURES  # noqa: E402, F401

_RATE_LIMITER_IMAGE = "lol-crawler-rate-limiter:latest"
_RATE_LIMITER_PORT = 8079


@pytest.fixture(scope="session")
def redis_container():
    """Start a Redis 7 container for the entire test session."""
    with RedisContainer("redis:7.2.11-alpine") as container:
        yield container


@pytest.fixture(scope="session")
def rate_limiter_container(redis_container):
    """Start the rate-limiter HTTP service pointing at the session Redis.

    Session-scoped for pytest-xdist compatibility (one container per worker).
    Skips the test if the Docker image has not been built.
    """
    import docker

    try:
        client = docker.from_env()
        client.images.get(_RATE_LIMITER_IMAGE)
    except Exception:
        pytest.skip(f"Docker image {_RATE_LIMITER_IMAGE!r} not found — build it first")

    redis_host = redis_container.get_container_host_ip()
    redis_port = redis_container.get_exposed_port(6379)
    redis_url = f"redis://{redis_host}:{redis_port}/0"

    # The rate-limiter container runs on the host network so it can reach
    # the Redis container's mapped port.
    container = (
        DockerContainer(_RATE_LIMITER_IMAGE)
        .with_exposed_ports(_RATE_LIMITER_PORT)
        .with_env("RATE_LIMITER_REDIS_URL", redis_url)
        .with_env("RATE_LIMITER_HOST", "0.0.0.0")
        .with_env("RATE_LIMITER_PORT", str(_RATE_LIMITER_PORT))
        .with_env("RATELIMIT_RIOT_SHORT_LIMIT", "20")
        .with_env("RATELIMIT_RIOT_LONG_LIMIT", "1000")
        .with_env(
            "RATELIMIT_KNOWN_SOURCES",
            "riot,fetcher,crawler,discovery,opgg",
        )
    )
    container.start()
    try:
        wait_for_logs(container, "Uvicorn running", timeout=15)
    except Exception:
        # Fallback: give the container a moment to start
        time.sleep(3)

    host = container.get_container_host_ip()
    port = container.get_exposed_port(_RATE_LIMITER_PORT)
    url = f"http://{host}:{port}"

    # Expose the URL so the rate_limiter_client picks it up.
    # Also patch the module-level cached URL and reset the shared client,
    # because the module reads os.environ at import time.
    os.environ["RATE_LIMITER_URL"] = url
    import lol_pipeline.rate_limiter_client as _rlc

    _rlc._RATE_LIMITER_URL = url
    _rlc._client = None

    yield url

    container.stop()


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
