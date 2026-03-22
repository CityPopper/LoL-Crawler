"""Tests for tabs.py — tab strip and panel HTML generation."""

from __future__ import annotations

from lol_ui.tabs import (
    _tab_js,
    _tab_panel_html,
    _tab_strip_html,
    _tabbed_match_detail,
)


class TestTabStripHtml:
    """_tab_strip_html produces a scrollable strip of tab buttons."""

    def test_renders_all_tab_labels(self):
        tabs = [("overview", "Overview"), ("build", "Build"), ("ai", "AI Score")]
        result = _tab_strip_html(tabs, active="overview")
        assert "Overview" in result
        assert "Build" in result
        assert "AI Score" in result

    def test_active_tab_has_active_class(self):
        tabs = [("overview", "Overview"), ("build", "Build")]
        result = _tab_strip_html(tabs, active="build")
        # The active button should have the active class
        assert 'tab-btn--active" data-tab="build"' in result

    def test_inactive_tab_lacks_active_class(self):
        tabs = [("overview", "Overview"), ("build", "Build")]
        result = _tab_strip_html(tabs, active="build")
        # overview button should NOT have the active class
        assert 'tab-btn" data-tab="overview"' in result

    def test_strip_has_scrollable_container_class(self):
        tabs = [("overview", "Overview")]
        result = _tab_strip_html(tabs, active="overview")
        assert "tab-strip" in result

    def test_buttons_have_data_tab_attributes(self):
        tabs = [("overview", "Overview"), ("timeline", "Timeline")]
        result = _tab_strip_html(tabs, active="overview")
        assert 'data-tab="overview"' in result
        assert 'data-tab="timeline"' in result

    def test_aria_attributes_on_active_tab(self):
        tabs = [("overview", "Overview"), ("build", "Build")]
        result = _tab_strip_html(tabs, active="overview")
        assert 'aria-selected="true"' in result
        assert 'aria-selected="false"' in result

    def test_empty_tabs_returns_empty_string(self):
        result = _tab_strip_html([], active="overview")
        assert result == ""

    def test_role_tablist_on_container(self):
        tabs = [("overview", "Overview")]
        result = _tab_strip_html(tabs, active="overview")
        assert 'role="tablist"' in result

    def test_role_tab_on_buttons(self):
        tabs = [("overview", "Overview")]
        result = _tab_strip_html(tabs, active="overview")
        assert 'role="tab"' in result


class TestTabPanelHtml:
    """_tab_panel_html wraps content in a panel div."""

    def test_active_panel_has_no_hidden_style(self):
        result = _tab_panel_html("overview", "<p>Hello</p>", active=True)
        assert 'style="display:none"' not in result
        assert "<p>Hello</p>" in result

    def test_inactive_panel_is_hidden(self):
        result = _tab_panel_html("build", "<p>Items</p>", active=False)
        assert 'style="display:none"' in result
        assert "<p>Items</p>" in result

    def test_panel_has_data_tab_panel_attribute(self):
        result = _tab_panel_html("ai_score", "content", active=True)
        assert 'data-tab-panel="ai_score"' in result

    def test_panel_has_tabpanel_role(self):
        result = _tab_panel_html("overview", "content", active=True)
        assert 'role="tabpanel"' in result

    def test_content_is_included_verbatim(self):
        content = '<div class="match-detail__team">Blue</div>'
        result = _tab_panel_html("overview", content, active=True)
        assert content in result


