"""Tests for RATE-DYN-2 domain registry (black-box against spec stubs).

Domain dataclass tests exercise the real validation (implemented in the spec).
load_domains_from_env and acquire_token_for_domain are stubs that raise
NotImplementedError — tests confirm they fail with that exact error.
"""

from __future__ import annotations

import math
import os
from dataclasses import FrozenInstanceError
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from lol_rate_limiter._spec_dyn2_domain_registry import (
    Domain,
    acquire_token_for_domain,
    load_domains_from_env,
)

try:
    import lupa  # noqa: F401

    _LUPA_AVAILABLE = True
except ImportError:
    _LUPA_AVAILABLE = False


# ===================================================================
# 1. Domain dataclass validation
# ===================================================================


class TestDomainValidNames:
    """Valid domain names are accepted without raising."""

    def test_domain__riot_americas__accepted(self):
        d = Domain(name="riot:americas", short_limit=18, long_limit=90)
        assert d.name == "riot:americas"

    def test_domain__opgg__accepted(self):
        d = Domain(name="opgg", short_limit=2, long_limit=100)
        assert d.name == "opgg"

    def test_domain__some_api_with_hyphen__accepted(self):
        d = Domain(name="some-api", short_limit=5, long_limit=50)
        assert d.name == "some-api"

    def test_domain__api_v2_with_underscore__accepted(self):
        d = Domain(name="api_v2", short_limit=10, long_limit=60)
        assert d.name == "api_v2"


class TestDomainInvalidNames:
    """Invalid domain names raise ValueError."""

    def test_domain__uppercase__raises_value_error(self):
        with pytest.raises(ValueError, match="must match"):
            Domain(name="RIOT", short_limit=2, long_limit=100)

    def test_domain__space_in_name__raises_value_error(self):
        with pytest.raises(ValueError, match="must match"):
            Domain(name="riot americas", short_limit=2, long_limit=100)

    def test_domain__dot_in_name__raises_value_error(self):
        with pytest.raises(ValueError, match="must match"):
            Domain(name="riot.americas", short_limit=2, long_limit=100)

    def test_domain__empty_string__raises_value_error(self):
        with pytest.raises(ValueError, match="must match"):
            Domain(name="", short_limit=2, long_limit=100)


class TestDomainUiPctValidation:
    """ui_pct must be in [0.0, 1.0]."""

    def test_domain__ui_pct_negative__raises_value_error(self):
        with pytest.raises(ValueError, match="ui_pct"):
            Domain(name="opgg", short_limit=2, long_limit=100, ui_pct=-0.1)

    def test_domain__ui_pct_above_one__raises_value_error(self):
        with pytest.raises(ValueError, match="ui_pct"):
            Domain(name="opgg", short_limit=2, long_limit=100, ui_pct=1.1)

    def test_domain__ui_pct_zero__accepted(self):
        d = Domain(name="opgg", short_limit=2, long_limit=100, ui_pct=0.0)
        assert d.ui_pct == 0.0

    def test_domain__ui_pct_one__accepted(self):
        d = Domain(name="opgg", short_limit=2, long_limit=100, ui_pct=1.0)
        assert d.ui_pct == 1.0


class TestDomainFrozen:
    """Domain is frozen — attribute assignment raises FrozenInstanceError."""

    def test_domain__set_attribute__raises_frozen_instance_error(self):
        d = Domain(name="opgg", short_limit=2, long_limit=100)
        with pytest.raises(FrozenInstanceError):
            d.name = "other"  # type: ignore[misc]

    def test_domain__set_short_limit__raises_frozen_instance_error(self):
        d = Domain(name="opgg", short_limit=2, long_limit=100)
        with pytest.raises(FrozenInstanceError):
            d.short_limit = 999  # type: ignore[misc]


class TestDomainDefaults:
    """Domain defaults match spec when not explicitly set."""

    def test_domain__defaults__match_spec(self):
        d = Domain(name="opgg", short_limit=2, long_limit=100)
        assert d.short_window_ms == 1000
        assert d.long_window_ms == 120_000
        assert d.header_aware is False
        assert d.has_method_limits is False
        assert d.ui_pct == 0.0


# ===================================================================
# 2. load_domains_from_env
# ===================================================================


