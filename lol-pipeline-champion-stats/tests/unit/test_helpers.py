"""Unit tests for champion-stats _helpers.py (PRIN-CHS-01/02)."""

from __future__ import annotations

from lol_pipeline.constants import RANKED_SOLO_QUEUE_ID

from lol_champion_stats._helpers import (
    RankedContext,
    _builds_key,
    _extract_ranked_context,
    _matchup_key,
    _runes_key,
    _stats_key,
)

# -------------------------------------------------------------------------
# _extract_ranked_context — PRIN-CHS-01
# -------------------------------------------------------------------------


class TestExtractRankedContext:
    """PRIN-CHS-01: _extract_ranked_context validates ranked match data."""

    def test_valid_ranked_match__returns_context(self):
        participant = {
            "champion_name": "Jinx",
            "team_position": "BOTTOM",
        }
        match_meta = {
            "queue_id": RANKED_SOLO_QUEUE_ID,
            "patch": "14.5",
        }
        ctx = _extract_ranked_context(participant, match_meta)
        assert ctx is not None
        assert ctx.champion_name == "Jinx"
        assert ctx.patch == "14.5"
        assert ctx.team_position == "BOTTOM"

    def test_returns_named_tuple(self):
        participant = {"champion_name": "Lux", "team_position": "MIDDLE"}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID, "patch": "14.3"}
        ctx = _extract_ranked_context(participant, match_meta)
        assert isinstance(ctx, RankedContext)

    def test_non_ranked_queue__returns_none(self):
        participant = {"champion_name": "Jinx", "team_position": "BOTTOM"}
        match_meta = {"queue_id": "450", "patch": "14.5"}  # ARAM
        assert _extract_ranked_context(participant, match_meta) is None

    def test_missing_queue_id__returns_none(self):
        participant = {"champion_name": "Jinx", "team_position": "BOTTOM"}
        match_meta = {"patch": "14.5"}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_missing_patch__returns_none(self):
        participant = {"champion_name": "Jinx", "team_position": "BOTTOM"}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_empty_patch__returns_none(self):
        participant = {"champion_name": "Jinx", "team_position": "BOTTOM"}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID, "patch": ""}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_missing_team_position__returns_none(self):
        participant = {"champion_name": "Jinx"}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID, "patch": "14.5"}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_empty_team_position__returns_none(self):
        participant = {"champion_name": "Jinx", "team_position": ""}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID, "patch": "14.5"}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_missing_champion_name__returns_none(self):
        participant = {"team_position": "BOTTOM"}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID, "patch": "14.5"}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_empty_champion_name__returns_none(self):
        participant = {"champion_name": "", "team_position": "BOTTOM"}
        match_meta = {"queue_id": RANKED_SOLO_QUEUE_ID, "patch": "14.5"}
        assert _extract_ranked_context(participant, match_meta) is None

    def test_empty_dicts__returns_none(self):
        assert _extract_ranked_context({}, {}) is None


# -------------------------------------------------------------------------
# Key builders — PRIN-CHS-02
# -------------------------------------------------------------------------


class TestStatsKey:
    def test_stats_key__format(self):
        assert _stats_key("Jinx", "14.5", "BOTTOM") == "champion:stats:Jinx:14.5:BOTTOM"

    def test_stats_key__different_values(self):
        assert _stats_key("Lux", "14.3", "MIDDLE") == "champion:stats:Lux:14.3:MIDDLE"


class TestBuildsKey:
    def test_builds_key__format(self):
        assert _builds_key("Jinx", "14.5", "BOTTOM") == "champion:builds:Jinx:14.5:BOTTOM"


class TestRunesKey:
    def test_runes_key__format(self):
        assert _runes_key("Jinx", "14.5", "BOTTOM") == "champion:runes:Jinx:14.5:BOTTOM"


class TestMatchupKey:
    def test_matchup_key__format(self):
        assert _matchup_key("Jinx", "Lux", "BOTTOM", "14.5") == "matchup:Jinx:Lux:BOTTOM:14.5"

    def test_matchup_key__order_matters(self):
        """Champions are expected pre-sorted; key reflects the order given."""
        key_ab = _matchup_key("Aatrox", "Zed", "TOP", "14.5")
        key_ba = _matchup_key("Zed", "Aatrox", "TOP", "14.5")
        assert key_ab != key_ba
