"""RUN-002 regression: no spurious opgg warning when opgg is absent from waterfall order.

Original bug: When opgg was not in SOURCE_WATERFALL_ORDER, the fetcher logged a
warning: "source 'opgg' in source_waterfall_order is not available, skipping".
This was noise because opgg was never requested.

opgg is now automatically enabled when "opgg" appears in SOURCE_WATERFALL_ORDER
(no separate OPGG_ENABLED flag). This test verifies:
1. SOURCE_WATERFALL_ORDER=riot produces no opgg warnings.
2. SOURCE_WATERFALL_ORDER=riot,opgg (without an OpggClient) warns that opgg is
   not available — expected, since the client was not provided.
"""

from __future__ import annotations

import logging
import logging.handlers

from lol_pipeline.config import Config
from lol_pipeline.raw_store import RawStore
from lol_pipeline.riot_api import RiotClient

from lol_fetcher.main import _build_coordinator


def _capture_fetcher_warnings(cfg: Config) -> list[str]:
    """Build coordinator and return all warning-level log messages from the fetcher logger."""
    fetcher_log = logging.getLogger("fetcher")
    handler = logging.handlers.MemoryHandler(capacity=100)
    handler.setLevel(logging.WARNING)
    fetcher_log.addHandler(handler)
    try:
        riot = RiotClient("RGAPI-test")
        raw_store = RawStore(None)
        _build_coordinator(riot, raw_store, cfg, opgg=None)
        return [record.getMessage() for record in handler.buffer if record.levelno >= logging.WARNING]
    finally:
        fetcher_log.removeHandler(handler)


def test_build_coordinator__riot_only__no_opgg_warning(monkeypatch):
    """RUN-002: SOURCE_WATERFALL_ORDER=riot produces no opgg warning."""
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    monkeypatch.setenv("SOURCE_WATERFALL_ORDER", "riot")
    cfg = Config(_env_file=None)  # type: ignore[call-arg]

    warnings = _capture_fetcher_warnings(cfg)

    opgg_warnings = [m for m in warnings if "opgg" in m.lower()]
    assert opgg_warnings == [], (
        f"Expected no opgg warnings when opgg not in waterfall order, "
        f"got: {opgg_warnings}"
    )


def test_build_coordinator__opgg_in_order_no_client__warns(monkeypatch):
    """RUN-002: SOURCE_WATERFALL_ORDER=riot,opgg without OpggClient warns about opgg."""
    monkeypatch.setenv("RIOT_API_KEY", "RGAPI-test")
    monkeypatch.setenv("REDIS_URL", "redis://localhost")
    monkeypatch.setenv("SOURCE_WATERFALL_ORDER", "riot,opgg")
    cfg = Config(_env_file=None)  # type: ignore[call-arg]

    warnings = _capture_fetcher_warnings(cfg)

    opgg_warnings = [m for m in warnings if "opgg" in m.lower()]
    assert len(opgg_warnings) == 1, (
        f"Expected exactly 1 opgg warning when opgg is in order but client is absent, "
        f"got {len(opgg_warnings)}: {opgg_warnings}"
    )
    assert "not available" in opgg_warnings[0]
