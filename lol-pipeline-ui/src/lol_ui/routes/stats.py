"""Stats routes — player lookup, match history, match detail."""

from __future__ import annotations

import asyncio
import contextlib
import html
import json
import time
import uuid
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from lol_pipeline._helpers import is_system_halted, name_cache_key
from lol_pipeline.config import Config
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_MANUAL_20, set_priority
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.streams import publish

from lol_ui.build_display import _build_tab_html
from lol_ui.charts.gold_chart import _gold_chart_svg
from lol_ui.charts.minimap import _minimap_html
from lol_ui.constants import (
    _BREAKDOWN_MATCH_COUNT,
    _HALT_BANNER,
    _MATCH_ID_RE,
    _MATCH_PAGE_SIZE,
    _PUUID_RE,
    _REGIONS_SET,
    _SPLIT_MATCH_LIMIT,
    _TILT_RECENT_COUNT,
)
from lol_ui.ddragon import _get_ddragon_version, get_champion_name_map
from lol_ui.kill_timeline import _kill_timeline_html
from lol_ui.language import _current_lang
from lol_ui.match_detail import _render_detail_player
from lol_ui.match_history import _match_history_html, _match_history_section
from lol_ui.playstyle import _playstyle_pills_html, _playstyle_tags
from lol_ui.profile_tabs import _profile_tab_js, _profile_tabs_html
from lol_ui.rank import _profile_header_html, _rank_card_html, _rank_history_html
from lol_ui.recently_played import _recently_played_html
from lol_ui.rendering import _badge, _stats_form
from lol_ui.rune_display import _get_runes_data
from lol_ui.scoring.ai_insight import _ai_insight_html
from lol_ui.scoring.ai_score import _ai_score_tab_html, _compute_ai_score
from lol_ui.sparkline import _sparkline_html
from lol_ui.spell_display import _get_summoner_spell_map
from lol_ui.stats_helpers import (
    _build_minimap_events,
    _build_participant_list,
    _compute_champion_breakdown,
    _compute_role_breakdown,
    _current_split,
    _group_participants,
    _has_timeline_data,
    _stats_table,
)
from lol_ui.strings import t, t_raw
from lol_ui.tabs import _tab_js, _tabbed_match_detail
from lol_ui.team_analysis import _team_analysis_html
from lol_ui.tilt import _streak_indicator, _tilt_banner_html

_log = get_logger("ui")

router = APIRouter()

# Fragment cache — in-memory with TTL (version bumped on deploys for cache busting)
_CACHE_VERSION = "v1"
_CACHE_TTL_S = 6 * 3600  # default; overridden at startup via _init_cache_ttl()
_fragment_cache: dict[str, tuple[str, float]] = {}


def _init_cache_ttl() -> None:
    """Override ``_CACHE_TTL_S`` from Config (called once at app startup)."""
    global _CACHE_TTL_S
    with contextlib.suppress(Exception):
        _CACHE_TTL_S = Config().stats_fragment_cache_ttl_s


def _fragment_get(key: str) -> str | None:
    """Read from the in-memory fragment cache, returning None if missing or expired."""
    entry = _fragment_cache.get(key)
    if entry is None:
        return None
    data, expiry = entry
    if time.monotonic() > expiry:
        _fragment_cache.pop(key, None)
        return None
    return data


def _fragment_put(key: str, data: str) -> None:
    """Store data in the in-memory fragment cache with the standard TTL."""
    _fragment_cache[key] = (data, time.monotonic() + _CACHE_TTL_S)


