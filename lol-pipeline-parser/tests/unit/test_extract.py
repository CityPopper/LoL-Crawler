"""Unit tests for lol_parser._extract — IMP-075: _extract_all_perks coverage."""

from __future__ import annotations

from lol_parser._extract import _extract_all_perks


class TestExtractAllPerks:
    """IMP-075: _extract_all_perks must return a full 6-tuple for all edge cases."""

    def test_normal__full_rune_page(self) -> None:
        """Standard rune page: 4 primary, 2 sub, 3 stat shards."""
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [
                            {"perk": 8112},
                            {"perk": 8126},
                            {"perk": 8139},
                            {"perk": 8135},
                        ],
                    },
                    {
                        "style": 8300,
                        "selections": [
                            {"perk": 8304},
                            {"perk": 8345},
                        ],
                    },
                ],
                "statPerks": {
                    "offense": 5008,
                    "flex": 5002,
                    "defense": 5001,
                },
            },
        }
        keystone, primary_id, sub_id, primary_sel, sub_sel, stat_shards = _extract_all_perks(p)
        assert keystone == 8112
        assert primary_id == 8100
        assert sub_id == 8300
        assert primary_sel == [8112, 8126, 8139, 8135]
        assert sub_sel == [8304, 8345]
        assert stat_shards == [5008, 5002, 5001]

    def test_empty_participant__all_defaults(self) -> None:
        """Empty dict returns (0, 0, 0, [], [], [])."""
        keystone, primary_id, sub_id, primary_sel, sub_sel, stat_shards = _extract_all_perks({})
        assert keystone == 0
        assert primary_id == 0
        assert sub_id == 0
        assert primary_sel == []
        assert sub_sel == []
        assert stat_shards == []

    def test_perks_key_empty_dict(self) -> None:
        """perks={} with no styles or statPerks returns all defaults."""
        result = _extract_all_perks({"perks": {}})
        assert result == (0, 0, 0, [], [], [])

    def test_empty_styles_list(self) -> None:
        """perks.styles=[] returns keystone=0 and empty selections."""
        result = _extract_all_perks({"perks": {"styles": []}})
        assert result == (0, 0, 0, [], [], [])

    def test_no_sub_style(self) -> None:
        """Only primary style present; sub_style_id=0, sub_sel=[]."""
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [{"perk": 8112}, {"perk": 8126}],
                    },
                ],
            },
        }
        keystone, primary_id, sub_id, primary_sel, sub_sel, stat_shards = _extract_all_perks(p)
        assert keystone == 8112
        assert primary_id == 8100
        assert sub_id == 0
        assert primary_sel == [8112, 8126]
        assert sub_sel == []
        assert stat_shards == []

    def test_empty_selections(self) -> None:
        """Styles with empty selections arrays."""
        p = {
            "perks": {
                "styles": [
                    {"style": 8100, "selections": []},
                    {"style": 8300, "selections": []},
                ],
            },
        }
        keystone, primary_id, sub_id, primary_sel, sub_sel, stat_shards = _extract_all_perks(p)
        assert keystone == 0
        assert primary_id == 8100
        assert sub_id == 8300
        assert primary_sel == []
        assert sub_sel == []
        assert stat_shards == []

    def test_partial_stat_perks__only_offense(self) -> None:
        """StatPerks with only offense present."""
        p = {
            "perks": {
                "styles": [],
                "statPerks": {"offense": 5008},
            },
        }
        result = _extract_all_perks(p)
        assert result == (0, 0, 0, [], [], [5008])

    def test_partial_stat_perks__only_defense(self) -> None:
        """StatPerks with only defense present."""
        p = {
            "perks": {
                "styles": [],
                "statPerks": {"defense": 5001},
            },
        }
        result = _extract_all_perks(p)
        assert result == (0, 0, 0, [], [], [5001])

    def test_partial_stat_perks__offense_and_defense_no_flex(self) -> None:
        """StatPerks missing flex; only offense and defense present."""
        p = {
            "perks": {
                "styles": [],
                "statPerks": {"offense": 5008, "defense": 5001},
            },
        }
        result = _extract_all_perks(p)
        assert result == (0, 0, 0, [], [], [5008, 5001])

    def test_missing_perk_key_in_selection__defaults_to_zero(self) -> None:
        """Selection dicts missing the 'perk' key default to 0."""
        p = {
            "perks": {
                "styles": [
                    {
                        "style": 8100,
                        "selections": [{}, {"perk": 8126}],
                    },
                ],
            },
        }
        keystone, _, _, primary_sel, _, _ = _extract_all_perks(p)
        assert keystone == 0  # first selection has no perk key
        assert primary_sel == [0, 8126]

    def test_no_perks_key_at_all(self) -> None:
        """Participant with other fields but no 'perks' key."""
        p = {"championName": "Jinx", "kills": 5}
        result = _extract_all_perks(p)
        assert result == (0, 0, 0, [], [], [])

    def test_stat_perks_empty_dict(self) -> None:
        """statPerks present but empty dict yields empty shards list."""
        p = {
            "perks": {
                "styles": [
                    {"style": 8100, "selections": [{"perk": 8112}]},
                ],
                "statPerks": {},
            },
        }
        _, _, _, _, _, stat_shards = _extract_all_perks(p)
        assert stat_shards == []
