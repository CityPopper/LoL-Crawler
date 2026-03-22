"""Tests for profile_tabs.py — profile-level tab navigation."""

from __future__ import annotations

from lol_ui.profile_tabs import (
    _PLACEHOLDER_HTML,
    _PROFILE_TABS,
    _profile_tab_js,
    _profile_tabs_html,
)


class TestProfileTabsHtml:
    """_profile_tabs_html wraps stats content with Summary/Mastery/ARAM tabs."""

    def test_renders_all_three_tab_labels(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert "Summary" in result
        assert "Mastery" in result
        assert "ARAM" in result

    def test_summary_tab_is_active_by_default(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert 'tab-btn--active" data-tab="summary"' in result

    def test_summary_panel_contains_content(self):
        result = _profile_tabs_html("<p>My Stats Content</p>")
        assert "<p>My Stats Content</p>" in result

    def test_mastery_panel_shows_placeholder(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert "Coming soon." in result

    def test_aram_panel_shows_placeholder(self):
        result = _profile_tabs_html("<p>Stats</p>")
        # Two placeholders: mastery + aram
        assert result.count("Coming soon.") == 2

    def test_mastery_panel_is_hidden(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert 'data-tab-panel="mastery"' in result

    def test_aram_panel_is_hidden(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert 'data-tab-panel="aram"' in result

    def test_container_has_profile_tabs_class(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert "profile-tabs" in result

    def test_exactly_three_tab_buttons(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert result.count('role="tab"') == 3

    def test_exactly_three_tab_panels(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert result.count('role="tabpanel"') == 3

    def test_tab_order_is_summary_mastery_aram(self):
        result = _profile_tabs_html("<p>Stats</p>")
        s_pos = result.index("Summary")
        m_pos = result.index("Mastery")
        a_pos = result.index("ARAM")
        assert s_pos < m_pos < a_pos

    def test_has_tablist_role(self):
        result = _profile_tabs_html("<p>Stats</p>")
        assert 'role="tablist"' in result


class TestProfileTabConstants:
    """_PROFILE_TABS constant is well-formed."""

    def test_three_tabs_defined(self):
        assert len(_PROFILE_TABS) == 3

    def test_tab_ids_are_unique(self):
        ids = [t[0] for t in _PROFILE_TABS]
        assert len(ids) == len(set(ids))

    def test_placeholder_contains_coming_soon(self):
        assert "Coming soon" in _PLACEHOLDER_HTML


class TestProfileTabJs:
    """_profile_tab_js returns JS for profile-level tab switching."""

    def test_wrapped_in_script_tag(self):
        result = _profile_tab_js()
        assert result.strip().startswith("<script>")
        assert result.strip().endswith("</script>")

    def test_scoped_to_profile_tabs_container(self):
        result = _profile_tab_js()
        assert "profile-tabs" in result

    def test_uses_event_delegation(self):
        result = _profile_tab_js()
        assert "addEventListener" in result

    def test_uses_class_list_toggle(self):
        result = _profile_tab_js()
        assert "classList" in result