async def _build_timeline_tab(
    r: Any,
    match_id: str,
    sorted_puuids: list[str],
    focused_puuid: str,
    version: str | None,
    participant_map: dict[str, dict[str, str]] | None = None,
) -> str:
    """Build the Timeline tab content: gold chart + minimap + kill timeline.

    *participant_map* is an optional ``{puuid: participant_hash}`` dict to avoid
    redundant Redis reads when the caller already loaded participant data.
    """
    # Batch gold timeline + kill events reads
    async with r.pipeline(transaction=False) as pipe:
        for p in sorted_puuids:
            pipe.get(f"gold_timeline:{match_id}:{p}")
        pipe.get(f"kill_events:{match_id}")
        pipe_results = await pipe.execute()

    gold_raws = pipe_results[: len(sorted_puuids)]
    kill_events_raw = pipe_results[len(sorted_puuids)]

    # Build gold chart data
    gold_data: dict[str, dict[str, object]] = {}
    blue_idx = 0
    red_idx = 0
    for i, p in enumerate(sorted_puuids):
        raw = gold_raws[i]
        if not raw:
            continue
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            values = json.loads(raw)
            if isinstance(values, list):
                part_data = (
                    participant_map.get(p, {})
                    if participant_map is not None
                    else await r.hgetall(f"participant:{match_id}:{p}")
                )
                team_id = part_data.get("team_id", "100")
                champ = part_data.get("champion_name", "?")
                if team_id == "200":
                    t_idx = red_idx
                    red_idx += 1
                else:
                    t_idx = blue_idx
                    blue_idx += 1
                gold_data[p] = {
                    "gold_values": [v for v in values if isinstance(v, int)],
                    "team_id": team_id,
                    "champion_name": champ,
                    "team_index": t_idx,
                }

    gold_html = _gold_chart_svg(gold_data, focused_puuid, version)

    # Build kill timeline
    kill_events: list[dict[str, object]] = []
    if kill_events_raw:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            parsed = json.loads(kill_events_raw)
            if isinstance(parsed, list):
                kill_events = parsed

    kill_html = _kill_timeline_html(kill_events, version)
    minimap_events = _build_minimap_events(kill_events, gold_data)
    minimap_html = _minimap_html(minimap_events, version)

    if not gold_html and not kill_events:
        return '<p class="warning">Timeline data unavailable for this match.</p>'

    return gold_html + minimap_html + kill_html


# ---------------------------------------------------------------------------
# Helpers (only used by stats routes)
# ---------------------------------------------------------------------------