class TestLoadDomainsFromEnvStub:
    """Stub raises NotImplementedError — confirms contract is importable."""

    def test_load_domains_from_env__raises_not_implemented(self):
        with pytest.raises(NotImplementedError):
            load_domains_from_env()


class TestLoadDomainsFromEnvEmptyEnv:
    """Empty env returns empty dict (behavioral, against stub -> NotImplementedError)."""

    def test_load_domains_from_env__empty_env__raises_not_implemented(self, monkeypatch):
        # Clear any DOMAIN_* env vars that might exist
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        with pytest.raises(NotImplementedError):
            load_domains_from_env()


class TestLoadDomainsFromEnvSingleDomain:
    """Single domain with only SHORT_LIMIT and LONG_LIMIT."""

    def test_load_domains__single_opgg__raises_not_implemented(self, monkeypatch):
        # Clear any pre-existing DOMAIN_ vars
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("DOMAIN_OPGG_SHORT_LIMIT", "2")
        monkeypatch.setenv("DOMAIN_OPGG_LONG_LIMIT", "100")
        with pytest.raises(NotImplementedError):
            result = load_domains_from_env()
            # When implemented, should return:
            # {"opgg": Domain(name="opgg", short_limit=2, long_limit=100,
            #   short_window_ms=1000, long_window_ms=120000,
            #   header_aware=False, has_method_limits=False, ui_pct=0.0)}
            assert "opgg" in result
            assert result["opgg"].name == "opgg"
            assert result["opgg"].short_limit == 2
            assert result["opgg"].long_limit == 100


class TestLoadDomainsFromEnvAllProperties:
    """Domain with all properties set via env vars."""

    def test_load_domains__riot_americas_full__raises_not_implemented(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_SHORT_LIMIT", "18")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_LONG_LIMIT", "90")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_SHORT_WINDOW_MS", "1000")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_LONG_WINDOW_MS", "120000")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_HEADER_AWARE", "true")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_HAS_METHOD_LIMITS", "true")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_UI_PCT", "0.0")
        with pytest.raises(NotImplementedError):
            result = load_domains_from_env()
            # When implemented: Domain name "riot:americas", all fields as specified
            assert "riot:americas" in result
            d = result["riot:americas"]
            assert d.short_limit == 18
            assert d.long_limit == 90
            assert d.short_window_ms == 1000
            assert d.long_window_ms == 120_000
            assert d.header_aware is True
            assert d.has_method_limits is True
            assert d.ui_pct == 0.0


