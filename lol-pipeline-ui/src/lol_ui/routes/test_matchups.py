"""Tests for matchups route — datalist autocomplete and role localization."""

from __future__ import annotations

from lol_ui.routes.matchups import _champion_datalist, _role_options  # type: ignore[attr-defined]


class TestChampionDatalist:
    """_champion_datalist renders <datalist> for champion autocomplete."""

    def test_empty_map__returns_empty_string(self):
        assert _champion_datalist({}) == ""

    def test_single_champion__renders_option(self):
        result = _champion_datalist({"Jinx": "Jinx"})
        assert '<datalist id="champion-list">' in result
        assert '<option value="Jinx">' in result
        assert "</datalist>" in result

    def test_multiple_champions__sorted_alphabetically(self):
        name_map = {"Zyra": "Zyra", "Annie": "Annie", "MonkeyKing": "Wukong"}
        result = _champion_datalist(name_map)
        annie_pos = result.index("Annie")
        wukong_pos = result.index("Wukong")
        zyra_pos = result.index("Zyra")
        assert annie_pos < wukong_pos < zyra_pos

    def test_html_escapes_names(self):
        name_map = {"Test": "A&B"}
        result = _champion_datalist(name_map)
        assert "A&amp;B" in result

    def test_chinese_names__renders_correctly(self):
        name_map = {"Annie": "\u5b89\u59ae"}
        result = _champion_datalist(name_map)
        assert "\u5b89\u59ae" in result


class TestRoleOptions:
    """_role_options renders localized <option> elements."""

    def test_english__renders_display_names(self):
        result = _role_options("en")
        assert '<option value="TOP">Top</option>' in result
        assert '<option value="UTILITY">Support</option>' in result

    def test_chinese__renders_chinese_names(self):
        result = _role_options("zh-CN")
        assert '<option value="TOP">\u4e0a\u5355</option>' in result
        assert '<option value="UTILITY">\u8f85\u52a9</option>' in result

    def test_all_five_roles_present(self):
        result = _role_options("en")
        for value in ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY"):
            assert f'value="{value}"' in result

    def test_unknown_lang__falls_back_to_english(self):
        result = _role_options("fr")
        assert "Top" in result
