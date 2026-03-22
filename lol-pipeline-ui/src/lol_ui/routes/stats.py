"""Stats routes — player lookup, match history, match detail."""

from __future__ import annotations

import contextlib
import html
import json
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from lol_pipeline.config import Config
from lol_pipeline.constants import PLAYER_DATA_TTL_SECONDS
from lol_pipeline.helpers import name_cache_key
from lol_pipeline.log import get_logger
from lol_pipeline.models import MessageEnvelope
from lol_pipeline.priority import PRIORITY_MANUAL_20, set_priority
from lol_pipeline.rate_limiter import wait_for_token
from lol_pipeline.resolve import CACHE_TTL_S
from lol_pipeline.riot_api import (
    AuthError,
    NotFoundError,
    RateLimitError,
    RiotClient,
    ServerError,
)
from lol_pipeline.streams import publish

from lol_ui.constants import (
    _AUTOSEED_COOLDOWN_S,
    _BREAKDOWN_MATCH_COUNT,
    _HALT_BANNER,
    _MATCH_ID_RE,
    _MATCH_PAGE_SIZE,
    _NAME_CACHE_INDEX,
    _NAME_CACHE_MAX,
    _PUUID_RE,
    _REGIONS_SET,
    _SPLIT_MATCH_LIMIT,
    _STREAM_PUUID,
    _TILT_RECENT_COUNT,
)
from lol_ui.ddragon import _get_ddragon_version
from lol_ui.match_detail import _render_build_section, _render_detail_player
from lol_ui.match_history import _match_history_html, _match_history_section
from lol_ui.playstyle import _playstyle_pills_html, _playstyle_tags
from lol_ui.rank import _profile_header_html, _rank_card_html, _rank_history_html
from lol_ui.rendering import _badge, _stats_form
from lol_ui.stats_helpers import (
    _compute_champion_breakdown,
    _compute_role_breakdown,
    _current_split,
    _stats_table,
)
from lol_ui.tilt import _streak_indicator, _tilt_banner_html

_log = get_logger("ui")

router = APIRouter()


# ---------------------------------------------------------------------------
# Helpers (only used by stats routes)
# ---------------------------------------------------------------------------


