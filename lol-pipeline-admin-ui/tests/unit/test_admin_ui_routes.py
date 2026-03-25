"""STRUCT-3: Tests for lol-pipeline-admin-ui routes.

Red-step tests for the new admin-ui FastAPI service that will own all
Redis write operations extracted from lol-pipeline-ui:
  - DLQ listing, replay, and clear
  - system halt / resume
  - health check
  - authentication via ADMIN_UI_SECRET header
"""

from __future__ import annotations

import json

import pytest
from httpx import ASGITransport, AsyncClient

# --------------------------------------------------------------------------
# Fixtures
# --------------------------------------------------------------------------

ADMIN_SECRET = "test-admin-secret-42"


@pytest.fixture
def _set_admin_secret(monkeypatch):
    """Set the ADMIN_UI_SECRET env var for the test session."""
    monkeypatch.setenv("ADMIN_UI_SECRET", ADMIN_SECRET)


@pytest.fixture
async def client(_set_admin_secret, r):
    """Create an httpx AsyncClient bound to the admin-ui FastAPI app.

    The app is imported inside the fixture so the env var is set first.
    We also inject the fakeredis instance into app.state.r.
    """
    from lol_admin_ui.main import app

    app.state.r = r

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


def _auth_headers() -> dict[str, str]:
    """Return the authorization header expected by admin-ui."""
    return {"X-Admin-Secret": ADMIN_SECRET}


# --------------------------------------------------------------------------
# GET /health
# --------------------------------------------------------------------------


class TestHealthRoute:
    """GET /health returns 200 with basic health info."""

    @pytest.mark.asyncio
    async def test_health__returns_200(self, client):
        resp = await client.get("/health", headers=_auth_headers())
        assert resp.status_code == 200

    @pytest.mark.asyncio
    async def test_health__contains_status_ok(self, client):
        resp = await client.get("/health", headers=_auth_headers())
        data = resp.json()
        assert data["status"] == "ok"


# --------------------------------------------------------------------------
# Authentication
# --------------------------------------------------------------------------


class TestAuthentication:
    """All admin-ui routes require a valid ADMIN_UI_SECRET header."""

    @pytest.mark.asyncio
    async def test_missing_secret__returns_401(self, client):
        """Request without X-Admin-Secret header gets 401."""
        resp = await client.get("/dlq")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_wrong_secret__returns_401(self, client):
        """Request with incorrect secret gets 401."""
        resp = await client.get("/dlq", headers={"X-Admin-Secret": "wrong"})
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_correct_secret__passes_auth(self, client):
        """Request with correct secret does not get 401."""
        resp = await client.get("/dlq", headers=_auth_headers())
        assert resp.status_code != 401

    @pytest.mark.asyncio
    async def test_halt_without_secret__returns_401(self, client):
        """POST /system/halt without secret gets 401."""
        resp = await client.post("/system/halt")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_resume_without_secret__returns_401(self, client):
        """POST /system/resume without secret gets 401."""
        resp = await client.post("/system/resume")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_dlq_replay_without_secret__returns_401(self, client):
        """POST /dlq/replay/{id} without secret gets 401."""
        resp = await client.post("/dlq/replay/12345-0")
        assert resp.status_code == 401

    @pytest.mark.asyncio
    async def test_dlq_clear_without_secret__returns_401(self, client):
        """POST /dlq/clear without secret gets 401."""
        resp = await client.post("/dlq/clear")
        assert resp.status_code == 401


# --------------------------------------------------------------------------
# GET /dlq
# --------------------------------------------------------------------------


class TestDlqList:
    """GET /dlq lists DLQ entries from stream:dlq."""

    @pytest.mark.asyncio
    async def test_dlq__empty__returns_empty_list(self, client):
        """When stream:dlq is empty, return an empty entries list."""
        resp = await client.get("/dlq", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert data["entries"] == []

    @pytest.mark.asyncio
    async def test_dlq__with_entries__returns_entries(self, client, r):
        """When stream:dlq has entries, return them in the response."""
        await r.xadd(
            "stream:dlq",
            {
                "type": "match_id",
                "payload": json.dumps({"match_id": "NA1_100"}),
                "failure_code": "http_429",
                "failure_reason": "rate limited",
                "failed_by": "fetcher",
                "original_stream": "stream:match_id",
                "attempts": "2",
                "max_attempts": "3",
            },
        )
        resp = await client.get("/dlq", headers=_auth_headers())
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["entries"]) == 1
        entry = data["entries"][0]
        assert entry["failure_code"] == "http_429"

    @pytest.mark.asyncio
    async def test_dlq__returns_total_count(self, client, r):
        """Response includes total count of DLQ entries."""
        for i in range(3):
            await r.xadd(
                "stream:dlq",
                {
                    "type": "match_id",
                    "payload": json.dumps({"match_id": f"NA1_{i}"}),
                    "failure_code": "http_5xx",
                    "failure_reason": "server error",
                    "failed_by": "fetcher",
                    "original_stream": "stream:match_id",
                    "attempts": "1",
                    "max_attempts": "3",
                },
            )
        resp = await client.get("/dlq", headers=_auth_headers())
        data = resp.json()
        assert data["total"] == 3


