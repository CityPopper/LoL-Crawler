"""Localization string table and lookup functions.

All user-facing strings go through ``t()`` (auto-escaped) or ``t_raw()``
(unescaped, for intentional HTML).  Both languages must have identical key sets.

``t()`` and ``t_raw()`` read the active language from the ``_current_lang``
context variable (set by middleware in ``main.py``).  An explicit ``lang``
parameter can override this for tests or special cases.
"""

from __future__ import annotations

import html as _html

SUPPORTED_LANGUAGES: list[str] = ["en", "zh-CN"]

_STRINGS: dict[str, dict[str, str]] = {
    "en": {
        # -- Tab labels --
        "win": "Win",
        "loss": "Loss",
        "ai_score": "AI Score",
        "team_analysis": "Team Analysis",
        "build": "Build",
        "overview": "Overview",
        "timeline": "Timeline",
        # -- Empty states --
        "no_timeline_data": "Timeline data unavailable for this match.",
        "no_build_data": "Build data unavailable for this match.",
        "not_enough_games": "Not enough games for an insight yet.",
        "no_skill_data": "Skill data requires timeline.",
        "no_kill_data": "No kill events recorded for this match.",
        "no_match_history": "No matches found.",
        # -- Grade labels --
        "grade_s": "Exceptional",
        "grade_a": "Great",
        "grade_b": "Good",
        "grade_c": "Below Average",
        "grade_d": "Poor",
        # -- Team labels --
        "blue_team": "Blue Team",
        "red_team": "Red Team",
        # -- Common --
        "loading": "Loading\u2026",
        "load_more": "Load More",
        "player_stats": "Player Stats",
        "build_order": "Build Order",
        "match_details_unavailable": "Match details not available.",
        # -- Stat labels --
        "gold": "Gold",
        "damage": "Damage",
        "kills": "Kills",
        "cs": "CS",
        "vision": "Vision",
        "objectives": "Objectives",
        "kda": "KDA",
        "damage_share": "Damage Share",
        "gold_share": "Gold Share",
        "cs_per_min": "CS/min",
        "kill_participation": "Kill Participation",
        "objective_contribution": "Objective Contribution",
        # -- Build tab --
        "final_items": "Final Items",
        "skill_order": "Skill Order",
        "runes": "Runes",
        "summoner_spells": "Summoner Spells",
        # -- AI Insight --
        "ai_insight_title": "AI Insight",
        "insight_high_kda": "Maintains a high KDA ratio.",
        "insight_low_vision": "Vision score below average.",
        "insight_high_cs": "Consistently high CS per minute.",
        "insight_dominant_role_prefix": "Primarily plays",
        # -- Gold chart --
        "gold_over_time": "Gold Over Time",
        # -- Sprint 5 --
        "recently_played_with": "Recently Played With",
        "games_shared": "games",
        "minimap": "Kill Map",
        "sparkline_7d": "7-Day Win Rate",
        # -- Dashboard --
        "dashboard": "Dashboard",
        "system_status": "System Status",
        "running": "Running",
        "halted": "HALTED",
        "players_tracked": "Players Tracked",
        "total_players": "total players",
        "dead_letter_queue": "Dead Letter Queue",
        "clean": "Clean",
        "errors": "errors",
        "view_streams": "View streams",
        "browse_players": "Browse players",
        "view_dlq": "View DLQ",
        "stream_depths": "Stream Depths",
        "key": "KEY",
        "length": "LENGTH",
        "status": "STATUS",
        "look_up_player": "Look Up a Player",
        "look_up_player_desc": (
            "Enter a Riot ID to view stats or auto-seed the player into the pipeline."
        ),
        "riot_id": "Riot ID",
        "region": "Region",
        "look_up": "Look Up",
        "all_regions": "All regions",
        # -- Nav --
        "nav_dashboard": "Dashboard",
        "nav_stats": "Stats",
        "nav_champions": "Champions",
        "nav_matchups": "Matchups",
        "nav_players": "Players",
        "nav_streams": "Streams",
        "nav_dlq": "DLQ",
        "nav_logs": "Logs",
        # -- Footer --
        "footer_disclaimer": (
            "LoL Pipeline isn\u2019t endorsed by Riot Games and doesn\u2019t"
            " reflect the views or opinions of Riot Games or anyone officially"
            " involved in producing or managing Riot Games properties."
            " League of Legends and Riot Games are trademarks or registered"
            " trademarks of Riot Games, Inc."
        ),
        # -- Error pages --
        "error_title": "Error",
        "redis_error": ("Cannot connect to Redis. Is the stack running? Try: <code>just up</code>"),
        # -- Stats form --
        "stats_form_riot_id_label": "Riot ID:",
        "stats_form_region_label": "Region:",
        "stats_form_submit": "Look Up",
        "stats_form_title": "Player Stats",
        # -- Stats error messages --
        "stats_player_not_found": "Player not found. Check the spelling of the Riot ID.",
        "stats_rate_limited": "Rate limited. Try again in a few seconds.",
        "stats_auth_error": (
            "API key invalid or expired. Update <code>RIOT_API_KEY</code> in"
            " <code>.env</code> and restart, then run"
            " <code>just admin system-resume</code>."
        ),
        "stats_server_error": "Riot servers temporarily unavailable. Try again later.",
        "stats_invalid_riot_id": "Invalid Riot ID \u2014 expected GameName#TagLine",
        "stats_for": "Stats for",
        "stats_no_api_data": "No verified API stats yet (pipeline still processing).",
        "unranked": "Unranked",
        "theme_label": "Theme:",
        "matchups_placeholder_champ": "e.g. Jinx",
        "matchups_placeholder_patch": "e.g. 14.5",
        # -- Page titles & headings --
        "page_streams": "Streams",
        "page_players": "Players",
        "page_champions": "Champions",
        "page_champion_detail": "Champion Detail",
        "page_matchups": "Matchups",
        "page_champion_matchups": "Champion Matchups",
        "page_logs": "Logs",
        "page_dlq": "Dead Letter Queue",
        "page_dlq_replay_failed": "DLQ Replay Failed",
        # -- Streams page --
        "streams_system_running": "System running",
        "streams_priority_label": "Priority players in-flight:",
        "streams_yes": "Yes",
        "streams_no": "No",
        "streams_pause": "Pause",
        "streams_resume": "Resume",
        "streams_auto_refresh": "Auto-refresh every 5s",
        "streams_col_key": "Key",
        "streams_col_length": "Length",
        "streams_col_group": "Group",
        "streams_col_pending": "Pending",
        "streams_col_lag": "Lag",
        "streams_col_status": "Status",
        # -- Players page --
        "players_no_players": "No players seeded yet",
        "players_seed_hint": "Run <code>just seed GameName#Tag</code> to get started.",
        "players_total_page": "total, page",
        "players_of": "of",
        "players_sort": "Sort:",
        "players_sort_rank": "Rank",
        "players_sort_name": "Name",
        "players_sort_region": "Region",
        "players_filter": "Filter players...",
        "players_filter_aria": "Filter players by name",
        "players_col_riot_id": "Riot ID",
        "players_col_region": "Region",
        "players_col_rank": "Rank",
        "players_col_seeded": "Seeded",
        "players_all_regions": "All Regions",
        "players_prev": "Prev",
        "players_next": "Next",
        "players_page": "page",
        # -- Champions page --
        "champions_no_data": "No champion data yet",
        "champions_no_data_hint": "Seed some players and wait for matches to be analyzed.",
        "champions_patch_prefix": "Champions \u2014 Patch",
        "champions_no_data_for": "No data for",
        "champions_no_stats_hint": "This champion has no stats on this patch.",
        # -- Matchups page --
        "matchups_champ_a": "Champion A:",
        "matchups_champ_b": "Champion B:",
        "matchups_role": "Role:",
        "matchups_patch_optional": "Patch (optional):",
        "matchups_compare": "Compare",
        "matchups_no_patch_data": "No patch data",
        "matchups_no_patch_hint": "No patches found. Seed players and wait for analysis.",
        "matchups_no_matchup_data": "No matchup data",
        "matchups_no_games_for": "No games found for",
        "matchups_vs": "vs",
        "matchups_as": "as",
        "matchups_games": "games",
        "matchups_win_rate": "Win Rate",
        "matchups_new_lookup": "New matchup lookup",
        # -- DLQ page --
        "dlq_empty": "DLQ is empty",
        "dlq_healthy": "No failed messages. The pipeline is healthy.",
        "dlq_total_entries": "Total entries:",
        "dlq_showing_per_page": "Showing {n} per page.",
        "dlq_col_entry_id": "Entry ID",
        "dlq_col_failure_code": "Failure Code",
        "dlq_col_original_stream": "Original Stream",
        "dlq_col_service": "Service",
        "dlq_col_attempts": "Attempts",
        "dlq_col_payload": "Payload",
        "dlq_col_action": "Action",
        "dlq_replay": "Replay",
        "dlq_failure_reason": "Failure Reason:",
        "dlq_entry_not_found": "not found.",
        "dlq_entry_corrupt": "is corrupt and cannot be replayed.",
        "dlq_invalid_stream": "has invalid original_stream",
        "dlq_replay_refused": "replay refused.",
        "dlq_back": "Back to DLQ",
        "dlq_remove_hint": "To remove it, run",
        "dlq_analytics_title": "DLQ Analytics",
        "dlq_stat_pending": "pending",
        "dlq_stat_archived": "archived",
        "dlq_stat_oldest": "oldest message",
        "dlq_breakdown_failure_codes": "Failure Codes",
        "dlq_breakdown_source_streams": "Source Streams",
        "dlq_breakdown_col_code": "Code",
        "dlq_breakdown_col_count": "Count",
        "dlq_breakdown_col_stream": "Stream",
        # -- Logs page --
        "logs_no_log_dir": "LOG_DIR not configured",
        "logs_no_log_dir_hint": "Add it to docker-compose.yml.",
        "logs_no_files": "No log files found",
        "logs_no_files_hint": "Services may not have started yet.",
        "logs_all_services": "All services",
        "logs_service_label": "Service:",
        "logs_pause": "Pause",
        "logs_resume": "Resume",
        "logs_clear": "Clear",
        "logs_services_prefix": "Services:",
        "logs_last_n_lines": "last {n} lines — auto-refresh every 2s",
        # -- Stream badges --
        "badge_ok": "OK",
        "badge_busy": "Busy",
        "badge_backlog": "Backlog",
        # -- Match detail --
        "match_details_unavailable_short": "Match details not available",
    },
    "zh-CN": {
        # -- Tab labels --
        "win": "\u80dc\u5229",
        "loss": "\u5931\u8d25",
        "ai_score": "AI\u8bc4\u5206",
        "team_analysis": "\u56e2\u961f\u5206\u6790",
        "build": "\u51fa\u88c5",
        "overview": "\u6982\u89c8",
        "timeline": "\u65f6\u95f4\u7ebf",
        # -- Empty states --
        "no_timeline_data": "\u672c\u573a\u6bd4\u8d5b\u65e0\u65f6\u95f4\u7ebf\u6570\u636e\u3002",
        "no_build_data": "\u672c\u573a\u6bd4\u8d5b\u65e0\u51fa\u88c5\u6570\u636e\u3002",
        "not_enough_games": "游戏局数不足，无法生成分析。",
        "no_skill_data": "\u6280\u80fd\u6570\u636e\u9700\u8981\u65f6\u95f4\u7ebf\u3002",
        "no_kill_data": "\u672c\u573a\u6bd4\u8d5b\u65e0\u51fb\u6740\u4e8b\u4ef6\u8bb0\u5f55\u3002",
        "no_match_history": "\u672a\u627e\u5230\u6bd4\u8d5b\u8bb0\u5f55\u3002",
        # -- Grade labels --
        "grade_s": "\u5353\u8d8a",
        "grade_a": "\u4f18\u79c0",
        "grade_b": "\u826f\u597d",
        "grade_c": "\u4e00\u822c",
        "grade_d": "\u8f83\u5dee",
        # -- Team labels --
        "blue_team": "\u84dd\u8272\u65b9",
        "red_team": "\u7ea2\u8272\u65b9",
        # -- Common --
        "loading": "\u52a0\u8f7d\u4e2d\u2026",
        "load_more": "\u52a0\u8f7d\u66f4\u591a",
        "player_stats": "\u73a9\u5bb6\u6570\u636e",
        "build_order": "\u51fa\u88c5\u987a\u5e8f",
        "match_details_unavailable": "\u6bd4\u8d5b\u8be6\u60c5\u4e0d\u53ef\u7528\u3002",
        # -- Stat labels --
        "gold": "\u91d1\u5e01",
        "damage": "\u4f24\u5bb3",
        "kills": "\u51fb\u6740",
        "cs": "\u8865\u5200",
        "vision": "\u89c6\u91ce",
        "objectives": "\u76ee\u6807",
        "kda": "KDA",
        "damage_share": "\u4f24\u5bb3\u5360\u6bd4",
        "gold_share": "\u91d1\u5e01\u5360\u6bd4",
        "cs_per_min": "\u6bcf\u5206\u8865\u5200",
        "kill_participation": "\u53c2\u56e2\u7387",
        "objective_contribution": "\u76ee\u6807\u8d21\u732e",
        # -- Build tab --
        "final_items": "\u6700\u7ec8\u88c5\u5907",
        "skill_order": "\u6280\u80fd\u52a0\u70b9",
        "runes": "\u7b26\u6587",
        "summoner_spells": "\u53ec\u5524\u5e08\u6280\u80fd",
        # -- AI Insight --
        "ai_insight_title": "AI\u5206\u6790",
        "insight_high_kda": "\u4fdd\u6301\u8f83\u9ad8\u7684KDA\u6bd4\u7387\u3002",
        "insight_low_vision": "\u89c6\u91ce\u5f97\u5206\u4f4e\u4e8e\u5e73\u5747\u6c34\u5e73\u3002",
        "insight_high_cs": "持续保持较高的每分补刀。",
        "insight_dominant_role_prefix": "\u4e3b\u8981\u626e\u6f14",
        # -- Gold chart --
        "gold_over_time": "\u91d1\u5e01\u8d70\u52bf",
        # -- Sprint 5 --
        "recently_played_with": "\u6700\u8fd1\u4e00\u8d77\u73a9\u7684",
        "games_shared": "\u573a",
        "minimap": "\u51fb\u6740\u5730\u56fe",
        "sparkline_7d": "7天胜率",
        # -- Dashboard --
        "dashboard": "仪表盘",
        "system_status": "系统状态",
        "running": "运行中",
        "halted": "已停止",
        "players_tracked": "追踪玩家",
        "total_players": "总玩家数",
        "dead_letter_queue": "死信队列",
        "clean": "正常",
        "errors": "个错误",
        "view_streams": "查看流",
        "browse_players": "浏览玩家",
        "view_dlq": "查看死信队列",
        "stream_depths": "流深度",
        "key": "键名",
        "length": "长度",
        "status": "状态",
        "look_up_player": "查找玩家",
        "look_up_player_desc": "输入Riot ID查看数据或自动加入追踪。",
        "riot_id": "Riot ID",
        "region": "大区",
        "look_up": "查找",
        "all_regions": "所有大区",
        # -- Nav --
        "nav_dashboard": "仪表盘",
        "nav_stats": "数据",
        "nav_champions": "英雄",
        "nav_matchups": "对位",
        "nav_players": "玩家",
        "nav_streams": "流",
        "nav_dlq": "死信",
        "nav_logs": "日志",
        # -- Footer --
        "footer_disclaimer": (
            "LoL Pipeline \u672a\u83b7\u5f97 Riot Games \u7684\u8ba4\u53ef\uff0c"
            "\u4e0d\u4ee3\u8868 Riot Games"
            " \u6216\u4efb\u4f55\u6b63\u5f0f\u53c2\u4e0e\u5236\u4f5c\u6216\u7ba1\u7406"
            " Riot Games 资产的相关人员的观点或意见。"
            "\u300a\u82f1\u96c4\u8054\u76df\u300b\u548c Riot Games \u662f"
            " Riot Games, Inc. \u7684\u5546\u6807\u6216\u6ce8\u518c\u5546\u6807\u3002"
        ),
        # -- Error pages --
        "error_title": "错误",
        "redis_error": "无法连接到 Redis。服务是否在运行？请尝试：<code>just up</code>",
        # -- Stats form --
        "stats_form_riot_id_label": "Riot ID：",
        "stats_form_region_label": "大区：",
        "stats_form_submit": "查找",
        "stats_form_title": "玩家数据",
        # -- Stats error messages --
        "stats_player_not_found": "未找到玩家。请检查 Riot ID 拼写。",
        "stats_rate_limited": "请求频率过高，请稍后再试。",
        "stats_auth_error": (
            "API 密钥无效或已过期。请更新 <code>.env</code> 中的"
            " <code>RIOT_API_KEY</code> 并重启，然后运行"
            " <code>just admin system-resume</code>。"
        ),
        "stats_server_error": "Riot 服务器暂时不可用，请稍后再试。",
        "stats_invalid_riot_id": "Riot ID 格式无效——应为 GameName#TagLine",
        "stats_for": "\u6570\u636e\uff1a",
        "stats_no_api_data": "暂无已验证的 API 数据（流水线仍在处理中）。",
        "unranked": "未定级",
        "theme_label": "主题：",
        "matchups_placeholder_champ": "如：金克丝",
        "matchups_placeholder_patch": "如：14.5",
        # -- Page titles & headings --
        "page_streams": "流",
        "page_players": "玩家",
        "page_champions": "英雄",
        "page_champion_detail": "英雄详情",
        "page_matchups": "对位",
        "page_champion_matchups": "英雄对位",
        "page_logs": "日志",
        "page_dlq": "死信队列",
        "page_dlq_replay_failed": "死信重放失败",
        # -- Streams page --
        "streams_system_running": "系统运行中",
        "streams_priority_label": "优先玩家处理中：",
        "streams_yes": "是",
        "streams_no": "否",
        "streams_pause": "暂停",
        "streams_resume": "恢复",
        "streams_auto_refresh": "每5秒自动刷新",
        "streams_col_key": "键名",
        "streams_col_length": "长度",
        "streams_col_group": "消费组",
        "streams_col_pending": "待处理",
        "streams_col_lag": "延迟",
        "streams_col_status": "状态",
        # -- Players page --
        "players_no_players": "暂无追踪的玩家",
        "players_seed_hint": "运行 <code>just seed GameName#Tag</code> 开始追踪。",
        "players_total_page": "个，第",
        "players_of": "/",
        "players_sort": "排序：",
        "players_sort_rank": "段位",
        "players_sort_name": "名称",
        "players_sort_region": "大区",
        "players_filter": "筛选玩家...",
        "players_filter_aria": "按名称筛选玩家",
        "players_col_riot_id": "Riot ID",
        "players_col_region": "大区",
        "players_col_rank": "段位",
        "players_col_seeded": "加入时间",
        "players_all_regions": "所有大区",
        "players_prev": "上一页",
        "players_next": "下一页",
        "players_page": "第",
        # -- Champions page --
        "champions_no_data": "暂无英雄数据",
        "champions_no_data_hint": "请先追踪一些玩家，等待比赛数据分析完成。",
        "champions_patch_prefix": "英雄 \u2014 版本",
        "champions_no_data_for": "暂无数据 -",
        "champions_no_stats_hint": "该英雄在此版本暂无数据。",
        # -- Matchups page --
        "matchups_champ_a": "英雄 A：",
        "matchups_champ_b": "英雄 B：",
        "matchups_role": "位置：",
        "matchups_patch_optional": "版本（可选）：",
        "matchups_compare": "对比",
        "matchups_no_patch_data": "暂无版本数据",
        "matchups_no_patch_hint": "未找到版本信息。请先追踪玩家并等待分析完成。",
        "matchups_no_matchup_data": "暂无对位数据",
        "matchups_no_games_for": "未找到对局记录 -",
        "matchups_vs": "vs",
        "matchups_as": "\u4f7f\u7528",
        "matchups_games": "场比赛",
        "matchups_win_rate": "胜率",
        "matchups_new_lookup": "重新查找对位",
        # -- DLQ page --
        "dlq_empty": "死信队列为空",
        "dlq_healthy": "无失败消息。流水线运行正常。",
        "dlq_total_entries": "总条目数：",
        "dlq_showing_per_page": "每页显示 {n} 条。",
        "dlq_col_entry_id": "条目 ID",
        "dlq_col_failure_code": "失败代码",
        "dlq_col_original_stream": "原始流",
        "dlq_col_service": "服务",
        "dlq_col_attempts": "尝试次数",
        "dlq_col_payload": "\u6d88\u606f\u4f53",
        "dlq_col_action": "操作",
        "dlq_replay": "重放",
        "dlq_failure_reason": "失败原因：",
        "dlq_entry_not_found": "未找到。",
        "dlq_entry_corrupt": "已损坏，无法重放。",
        "dlq_invalid_stream": "原始流无效",
        "dlq_replay_refused": "拒绝重放。",
        "dlq_back": "返回死信队列",
        "dlq_remove_hint": "要删除此条目，请运行",
        "dlq_analytics_title": "死信队列分析",
        "dlq_stat_pending": "待处理",
        "dlq_stat_archived": "已归档",
        "dlq_stat_oldest": "最早消息",
        "dlq_breakdown_failure_codes": "失败代码",
        "dlq_breakdown_source_streams": "来源流",
        "dlq_breakdown_col_code": "代码",
        "dlq_breakdown_col_count": "数量",
        "dlq_breakdown_col_stream": "流",
        # -- Logs page --
        "logs_no_log_dir": "LOG_DIR 未配置",
        "logs_no_log_dir_hint": "请在 docker-compose.yml 中添加。",
        "logs_no_files": "未找到日志文件",
        "logs_no_files_hint": "服务可能尚未启动。",
        "logs_all_services": "所有服务",
        "logs_service_label": "服务：",
        "logs_pause": "暂停",
        "logs_resume": "恢复",
        "logs_clear": "清除",
        "logs_services_prefix": "服务：",
        "logs_last_n_lines": "最近 {n} 行，每2秒自动刷新",
        # -- Stream badges --
        "badge_ok": "正常",
        "badge_busy": "繁忙",
        "badge_backlog": "积压",
        # -- Match detail --
        "match_details_unavailable_short": "比赛详情不可用",
    },
}


def t(key: str, lang: str | None = None) -> str:
    """Return localized string, HTML-escaped by default.

    When *lang* is not provided, reads the active language from the
    ``_current_lang`` context variable (set by middleware).
    Falls back to the key itself when the key is not found.
    """
    if lang is None:
        from lol_ui.language import _current_lang

        lang = _current_lang.get()
    raw = _STRINGS.get(lang, _STRINGS["en"]).get(key, key)
    return _html.escape(raw)


def t_raw(key: str, lang: str | None = None) -> str:
    """Return localized string without escaping (for intentional HTML).

    When *lang* is not provided, reads the active language from the
    ``_current_lang`` context variable (set by middleware).
    """
    if lang is None:
        from lol_ui.language import _current_lang

        lang = _current_lang.get()
    return _STRINGS.get(lang, _STRINGS["en"]).get(key, key)