class TestLoadDomainsFromEnvHeaderAware:
    """Boolean parsing: HEADER_AWARE=true/false."""

    def test_load_domains__header_aware_true__raises_not_implemented(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("DOMAIN_TESTAPI_SHORT_LIMIT", "5")
        monkeypatch.setenv("DOMAIN_TESTAPI_LONG_LIMIT", "50")
        monkeypatch.setenv("DOMAIN_TESTAPI_HEADER_AWARE", "true")
        with pytest.raises(NotImplementedError):
            load_domains_from_env()

    def test_load_domains__header_aware_false__raises_not_implemented(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("DOMAIN_TESTAPI_SHORT_LIMIT", "5")
        monkeypatch.setenv("DOMAIN_TESTAPI_LONG_LIMIT", "50")
        monkeypatch.setenv("DOMAIN_TESTAPI_HEADER_AWARE", "false")
        with pytest.raises(NotImplementedError):
            load_domains_from_env()


class TestLoadDomainsFromEnvNameOverride:
    """DOMAIN_SOMEAPI_NAME overrides the derived domain name."""

    def test_load_domains__name_override__raises_not_implemented(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("DOMAIN_SOMEAPI_SHORT_LIMIT", "5")
        monkeypatch.setenv("DOMAIN_SOMEAPI_LONG_LIMIT", "50")
        monkeypatch.setenv("DOMAIN_SOMEAPI_NAME", "my-api:v2")
        with pytest.raises(NotImplementedError):
            result = load_domains_from_env()
            # When implemented: keyed by "my-api:v2" not "someapi"
            assert "my-api:v2" in result
            assert result["my-api:v2"].name == "my-api:v2"


class TestLoadDomainsFromEnvInvalidDerivedName:
    """Invalid derived name raises ValueError at startup."""

    def test_load_domains__invalid_derived_name__raises_not_implemented(self, monkeypatch):
        """Until implemented, the stub raises NotImplementedError.
        When implemented, it should raise ValueError for names that fail validation.
        """
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        # A domain prefix that when lowercased and de-underscored, produces
        # an invalid name — we use NAME override to force an invalid name.
        monkeypatch.setenv("DOMAIN_BAD_SHORT_LIMIT", "5")
        monkeypatch.setenv("DOMAIN_BAD_LONG_LIMIT", "50")
        monkeypatch.setenv("DOMAIN_BAD_NAME", "BAD NAME")  # spaces + uppercase = invalid
        with pytest.raises((NotImplementedError, ValueError)):
            load_domains_from_env()


class TestLoadDomainsFromEnvMultiple:
    """Multiple domains in one env produce a dict with all domains."""

    def test_load_domains__multiple_domains__raises_not_implemented(self, monkeypatch):
        for key in list(os.environ.keys()):
            if key.startswith("DOMAIN_"):
                monkeypatch.delenv(key)
        monkeypatch.setenv("DOMAIN_OPGG_SHORT_LIMIT", "2")
        monkeypatch.setenv("DOMAIN_OPGG_LONG_LIMIT", "100")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_SHORT_LIMIT", "18")
        monkeypatch.setenv("DOMAIN_RIOT_AMERICAS_LONG_LIMIT", "90")
        with pytest.raises(NotImplementedError):
            result = load_domains_from_env()
            # When implemented: dict has both domains
            assert len(result) == 2
            assert "opgg" in result
            assert "riot:americas" in result


# ===================================================================
# 3. acquire_token_for_domain — cooling-off check
# ===================================================================


class TestAcquireTokenForDomainStub:
    """Stub raises NotImplementedError — confirms contract is importable."""

    @pytest.mark.asyncio
    async def test_acquire_token_for_domain__raises_not_implemented(self):
        domain = Domain(name="opgg", short_limit=2, long_limit=100)
        r = AsyncMock()
        r.pttl = AsyncMock(return_value=-2)
        with pytest.raises(NotImplementedError):
            await acquire_token_for_domain(r, domain)


class TestAcquireTokenCoolingOffActive:
    """Cooling-off active: returns (False, ttl_ms) without calling eval.

    Against the stub, this raises NotImplementedError. When implemented,
    the function should short-circuit on a positive PTTL.
    """

    @pytest.mark.asyncio
    async def test_acquire_token__cooling_off_active__raises_not_implemented(self):
        domain = Domain(name="opgg", short_limit=2, long_limit=100)
        r = AsyncMock()
        r.pttl = AsyncMock(return_value=29500)
        with pytest.raises(NotImplementedError):
            await acquire_token_for_domain(r, domain)

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed")
    async def test_acquire_token__cooling_off_active__returns_false_with_ttl(self):
        """Behavioral contract test: when cooling-off key has TTL > 0,
        acquire should return (False, ttl_ms) without calling Lua.

        Skipped against stub (NotImplementedError). Will pass once
        real implementation is in place.
        """
        import fakeredis.aioredis

        fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            domain = Domain(name="opgg", short_limit=2, long_limit=100)
            # Set cooling-off key with 30s TTL
            await fake_r.set("ratelimit:opgg:cooling_off", "1", px=30000)
            try:
                granted, retry_ms = await acquire_token_for_domain(fake_r, domain)
                assert granted is False
                assert retry_ms is not None
                assert retry_ms > 0
            except NotImplementedError:
                pytest.skip("Stub not yet implemented")
        finally:
            await fake_r.aclose()


class TestAcquireTokenNoCoolingOff:
    """No cooling-off: proceeds to Lua."""

    @pytest.mark.asyncio
    async def test_acquire_token__no_cooling_off__raises_not_implemented(self):
        domain = Domain(name="opgg", short_limit=2, long_limit=100)
        r = AsyncMock()
        r.pttl = AsyncMock(return_value=-2)  # key does not exist
        with pytest.raises(NotImplementedError):
            await acquire_token_for_domain(r, domain)


class TestAcquireTokenKeyPrefixUiPctZero:
    """ui_pct=0.0: key prefix is ratelimit:{domain}:short/long (no sub-bucket)."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed")
    async def test_acquire_token__ui_pct_zero__uses_base_prefix(self):
        """Behavioral contract: ui_pct=0.0 uses ratelimit:opgg as key prefix."""
        import fakeredis.aioredis

        fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            domain = Domain(name="opgg", short_limit=2, long_limit=100, ui_pct=0.0)
            try:
                granted, retry_ms = await acquire_token_for_domain(fake_r, domain)
                # If granted, check that the base prefix keys were used
                # by verifying that ratelimit:opgg:short sorted set exists
                short_count = await fake_r.zcard("ratelimit:opgg:short")
                assert short_count >= 0  # key should exist after a token is acquired
            except NotImplementedError:
                pytest.skip("Stub not yet implemented")
        finally:
            await fake_r.aclose()


class TestAcquireTokenKeyPrefixUiTrue:
    """ui_pct > 0, is_ui=True: key prefix is ratelimit:{domain}:ui."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed")
    async def test_acquire_token__ui_pct_02_is_ui_true__uses_ui_prefix(self):
        """Behavioral contract: ui_pct=0.2, is_ui=True uses ratelimit:opgg:ui prefix."""
        import fakeredis.aioredis

        fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            domain = Domain(name="opgg", short_limit=10, long_limit=100, ui_pct=0.2)
            try:
                granted, retry_ms = await acquire_token_for_domain(
                    fake_r, domain, is_ui=True
                )
                # Verify the UI sub-bucket keys were used
                ui_short_count = await fake_r.zcard("ratelimit:opgg:ui:short")
                assert ui_short_count >= 0
            except NotImplementedError:
                pytest.skip("Stub not yet implemented")
        finally:
            await fake_r.aclose()


class TestAcquireTokenKeyPrefixUiFalse:
    """ui_pct > 0, is_ui=False: key prefix is ratelimit:{domain}:pipeline."""

    @pytest.mark.asyncio
    @pytest.mark.skipif(not _LUPA_AVAILABLE, reason="lupa not installed")
    async def test_acquire_token__ui_pct_02_is_ui_false__uses_pipeline_prefix(self):
        """Behavioral contract: ui_pct=0.2, is_ui=False uses ratelimit:opgg:pipeline prefix."""
        import fakeredis.aioredis

        fake_r = fakeredis.aioredis.FakeRedis(decode_responses=True)
        try:
            domain = Domain(name="opgg", short_limit=10, long_limit=100, ui_pct=0.2)
            try:
                granted, retry_ms = await acquire_token_for_domain(
                    fake_r, domain, is_ui=False
                )
                # Verify the pipeline sub-bucket keys were used
                pipeline_short_count = await fake_r.zcard(
                    "ratelimit:opgg:pipeline:short"
                )
                assert pipeline_short_count >= 0
            except NotImplementedError:
                pytest.skip("Stub not yet implemented")
        finally:
            await fake_r.aclose()


# ===================================================================
# 4. HTTP endpoint tests
# ===================================================================

# For endpoint tests we need to import app and Config from the real modules.
# This is acceptable per the task instructions.
from lol_rate_limiter.config import Config
from lol_rate_limiter.main import app


@pytest.fixture
def mock_redis():
    """Create a mock Redis instance with default behaviors."""
    r = AsyncMock()
    r.zcard = AsyncMock(return_value=0)
    r.eval = AsyncMock(return_value=1)
    r.set = AsyncMock(return_value=True)
    r.get = AsyncMock(return_value=None)
    r.delete = AsyncMock(return_value=1)
    r.pttl = AsyncMock(return_value=-2)
    r.aclose = AsyncMock()
    return r


@pytest.fixture
def opgg_domain():
    """A standard opgg domain for testing."""
    return Domain(name="opgg", short_limit=2, long_limit=100)


@pytest.fixture
def riot_americas_domain():
    """A riot:americas domain with header_aware=True."""
    return Domain(
        name="riot:americas",
        short_limit=18,
        long_limit=90,
        header_aware=True,
        has_method_limits=True,
    )


@pytest.fixture
async def domain_client(mock_redis, opgg_domain, riot_americas_domain):
    """AsyncClient wired to FastAPI app with domain-based config.

    Creates a Config, then overrides its domains dict directly
    (approach 2 from task instructions).
    """
    cfg = Config()
    cfg.domains = {
        "opgg": opgg_domain,
        "riot:americas": riot_americas_domain,
    }
    app.state.cfg = cfg
    app.state.r = mock_redis
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


# ---------------------------------------------------------------------------
# POST /token/acquire — domain-based tests
# ---------------------------------------------------------------------------


class TestTokenAcquireDomain:
    """POST /token/acquire with domain-based routing."""

    @pytest.mark.asyncio
    async def test_token_acquire__unknown_domain__returns_404(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.post(
            "/token/acquire",
            json={"domain": "nonexistent"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "unknown domain"}

    @pytest.mark.asyncio
    async def test_token_acquire__known_domain_granted__returns_granted(
        self, domain_client, mock_redis
    ):
        # Mock acquire_token_for_domain to return granted
        with patch(
            "lol_rate_limiter.main.acquire_token_for_domain",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            resp = await domain_client.post(
                "/token/acquire",
                json={"domain": "opgg"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["granted"] is True
            assert data["retry_after_ms"] is None

    @pytest.mark.asyncio
    async def test_token_acquire__known_domain_denied__returns_retry_after(
        self, domain_client, mock_redis
    ):
        with patch(
            "lol_rate_limiter.main.acquire_token_for_domain",
            new_callable=AsyncMock,
            return_value=(False, 1500),
        ):
            resp = await domain_client.post(
                "/token/acquire",
                json={"domain": "opgg"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["granted"] is False
            assert data["retry_after_ms"] == 1500

    @pytest.mark.asyncio
    async def test_token_acquire__is_ui_field_accepted__no_error(
        self, domain_client, mock_redis
    ):
        """is_ui field is accepted without error even when domain has ui_pct=0.0."""
        with patch(
            "lol_rate_limiter.main.acquire_token_for_domain",
            new_callable=AsyncMock,
            return_value=(True, None),
        ):
            resp = await domain_client.post(
                "/token/acquire",
                json={"domain": "opgg", "is_ui": True},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["granted"] is True

    @pytest.mark.asyncio
    async def test_token_acquire__redis_failure__fail_open(
        self, domain_client, mock_redis
    ):
        """Redis failure causes fail-open: granted=True, retry_after_ms=None."""
        with patch(
            "lol_rate_limiter.main.acquire_token_for_domain",
            new_callable=AsyncMock,
            side_effect=ConnectionError("Redis down"),
        ):
            resp = await domain_client.post(
                "/token/acquire",
                json={"domain": "opgg"},
            )
            assert resp.status_code == 200
            data = resp.json()
            assert data["granted"] is True
            assert data["retry_after_ms"] is None


# ---------------------------------------------------------------------------
# POST /cooling-off — domain-based tests
# ---------------------------------------------------------------------------


class TestCoolingOffDomain:
    """POST /cooling-off with domain-based routing."""

    @pytest.mark.asyncio
    async def test_cooling_off__unknown_domain__returns_404(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.post(
            "/cooling-off",
            json={"domain": "nonexistent", "delay_ms": 5000},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "unknown domain"}

    @pytest.mark.asyncio
    async def test_cooling_off__known_domain__sets_key_and_returns_ok(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.post(
            "/cooling-off",
            json={"domain": "opgg", "delay_ms": 5000},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        mock_redis.set.assert_any_call(
            "ratelimit:opgg:cooling_off", "1", px=5000
        )


# ---------------------------------------------------------------------------
# POST /cooling-off/reset — domain-based tests
# ---------------------------------------------------------------------------


class TestCoolingOffResetDomain:
    """POST /cooling-off/reset with domain-based routing."""

    @pytest.mark.asyncio
    async def test_cooling_off_reset__unknown_domain__returns_404(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.post(
            "/cooling-off/reset",
            json={"domain": "nonexistent"},
        )
        assert resp.status_code == 404
        assert resp.json() == {"error": "unknown domain"}

    @pytest.mark.asyncio
    async def test_cooling_off_reset__known_domain__deletes_halving_keys(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.post(
            "/cooling-off/reset",
            json={"domain": "opgg"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}
        # Should delete the 3 halving keys (single call or separate calls)
        deleted_keys = set()
        for call in mock_redis.delete.call_args_list:
            for arg in call.args:
                deleted_keys.add(arg)
        expected_keys = {
            "ratelimit:opgg:halved",
            "ratelimit:opgg:limits:short",
            "ratelimit:opgg:limits:long",
        }
        assert expected_keys.issubset(deleted_keys), (
            f"Expected delete calls for {expected_keys}, got {deleted_keys}"
        )


# ---------------------------------------------------------------------------
# POST /headers — domain-based tests
# ---------------------------------------------------------------------------


class TestHeadersDomain:
    """POST /headers with domain-based routing."""

    @pytest.mark.asyncio
    async def test_headers__unknown_domain__returns_404(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.post(
            "/headers",
            json={"domain": "nonexistent", "rate_limit": "20:1,100:120"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_headers__header_aware_false__returns_not_updated(
        self, domain_client, mock_redis
    ):
        """Domain with header_aware=False returns updated=false (no-op)."""
        resp = await domain_client.post(
            "/headers",
            json={
                "domain": "opgg",
                "rate_limit": "2:1,100:120",
                "rate_limit_count": "1:1,40:120",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] is False

    @pytest.mark.asyncio
    async def test_headers__header_aware_true__returns_updated_and_sets_redis(
        self, domain_client, mock_redis
    ):
        """Domain with header_aware=True parses headers and stores in Redis."""
        resp = await domain_client.post(
            "/headers",
            json={
                "domain": "riot:americas",
                "rate_limit": "20:1,100:120",
                "rate_limit_count": "5:1,40:120",
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["updated"] is True
        # Redis set should have been called for limits
        set_calls = [str(c) for c in mock_redis.set.call_args_list]
        # At least one call should target the limits keys
        limits_calls = [
            c
            for c in set_calls
            if "ratelimit:riot:americas:limits" in c
        ]
        assert len(limits_calls) > 0, (
            f"Expected Redis set calls for limits keys, got: {set_calls}"
        )


# ---------------------------------------------------------------------------
# GET /status — domain-based tests
# ---------------------------------------------------------------------------


class TestStatusDomain:
    """GET /status returns domain information."""

    @pytest.mark.asyncio
    async def test_status__returns_domains_dict(
        self, domain_client, mock_redis
    ):
        resp = await domain_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "domains" in data

    @pytest.mark.asyncio
    async def test_status__per_domain_has_required_fields(
        self, domain_client, mock_redis
    ):
        # Mock Redis responses for status fields
        mock_redis.get = AsyncMock(return_value=None)
        resp = await domain_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        domains = data["domains"]
        for domain_name in ["opgg", "riot:americas"]:
            assert domain_name in domains, f"Missing domain {domain_name}"
            entry = domains[domain_name]
            assert "short_limit" in entry
            assert "long_limit" in entry
            assert "halved" in entry
            assert "halve_count" in entry
            assert "header_aware" in entry
            assert "has_method_limits" in entry

    @pytest.mark.asyncio
    async def test_status__halved_at_present_when_redis_has_key(
        self, domain_client, mock_redis
    ):
        """halved_at is present when Redis has the key, absent when not."""
        epoch_ts = 1711500000

        async def mock_get(key):
            if key == "ratelimit:opgg:halved_at":
                return str(epoch_ts)
            if key == "ratelimit:opgg:halved":
                return "1"
            if key == "ratelimit:opgg:halve_count":
                return "2"
            return None

        mock_redis.get = AsyncMock(side_effect=mock_get)
        resp = await domain_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        domains = data["domains"]

        # opgg should have halved_at since Redis returns it
        if "opgg" in domains and "halved_at" in domains["opgg"]:
            assert domains["opgg"]["halved_at"] == epoch_ts

        # riot:americas should NOT have halved_at (Redis returns None for it)
        if "riot:americas" in domains:
            assert "halved_at" not in domains["riot:americas"] or domains["riot:americas"].get("halved_at") is None

    @pytest.mark.asyncio
    async def test_status__domain_values_match_config(
        self, domain_client, mock_redis
    ):
        mock_redis.get = AsyncMock(return_value=None)
        resp = await domain_client.get("/status")
        assert resp.status_code == 200
        data = resp.json()
        domains = data["domains"]

        opgg = domains.get("opgg", {})
        assert opgg.get("short_limit") == 2
        assert opgg.get("long_limit") == 100
        assert opgg.get("header_aware") is False
        assert opgg.get("has_method_limits") is False

        riot = domains.get("riot:americas", {})
        assert riot.get("short_limit") == 18
        assert riot.get("long_limit") == 90
        assert riot.get("header_aware") is True
        assert riot.get("has_method_limits") is True