class TestTabbedMatchDetail:
    """_tabbed_match_detail assembles a full tabbed interface."""

    def test_all_five_tabs_present_with_timeline(self):
        result = _tabbed_match_detail(
            overview_html="<div>overview</div>",
            build_html="<div>build</div>",
            team_html="<div>team</div>",
            ai_html="<div>ai</div>",
            timeline_html="<div>timeline</div>",
            has_timeline=True,
        )
        assert "Overview" in result
        assert "Build" in result
        assert "Team Analysis" in result
        assert "AI Score" in result
        assert "Timeline" in result

    def test_timeline_tab_suppressed_when_no_timeline(self):
        result = _tabbed_match_detail(
            overview_html="<div>overview</div>",
            build_html="<div>build</div>",
            team_html="<div>team</div>",
            ai_html="<div>ai</div>",
            timeline_html="",
            has_timeline=False,
        )
        assert "Overview" in result
        assert "Build" in result
        assert "Team Analysis" in result
        assert "AI Score" in result
        assert "Timeline" not in result

    def test_overview_tab_is_active_by_default(self):
        result = _tabbed_match_detail(
            overview_html="<div>overview</div>",
            build_html="<div>build</div>",
            team_html="<div>team</div>",
            ai_html="<div>ai</div>",
            timeline_html="",
            has_timeline=False,
        )
        assert 'tab-btn tab-btn--active" data-tab="overview"' in result

    def test_overview_panel_visible_others_hidden(self):
        result = _tabbed_match_detail(
            overview_html="<div>overview</div>",
            build_html="<div>build</div>",
            team_html="<div>team</div>",
            ai_html="<div>ai</div>",
            timeline_html="",
            has_timeline=False,
        )
        # overview panel should not be hidden
        # Look for the panel that is NOT hidden
        assert 'data-tab-panel="overview"' in result
        # Build panel should be hidden
        assert 'data-tab-panel="build"' in result

    def test_four_tabs_when_no_timeline(self):
        result = _tabbed_match_detail(
            overview_html="OV",
            build_html="BU",
            team_html="TE",
            ai_html="AI",
            timeline_html="",
            has_timeline=False,
        )
        # Count tab buttons — exactly 4
        assert result.count('role="tab"') == 4

    def test_five_tabs_when_has_timeline(self):
        result = _tabbed_match_detail(
            overview_html="OV",
            build_html="BU",
            team_html="TE",
            ai_html="AI",
            timeline_html="TL",
            has_timeline=True,
        )
        assert result.count('role="tab"') == 5

    def test_tab_content_appears_in_panels(self):
        result = _tabbed_match_detail(
            overview_html="<p>Overview Content</p>",
            build_html="<p>Build Content</p>",
            team_html="<p>Team Content</p>",
            ai_html="<p>AI Content</p>",
            timeline_html="<p>Timeline Content</p>",
            has_timeline=True,
        )
        assert "<p>Overview Content</p>" in result
        assert "<p>Build Content</p>" in result
        assert "<p>Team Content</p>" in result
        assert "<p>AI Content</p>" in result
        assert "<p>Timeline Content</p>" in result

    def test_container_has_match_tabs_class(self):
        result = _tabbed_match_detail(
            overview_html="OV",
            build_html="BU",
            team_html="TE",
            ai_html="AI",
            timeline_html="",
            has_timeline=False,
        )
        assert "match-tabs" in result

    def test_tab_order_is_overview_build_team_ai_timeline(self):
        result = _tabbed_match_detail(
            overview_html="OV",
            build_html="BU",
            team_html="TE",
            ai_html="AI",
            timeline_html="TL",
            has_timeline=True,
        )
        ov_pos = result.index("Overview")
        bu_pos = result.index("Build")
        te_pos = result.index("Team Analysis")
        ai_pos = result.index("AI Score")
        tl_pos = result.index("Timeline")
        assert ov_pos < bu_pos < te_pos < ai_pos < tl_pos


class TestTabJs:
    """_tab_js returns JS for tab switching via event delegation."""

    def test_defines_init_match_tabs(self):
        result = _tab_js()
        assert "initMatchTabs" in result

    def test_uses_class_list_toggle(self):
        result = _tab_js()
        assert "classList" in result

    def test_wrapped_in_script_tag(self):
        result = _tab_js()
        assert result.strip().startswith("<script>")
        assert result.strip().endswith("</script>")

    def test_uses_event_delegation(self):
        result = _tab_js()
        # Should use addEventListener or event delegation pattern
        assert "addEventListener" in result or "closest" in result