async def _resolve_and_cache_puuid(
    r: Any,
    riot: RiotClient,
    riot_id: str,
    game_name: str,
    tag_line: str,
    region: str,
    cfg: Config,
) -> str | HTMLResponse:
    """Look up PUUID from cache or Riot API.

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
                "Player not found. Check the spelling of the Riot ID.",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    except RateLimitError:
        return HTMLResponse(
            _stats_form(
                "Rate limited. Try again in a few seconds.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    except AuthError:
        return HTMLResponse(
            _stats_form(
                "API key invalid or expired. Update <code>RIOT_API_KEY</code> in"
                " <code>.env</code> and restart, then run"
                " <code>just admin system-resume</code>.",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    except ServerError:
        return HTMLResponse(
            _stats_form(
                "Riot servers temporarily unavailable. Try again later.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    puuid: str = account["puuid"]
    await r.set(cache_key, puuid, ex=CACHE_TTL_S)
    now_ts = datetime.now(tz=UTC).timestamp()
    cache_size = int(await r.zcard(_NAME_CACHE_INDEX))
    if cache_size >= _NAME_CACHE_MAX:
        await r.zremrangebyrank(_NAME_CACHE_INDEX, 0, 0)
    await r.zadd(_NAME_CACHE_INDEX, {cache_key: now_ts})
    return puuid


async def _auto_seed_player(
    r: Any,
    puuid: str,
    game_name: str,
    tag_line: str,
    region: str,
    cfg: Config,
) -> HTMLResponse:
    """Auto-seed a player if not yet seeded, returning an appropriate status page."""
    riot_id = f"{game_name}#{tag_line}"
    safe_id = html.escape(riot_id)
    cooldown_key = f"autoseed:cooldown:{puuid}"
    async with r.pipeline(transaction=False) as pipe:
        pipe.get("system:halted")
        pipe.get(cooldown_key)
        pipe.hget(f"player:{puuid}", "seeded_at")
        halted, cooldown, existing_seeded = await pipe.execute()
    if halted:
        return HTMLResponse(
            _stats_form(
                f"System halted. No stats yet for {safe_id}.",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )
    if cooldown:
        return HTMLResponse(
            _stats_form(
                f"{safe_id} was seeded recently — pipeline processing. Check back soon.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    if existing_seeded:
        return HTMLResponse(
            _stats_form(
                f"{safe_id} was seeded recently — pipeline processing. Check back soon.",
                "warning",
                selected_region=region,
                value=riot_id,
            )
        )
    envelope = MessageEnvelope(
        source_stream=_STREAM_PUUID,
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
    # Set priority before publishing so clear_priority() by downstream
    # services cannot race against a not-yet-set priority key.
    await set_priority(r, puuid)
    now_ts = time.time()
    await r.zadd("players:all", {puuid: now_ts})
    await r.zremrangebyrank("players:all", 0, -(cfg.players_all_max + 1))
    await publish(r, _STREAM_PUUID, envelope)
    now_iso = datetime.now(tz=UTC).isoformat()
    await r.hset(
        f"player:{puuid}",
        mapping={
            "game_name": game_name,
            "tag_line": tag_line,
            "region": region,
            "seeded_at": now_iso,
        },
    )
    await r.expire(f"player:{puuid}", PLAYER_DATA_TTL_SECONDS)
    await r.set(cooldown_key, "1", ex=_AUTOSEED_COOLDOWN_S)
    _log.info("auto-seeded via stats lookup", extra={"puuid": puuid})
    return HTMLResponse(
        _stats_form(
            f"&#10003; Auto-seeded {safe_id} — pipeline processing. Refresh in a minute.",
            "success",
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


async def _load_tilt_data(r: Any, puuid: str) -> dict[str, object]:
    """Load recent match data and compute tilt/streak indicator."""
    matches = await _load_recent_matches(r, puuid, _TILT_RECENT_COUNT)
    return _streak_indicator(matches)


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
        priority_key, rank, rank_hist = await pipe.execute()

    rank = rank or {}
    rank_hist = rank_hist or []
    priority_html = f" {_badge('info', 'Priority')}" if priority_key else ""
    profile_html = _profile_header_html(game_name, tag_line, rank)
    rank_html = _rank_card_html(rank)
    rank_hist_html = _rank_history_html(rank_hist)
    playstyle_html = _playstyle_pills_html(_playstyle_tags(stats))

    # Load current-split matches for champion/role breakdowns
    split_label, split_start_ms = _current_split()
    split_matches = await _load_recent_matches(
        r,
        puuid,
        count=_SPLIT_MATCH_LIMIT,
        since_ms=split_start_ms,
    )
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
    return HTMLResponse(
        _stats_form(
            heading,
            "success",
            (
                profile_html
                + playstyle_html
                + rank_html
                + rank_hist_html
                + tilt_html
                + api_html
                + history_html
            ),
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
                "Invalid Riot ID — expected GameName#TagLine",
                "error",
                selected_region=region,
                value=riot_id,
            )
        )

    game_name, tag_line = riot_id.split("#", 1)
    r = request.app.state.r
    cfg: Config = request.app.state.cfg
    riot: RiotClient = request.app.state.riot

    result = await _resolve_and_cache_puuid(r, riot, riot_id, game_name, tag_line, region, cfg)
    if isinstance(result, HTMLResponse):
        return result
    puuid = result

    stats: dict[str, str] = await r.hgetall(f"player:stats:{puuid}")

    if not stats:
        return await _auto_seed_player(r, puuid, game_name, tag_line, region, cfg)

    return await _build_stats_response(r, puuid, game_name, tag_line, region, riot_id, stats)


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
    halted = await r.get("system:halted")
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

    version = await _get_ddragon_version(r)
    return HTMLResponse(
        halt_html + _match_history_html(results, puuid, region, riot_id, page, has_more, version)
    )


@router.get("/stats/match-detail", response_class=HTMLResponse)
async def match_detail(request: Request) -> HTMLResponse:  # noqa: C901
    """Return expanded match detail HTML showing all participants."""
    match_id = request.query_params.get("match_id", "")
    puuid = request.query_params.get("puuid", "")
    if not match_id:
        return HTMLResponse("<p class='error'>Missing match_id</p>", status_code=400)
    if not _MATCH_ID_RE.match(match_id):
        return HTMLResponse("<p class='error'>Invalid match ID format</p>", status_code=400)
    if puuid and not _PUUID_RE.match(puuid):
        return HTMLResponse("<p class='error'>Invalid PUUID format</p>", status_code=400)

    r = request.app.state.r
    # Get all participants in this match
    participant_puuids: set[str] = await r.smembers(f"match:participants:{match_id}")
    if not participant_puuids:
        return HTMLResponse("<p class='warning'>Match details not available</p>")

    # Batch fetch: participant data + player names + build orders
    sorted_puuids = sorted(participant_puuids)
    async with r.pipeline(transaction=False) as pipe:
        for p in sorted_puuids:
            pipe.hgetall(f"participant:{match_id}:{p}")
            pipe.hgetall(f"player:{p}")
            pipe.get(f"build:{match_id}:{p}")
        pipe_results = await pipe.execute()

    version = await _get_ddragon_version(r)

    # Group by team
    blue_team: list[tuple[str, dict[str, str], dict[str, str], list[str]]] = []
    red_team: list[tuple[str, dict[str, str], dict[str, str], list[str]]] = []
    max_damage = 1
    for i, p in enumerate(sorted_puuids):
        participant_data: dict[str, str] = pipe_results[i * 3]
        player_data: dict[str, str] = pipe_results[i * 3 + 1]
        build_raw: str | None = pipe_results[i * 3 + 2]
        if not participant_data:
            continue
        try:
            dmg = int(participant_data.get("total_damage_dealt_to_champions", "0"))
        except ValueError:
            dmg = 0
        max_damage = max(max_damage, dmg)
        # Parse build order
        build_order: list[str] = []
        if build_raw:
            with contextlib.suppress(json.JSONDecodeError, TypeError):
                build_order = [str(x) for x in json.loads(build_raw)]
        team_id = participant_data.get("team_id", "")
        entry = (p, participant_data, player_data, build_order)
        if team_id == "200":
            red_team.append(entry)
        else:
            blue_team.append(entry)

    blue_html = "".join(
        _render_detail_player(p, part, player, puuid, max_damage, version)
        for p, part, player, _build in blue_team
    )
    red_html = "".join(
        _render_detail_player(p, part, player, puuid, max_damage, version)
        for p, part, player, _build in red_team
    )
    builds_html = _render_build_section(
        blue_team,
        red_team,
        version,
    )

    return HTMLResponse(
        f'<div class="match-detail__team">'
        f'<div class="match-detail__team-label match-detail__team-label--blue">'
        f"Blue Team</div>"
        f"{blue_html}</div>"
        f'<div class="match-detail__team">'
        f'<div class="match-detail__team-label match-detail__team-label--red">'
        f"Red Team</div>"
        f"{red_html}</div>"
        f"{builds_html}"
    )