# --------------------------------------------------------------------------
# POST /dlq/replay/{message_id}
# --------------------------------------------------------------------------


class TestDlqReplay:
    """POST /dlq/replay/{message_id} replays a DLQ entry."""

    @pytest.mark.asyncio
    async def test_dlq_replay__valid_entry__removes_from_dlq(self, client, r):
        """Replaying a valid DLQ entry removes it from stream:dlq."""
        entry_id = await r.xadd(
            "stream:dlq",
            {
                "type": "match_id",
                "payload": json.dumps({"match_id": "NA1_200", "puuid": "abc", "region": "na1"}),
                "failure_code": "http_429",
                "failure_reason": "rate limited",
                "failed_by": "fetcher",
                "original_stream": "stream:match_id",
                "attempts": "1",
                "max_attempts": "3",
            },
        )
        resp = await client.post(f"/dlq/replay/{entry_id}", headers=_auth_headers())
        assert resp.status_code == 200
        # Entry should be removed from DLQ
        remaining = await r.xlen("stream:dlq")
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_dlq_replay__valid_entry__publishes_to_original_stream(self, client, r):
        """Replayed message appears on the original stream."""
        entry_id = await r.xadd(
            "stream:dlq",
            {
                "type": "match_id",
                "payload": json.dumps({"match_id": "NA1_201", "puuid": "xyz", "region": "na1"}),
                "failure_code": "http_5xx",
                "failure_reason": "server error",
                "failed_by": "fetcher",
                "original_stream": "stream:match_id",
                "attempts": "1",
                "max_attempts": "3",
            },
        )
        resp = await client.post(f"/dlq/replay/{entry_id}", headers=_auth_headers())
        assert resp.status_code == 200
        # Message should now be on the original stream
        entries = await r.xrange("stream:match_id")
        assert len(entries) >= 1

    @pytest.mark.asyncio
    async def test_dlq_replay__nonexistent_entry__returns_404(self, client):
        """Replaying a non-existent DLQ entry returns 404."""
        resp = await client.post("/dlq/replay/9999999-0", headers=_auth_headers())
        assert resp.status_code == 404


# --------------------------------------------------------------------------
# POST /dlq/clear
# --------------------------------------------------------------------------


class TestDlqClear:
    """POST /dlq/clear removes all DLQ entries."""

    @pytest.mark.asyncio
    async def test_dlq_clear__removes_all_entries(self, client, r):
        """After clearing, stream:dlq has zero entries."""
        for i in range(5):
            await r.xadd(
                "stream:dlq",
                {
                    "type": "match_id",
                    "payload": json.dumps({"match_id": f"NA1_{i}"}),
                    "failure_code": "http_5xx",
                    "failure_reason": "error",
                    "failed_by": "fetcher",
                    "original_stream": "stream:match_id",
                    "attempts": "1",
                    "max_attempts": "3",
                },
            )
        resp = await client.post("/dlq/clear", headers=_auth_headers())
        assert resp.status_code == 200
        remaining = await r.xlen("stream:dlq")
        assert remaining == 0

    @pytest.mark.asyncio
    async def test_dlq_clear__empty_dlq__returns_200(self, client):
        """Clearing an already-empty DLQ is a no-op 200."""
        resp = await client.post("/dlq/clear", headers=_auth_headers())
        assert resp.status_code == 200


# --------------------------------------------------------------------------
# POST /system/halt
# --------------------------------------------------------------------------


class TestSystemHalt:
    """POST /system/halt sets system:halted in Redis."""

    @pytest.mark.asyncio
    async def test_system_halt__sets_halted_key(self, client, r):
        """After halting, system:halted key exists in Redis."""
        resp = await client.post("/system/halt", headers=_auth_headers())
        assert resp.status_code == 200
        halted = await r.get("system:halted")
        assert halted is not None

    @pytest.mark.asyncio
    async def test_system_halt__response_confirms_halted(self, client):
        """Response body confirms the system is halted."""
        resp = await client.post("/system/halt", headers=_auth_headers())
        data = resp.json()
        assert data["halted"] is True


# --------------------------------------------------------------------------
# POST /system/resume
# --------------------------------------------------------------------------


class TestSystemResume:
    """POST /system/resume deletes system:halted from Redis."""

    @pytest.mark.asyncio
    async def test_system_resume__clears_halted_key(self, client, r):
        """After resuming, system:halted key no longer exists."""
        await r.set("system:halted", "1")
        resp = await client.post("/system/resume", headers=_auth_headers())
        assert resp.status_code == 200
        halted = await r.get("system:halted")
        assert halted is None

    @pytest.mark.asyncio
    async def test_system_resume__response_confirms_resumed(self, client):
        """Response body confirms the system is no longer halted."""
        resp = await client.post("/system/resume", headers=_auth_headers())
        data = resp.json()
        assert data["halted"] is False

    @pytest.mark.asyncio
    async def test_system_resume__idempotent(self, client, r):
        """Resuming when not halted is a no-op 200."""
        resp = await client.post("/system/resume", headers=_auth_headers())
        assert resp.status_code == 200
        halted = await r.get("system:halted")
        assert halted is None