async def _resolve_puuid(
    r: Any,
    riot: RiotClient,
    riot_id: str,
    game_name: str,
    tag_line: str,
    region: str,
    cfg: Config,
) -> str | HTMLResponse:
    """Look up PUUID from cache or Riot API (read-only — no Redis writes).

    Returns the PUUID string on success, or an HTMLResponse on error.
    """
    cache_key = name_cache_key(game_name, tag_line)
    cached_puuid: str | None = await r.get(cache_key)
    if cached_puuid:
        return cached_puuid
    try:
        await wait_for_token(
            r,
            limit_per_second=cfg.api_rate_limit_per_second,
            region=region,
        )
        account = await riot.get_account_by_riot_id(game_name, tag_line, region)
    except NotFoundError:
        return HTMLResponse(
            _stats_form(
                t("stats_player_not_found"),
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    except RateLimitError:
        return HTMLResponse(
            _stats_form(
                t("stats_rate_limited"),
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    except AuthError:
        return HTMLResponse(
            _stats_form(
                t_raw("stats_auth_error"),
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    except ServerError:
        return HTMLResponse(
            _stats_form(
                t("stats_server_error"),
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    puuid: str = account["puuid"]
    return puuid


def _not_seeded_response(
    game_name: str,
    tag_line: str,
    region: str,
) -> HTMLResponse:
    """Return a read-only 'not seeded' indicator (no Redis writes)."""
    riot_id = f"{game_name}#{tag_line}"
    safe_id = html.escape(riot_id)
    return HTMLResponse(
        _stats_form(
            f"No stats available for {safe_id}. "
            "Use the admin CLI to seed this player: "
            f"<code>just admin seed {safe_id} --region {html.escape(region)}</code>",
            "warning",
            selected_region=region,
            value=riot_id,
        )
    )


async def _load_recent_matches(
    r: Any,
    puuid: str,
    count: int = _BREAKDOWN_MATCH_COUNT,
    since_ms: int = 0,
) -> list[dict[str, str]]:
    """Load up to *count* recent match participant dicts for *puuid*.

    If *since_ms* is set, only matches with game_start >= since_ms are included.
    Returns a list of non-empty participant hashes, newest first.
    """
    key = f"player:matches:{puuid}"
    if since_ms > 0:
        match_pairs = await r.zrevrangebyscore(
            key, "+inf", since_ms, start=0, num=count, withscores=True
        )
    else:
        match_pairs = await r.zrevrange(key, 0, count - 1, withscores=True)
    if not match_pairs:
        return []

    # Batch HGETALL for all participant records in one pipeline RTT
    async with r.pipeline(transaction=False) as pipe:
        for match_id, _ in match_pairs:
            pipe.hgetall(f"participant:{match_id}:{puuid}")
        participant_results: list[dict[str, str]] = await pipe.execute()

    # Filter out empty results (match not yet parsed)
    return [p for p in participant_results if p]


async def _build_stats_response(
    r: Any,
    puuid: str,
    game_name: str,
    tag_line: str,
    region: str,
    riot_id: str,
    stats: dict[str, str],
) -> HTMLResponse:
    """Read Redis hashes and build the stats HTML response."""
    # Batch independent reads into a single pipeline RTT.
    async with r.pipeline(transaction=False) as pipe:
        pipe.get(f"player:priority:{puuid}")
        pipe.hgetall(f"player:rank:{puuid}")
        pipe.zrange(f"player:rank:history:{puuid}", 0, -1, withscores=True)
        pipe.hgetall(f"player:{puuid}")
        pipe.zrevrange(f"player:matches:{puuid}", 0, 19, withscores=True)
        priority_key, rank, rank_hist, player_data, recent_id_pairs = await pipe.execute()

    rank = rank or {}
    rank_hist = rank_hist or []
    player_data = player_data or {}
    priority_html = f" {_badge('info', 'Priority')}" if priority_key else ""

    # Load DDragon version and current-split matches concurrently (2 RTTs -> 1)
    split_label, split_start_ms = _current_split()
    version, split_matches = await asyncio.gather(
        _get_ddragon_version(r),
        _load_recent_matches(
            r,
            puuid,
            count=_SPLIT_MATCH_LIMIT,
            since_ms=split_start_ms,
        ),
    )

    profile_html = _profile_header_html(
        game_name,
        tag_line,
        rank,
        icon_id=player_data.get("profile_icon_id"),
        level=player_data.get("summoner_level"),
        version=version,
    )
    rank_html = _rank_card_html(rank)
    rank_hist_html = _rank_history_html(rank_hist)
    playstyle_html = _playstyle_pills_html(_playstyle_tags(stats))
    tilt_data = _streak_indicator(split_matches[:_TILT_RECENT_COUNT])
    tilt_html = _tilt_banner_html(tilt_data)

    champ_breakdown = _compute_champion_breakdown(split_matches) if split_matches else None
    role_breakdown = _compute_role_breakdown(split_matches) if split_matches else None

    # Derive champion/role lists from breakdown (consistent with match history)
    champs: list[tuple[str, float]] = [
        (name, float(entry.games)) for name, entry in (champ_breakdown or {}).items()
    ][:10]
    roles: list[tuple[str, float]] = [
        (name, float(entry.games)) for name, entry in (role_breakdown or {}).items()
    ]

    api_html = (
        _stats_table(stats, champs, roles, champ_breakdown, role_breakdown, split_label)
        if stats
        else "<p class='warning'>No verified API stats yet (pipeline still processing).</p>"
    )
    history_html = _match_history_section(puuid, region, riot_id)
    safe_name = html.escape(f"{game_name}#{tag_line}")
    heading = f"Stats for {safe_name}{priority_html}"

    # 7-day sparkline from recent match data
    sparkline_html = _sparkline_html(split_matches[:_BREAKDOWN_MATCH_COUNT])

    # Recently Played With — reuse match IDs already loaded in pipeline
    recent_match_ids = [mid for mid, _ in (recent_id_pairs or [])]
    recently_played_html = await _recently_played_html(r, puuid, recent_match_ids)

    # Two-column layout: sidebar (profile+rank+champions) | main (tilt+history)
    sidebar_html = (
        '<div class="stats-sidebar">'
        + profile_html
        + playstyle_html
        + sparkline_html
        + rank_html
        + rank_hist_html
        + api_html
        + recently_played_html
        + "</div>"
    )
    insight_html = _ai_insight_html(stats, champs, roles)
    main_html = '<div class="stats-main">' + tilt_html + insight_html + history_html + "</div>"
    layout_html = '<div class="stats-layout">' + sidebar_html + main_html + "</div>"
    tabbed_html = _profile_tabs_html(layout_html) + _profile_tab_js()

    return HTMLResponse(
        _stats_form(
            heading,
            "success",
            tabbed_html,
            selected_region=region,
            value=riot_id,
        )
    )


# ---------------------------------------------------------------------------
# Route handlers
# ---------------------------------------------------------------------------


@router.get("/stats", response_class=HTMLResponse)
async def show_stats(request: Request) -> HTMLResponse:
    riot_id = request.query_params.get("riot_id", "")
    region = request.query_params.get("region", "na1")
    if region not in _REGIONS_SET:
        safe_region = html.escape(region)
        return HTMLResponse(
            _stats_form(f"Invalid region: {safe_region}", "error", value=riot_id),
            status_code=400,
        )

    if not riot_id:
        return HTMLResponse(_stats_form(selected_region=region))

    if "#" not in riot_id:
        return HTMLResponse(
            _stats_form(
                t("stats_invalid_riot_id"),
                "error",
                selected_region=region,
                value=riot_id,
            )
        )

    game_name, tag_line = riot_id.split("#", 1)
    r = request.app.state.r
    cfg: Config = request.app.state.cfg
    riot: RiotClient = request.app.state.riot

    result = await _resolve_puuid(r, riot, riot_id, game_name, tag_line, region, cfg)
    if isinstance(result, HTMLResponse):
        return result
    puuid = result

    stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")

    if not stats:
        return _not_seeded_response(game_name, tag_line, region)

    return await _build_stats_response(r, puuid, game_name, tag_line, region, riot_id, stats)


@router.post("/player/refresh")
async def player_refresh(request: Request) -> JSONResponse:
    """Re-seed a player: clear cooldowns and enqueue to stream:puuid."""
    body: dict[str, Any] = await request.json()
    riot_id: str = body.get("riot_id", "")
    region: str = body.get("region", "na1")

    if "#" not in riot_id:
        return JSONResponse({"error": "invalid riot_id"}, status_code=400)

    game_name, tag_line = riot_id.split("#", 1)
    r = request.app.state.r
    puuid: str | None = await r.get(name_cache_key(game_name, tag_line))

    if not puuid:
        return JSONResponse({"error": "player not found — search for them first"}, status_code=404)

    await r.hdel(f"player:{puuid}", "seeded_at", "last_crawled_at")
    await set_priority(r, puuid)

    cfg: Config = request.app.state.cfg
    envelope = MessageEnvelope(
        source_stream="stream:puuid",
        type="puuid",
        payload={
            "puuid": puuid,
            "game_name": game_name,
            "tag_line": tag_line,
            "region": region,
        },
        max_attempts=cfg.max_attempts,
        priority=PRIORITY_MANUAL_20,
        correlation_id=str(uuid.uuid4()),
    )
    await publish(r, "stream:puuid", envelope)

    return JSONResponse({"queued": True})


@router.get("/stats/matches", response_class=HTMLResponse)
async def stats_matches(request: Request) -> HTMLResponse:
    """Return a fragment of match history HTML for lazy loading."""
    puuid = request.query_params.get("puuid", "")
    region = request.query_params.get("region", "na1")
    riot_id = request.query_params.get("riot_id", "")
    try:
        page = int(request.query_params.get("page", "0"))
    except ValueError:
        page = 0

    if not puuid:
        return HTMLResponse("<p class='error'>Missing puuid</p>")

    if not _PUUID_RE.match(puuid):
        return HTMLResponse("<p class='error'>Invalid PUUID format</p>", status_code=400)

    r = request.app.state.r
    halted = await is_system_halted(r)
    halt_html = _HALT_BANNER if halted else ""
    start = page * _MATCH_PAGE_SIZE
    stop = start + _MATCH_PAGE_SIZE  # fetch one extra to detect more pages
    # Sorted set score = game_start ms; ZREVRANGEBYSCORE to get newest first
    raw_pairs: list[tuple[str, float]] = await r.zrevrange(
        f"player:matches:{puuid}", start, stop, withscores=True
    )
    has_more = len(raw_pairs) > _MATCH_PAGE_SIZE
    raw_pairs = raw_pairs[:_MATCH_PAGE_SIZE]

    # Batch all HGETALL calls into a single pipeline round-trip
    results: list[tuple[str, dict[str, str], dict[str, str]]] = []
    if raw_pairs:
        async with r.pipeline(transaction=False) as pipe:
            for match_id, _ in raw_pairs:
                pipe.hgetall(f"match:{match_id}")
                pipe.hgetall(f"participant:{match_id}:{puuid}")
            pipe_results: list[dict[str, str]] = await pipe.execute()
        for i, (match_id, _) in enumerate(raw_pairs):
            match_data: dict[str, str] = pipe_results[i * 2]
            participant_data: dict[str, str] = pipe_results[i * 2 + 1]
            results.append((match_id, match_data, participant_data))

    lang = _current_lang.get()
    version, champ_name_map = await asyncio.gather(
        _get_ddragon_version(r),
        get_champion_name_map(r, lang),
    )
    return HTMLResponse(
        halt_html
        + _match_history_html(
            results, puuid, region, riot_id, page, has_more, version, name_map=champ_name_map
        )
    )


async def _render_match_detail(
    r: Any,
    match_id: str,
    puuid: str,
    cfg: Config,
) -> str:
    """Render match detail HTML (extracted for cache-or-render pattern)."""
    participant_puuids: set[str] = await r.smembers(f"match:participants:{match_id}")
    if not participant_puuids:
        return "<p class='warning'>Match details not available</p>"

    sorted_puuids = sorted(participant_puuids)
    async with r.pipeline(transaction=False) as pipe:
        for p in sorted_puuids:
            pipe.hgetall(f"participant:{match_id}:{p}")
            pipe.hgetall(f"player:{p}")
            pipe.get(f"build:{match_id}:{p}")
            pipe.get(f"skills:{match_id}:{p}")
        pipe.hgetall(f"match:{match_id}")
        pipe_results = await pipe.execute()

    # Last pipeline result is match:{match_id}
    match_data: dict[str, str] = pipe_results[-1]
    participant_pipe_results = pipe_results[:-1]

    lang = _current_lang.get()
    version, spell_map, runes_data, champ_name_map = await asyncio.gather(
        _get_ddragon_version(r),
        _get_summoner_spell_map(r),
        _get_runes_data(r),
        get_champion_name_map(r, lang),
    )

    blue_team, red_team, skill_orders, max_damage = _group_participants(
        sorted_puuids, participant_pipe_results
    )

    blue_html = "".join(
        _render_detail_player(p, part, player, puuid, max_damage, version, name_map=champ_name_map)
        for p, part, player, _build in blue_team
    )
    red_html = "".join(
        _render_detail_player(p, part, player, puuid, max_damage, version, name_map=champ_name_map)
        for p, part, player, _build in red_team
    )

    has_timeline = cfg.fetch_timeline

    build_content = _build_tab_html(
        blue_team,
        red_team,
        version,
        has_timeline,
        puuid,
        spell_map,
        runes_data,
        skill_orders,
    )

    overview_html = (
        f'<div class="match-detail__team">'
        f'<div class="match-detail__team-label match-detail__team-label--blue">'
        f"{t('blue_team')}</div>"
        f"{blue_html}</div>"
        f'<div class="match-detail__team">'
        f'<div class="match-detail__team-label match-detail__team-label--red">'
        f"{t('red_team')}</div>"
        f"{red_html}</div>"
    )

    # --- Team Analysis tab (match_data already loaded in pipeline above) ---
    blue_parts = [part for _p, part, _pl, _b in blue_team]
    red_parts = [part for _p, part, _pl, _b in red_team]
    team_tab_html = _team_analysis_html(blue_parts, red_parts, match_data)

    # --- AI Score tab ---
    all_participants = _build_participant_list(blue_team, red_team)
    ai_scores = _compute_ai_score(all_participants, match_data)
    ai_tab_html = _ai_score_tab_html(ai_scores, puuid, version)

    # --- Timeline tab: gold chart + kill timeline + minimap ---
    # Build puuid->participant map from already-loaded data to avoid redundant reads
    participant_map: dict[str, dict[str, str]] = {
        p: part for p, part, _pl, _b in blue_team + red_team
    }
    timeline_tab_html = ""
    if has_timeline:
        timeline_tab_html = await _build_timeline_tab(
            r, match_id, sorted_puuids, puuid, version, participant_map
        )

    tabbed = _tabbed_match_detail(
        overview_html=overview_html,
        build_html=build_content,
        team_html=team_tab_html,
        ai_html=ai_tab_html,
        timeline_html=timeline_tab_html,
        has_timeline=has_timeline,
    )
    return tabbed + _tab_js()


@router.get("/stats/match-detail", response_class=HTMLResponse)
async def match_detail(request: Request) -> HTMLResponse:
    """Return expanded match detail HTML showing all participants.

    Fragment caching: results are cached in-memory with a 6h TTL.
    ``?nocache=1`` skips both cache read AND write.
    Only caches when timeline data is present (avoids caching placeholder state).
    """
    match_id = request.query_params.get("match_id", "")
    puuid = request.query_params.get("puuid", "")
    nocache = request.query_params.get("nocache", "") == "1"
    if not match_id:
        return HTMLResponse("<p class='error'>Missing match_id</p>", status_code=400)
    if not _MATCH_ID_RE.match(match_id):
        return HTMLResponse("<p class='error'>Invalid match ID format</p>", status_code=400)
    if puuid and not _PUUID_RE.match(puuid):
        return HTMLResponse("<p class='error'>Invalid PUUID format</p>", status_code=400)

    r = request.app.state.r
    cfg: Config = request.app.state.cfg

    cache_key = f"ui:match-detail:{_CACHE_VERSION}:{match_id}:{puuid}"

    # Check in-memory cache first (unless nocache)
    if not nocache:
        cached = _fragment_get(cache_key)
        if cached:
            return HTMLResponse(cached)

    result_html = await _render_match_detail(r, match_id, puuid, cfg)

    # Cache the result in memory if timeline data is present and nocache not set
    if not nocache and _has_timeline_data(result_html):
        _fragment_put(cache_key, result_html)

    return HTMLResponse(result_html)
