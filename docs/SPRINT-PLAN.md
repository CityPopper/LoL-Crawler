# Sprint Plan — Phase 24: Match Intelligence UI

## Overview

Six-sprint plan (Sprint 0-5) to add OP.GG-style features to the pipeline UI.
All features are server-rendered (FastAPI + inline HTML/CSS/JS). No SPA.
All user-facing strings go through a `t()` lookup for future localization.
`t()` auto-escapes output by default (`t_raw()` for intentional HTML).

**Review consensus:** 37 agent reviews across 19 specialties. Findings integrated below.

---

## Cross-Cutting: Localization-Ready Architecture

**Every sprint** must follow this pattern for UI text:

**Target languages:** en, zh-CN

```python
# strings.py (extracted module — see Sprint 0)
_STRINGS = {
    "en": {
        "win": "Win",
        "loss": "Loss",
        "ai_score": "AI Score",
        "team_analysis": "Team Analysis",
        "build": "Build",
        "overview": "Overview",
        "timeline": "Timeline",
        "no_timeline_data": "Timeline data unavailable for this match.",
        "no_build_data": "Build data unavailable for this match.",
        "not_enough_games": "Not enough games for an insight yet.",
        "grade_s": "Exceptional",
        "grade_a": "Great",
        "grade_b": "Good",
        "grade_c": "Below Average",
        "grade_d": "Poor",
        # ... all UI-facing strings
    },
    "zh-CN": {
        "win": "[CN] Win",
        "loss": "[CN] Loss",
        # ... all keys duplicated with [CN] prefix as placeholders
    },
}
_LANG = "en"  # TODO: read from cookie/Accept-Language header

def t(key: str) -> str:
    """Return localized string, HTML-escaped by default."""
    import html as _html
    return _html.escape(_STRINGS.get(_LANG, _STRINGS["en"]).get(key, key))

def t_raw(key: str) -> str:
    """Return localized string without escaping (for intentional HTML)."""
    return _STRINGS.get(_LANG, _STRINGS["en"]).get(key, key)
```

**Rules:**
- No hardcoded user-facing strings in HTML generators — always `t("key")`
- Keys are snake_case English identifiers
- `t()` auto-escapes (safe for HTML interpolation by default)
- `t_raw()` for strings containing intentional HTML entities
- Apply `t()` only to NEW strings in Sprints 1-5; do NOT migrate all existing strings (avoids churn per "quantifiable improvements only" directive)
- Error/empty states must NOT expose internal config (no env var names in user-facing text)
- Both language dicts must have the same keys. zh-CN values start as `[CN] English text` placeholders, replaced with real translations later
- `_STRINGS` will eventually be extractable to JSON/YAML files per language

---

## Cross-Cutting: Visual Theme Updates

Apply in Sprint 0 alongside the module split:

```css
/* Deeper background — maintain surface separation ratio */
--color-bg: #141418;         /* was #1c1c1e */
--color-surface: #262636;    /* was #31313c — adjusted per design review */
--color-surface2: #2e2e42;   /* was #3d3d48 — adjusted */
--color-border: #3a3a50;     /* was #3f3f4a — adjusted */

/* New color tokens */
--color-gold: #f4c874;
--color-tier-s: #e89240;
--color-rank-purple: #9e6cd9;
--color-rank-teal: #3cbec0;
--color-success: #2daf6f;    /* was #2ecc40 (too neon) */

/* Damage type colors (LoL convention) */
--color-dmg-physical: #e89240;  /* orange */
--color-dmg-magic: #5383e8;     /* blue */
--color-dmg-true: #e8e8e8;      /* white */

/* Tabular numbers for stat columns */
.stat-num { font-variant-numeric: tabular-nums; }

/* Subtle row hover */
.match-row:hover { filter: brightness(1.08); }

/* Chart tokens */
--chart-stroke-width: 2;
--icon-champ-xs: 20px;  /* timeline events */

/* Per-player chart colors (5 per team) */
--chart-b0: #5383e8; --chart-b1: #3cbec0; --chart-b2: #2daf6f;
--chart-b3: #9e6cd9; --chart-b4: #f4c874;
--chart-r0: #e84057; --chart-r1: #e89240; --chart-r2: #ffdc00;
--chart-r3: #ff6b6b; --chart-r4: #c0a060;

/* Unified grade badges (used by BOTH AI Score and PBI tiers) */
.grade--S { background: linear-gradient(135deg, #e89240, #f4c874); color: #1c1c1e; }
.grade--A { background: #5383e8; color: #1c1c1e; }
.grade--B { background: #2daf6f; color: #1c1c1e; }
.grade--C { background: #7b7b8d; color: #fff; }
.grade--D { background: #3f3f4a; color: #e8e8e8; }
/* All grade badges meet WCAG AA 4.5:1 contrast */
```

**Also fix:** Replace 3 hardcoded `#2ecc40` values in main.py with `var(--color-success)`.

---

## Sprint 0 — Prerequisites (Before Any Feature Work)

**Goal:** Fix blockers and prepare the codebase for 5 sprints of changes.

| ID | Task | Size | Service |
|----|------|------|---------|
| T0-1 | Restore `match:participants` SADD in parser | S | parser |
| T0-2 | Split main.py into module package | M | ui |
| T0-3 | Add `t()` + `_STRINGS` (new strings only) | S | ui |
| T0-4 | Apply theme CSS changes | S | ui |
| T0-5 | Evolve pact contracts for new parser fields | S | parser/common |
| T0-6 | Update 04-storage.md with undocumented fields | S | docs |
| T0-7 | Add Phase 24 TODO to CLAUDE.md | XS | docs |

### T0-1: Restore `match:participants` SADD

**CRITICAL — Blocks ALL match detail features.**

The `SADD match:participants:{match_id}` was removed in B14 (v1.1.0). The UI match detail handler at line 2741 reads this set and gets empty for all post-B14 matches, returning "Match details not available."

Add `pipe.sadd(f"match:participants:{match_id}", puuid)` + `pipe.expire(...)` back into `_queue_participant()` in the parser, using `match_data_ttl_seconds` (7d) TTL. One-line change inside the existing pipeline — zero extra RTTs.

### T0-2: Split main.py into One-Function-Per-File Package

**CRITICAL — main.py is ~3,800 lines. AI agents can't efficiently work with monoliths.**

Split into the structure shown in the Implementation Rules section above. Key principles:
- Every function gets its own `.py` file
- Unit tests colocated: `foo.py` → `test_foo.py` same directory
- `tests/regression/` for bug-fix red/green tests (separate, kept forever)
- `tests/contract/` for consumer-driven contract tests (no consumer = no test)
- Existing `tests/unit/test_main.py` (~6,600 lines) split to match new source modules
- Shared test fixtures extracted to `conftest.py` files at appropriate levels
- pytest `testpaths` updated in `pyproject.toml` to discover colocated tests
- `app` object stays in `main.py` permanently (keeps `__main__.py` working)
- Migrate incrementally: use `__init__.py` re-exports as shim during transition

**Required config changes for colocated tests:**
```toml
# pyproject.toml
[tool.pytest.ini_options]
testpaths = ["src/lol_ui", "tests"]  # discovers colocated + regression

[tool.ruff.lint.per-file-ignores]
"tests/**" = ["S101", "ANN", "SIM"]
"src/**/test_*.py" = ["S101", "ANN", "SIM"]  # colocated tests

[[tool.mypy.overrides]]
module = ["lol_ui.test_*", "lol_ui.*.test_*"]
disallow_untyped_defs = false
```

**Dockerfile: strip test files from production image:**
```dockerfile
COPY lol-pipeline-${SERVICE_NAME}/src/ ./src/
RUN find ./src -name "test_*.py" -delete && find ./src -name "conftest.py" -delete
```

**CI changes (apply with T0-2):**
- `ci.yml` test job: add `tests/regression` to pytest path
- `ci.yml` contract job: delete or stub until pact broker is ready
- `justfile` test recipe: discover `tests/regression/` alongside `tests/unit/`

**Size: L** (not M — moving ~3,800 lines source + ~6,600 lines tests is significant)

**Benefits:** AI loads only the relevant ~100-200 line file. Merge conflicts eliminated. Lint thresholds respected. Source + test always visible together.

### T0-3-T0-7: Foundation Tasks

T0-3: Add `t()` function + initial string table for new strings only. Do NOT migrate existing strings.

T0-4: Apply all CSS theme changes from cross-cutting section. Fix hardcoded `#2ecc40`. Unify grade badge colors with PBI tier colors.

T0-5: Keep existing file-based consumer-driven pacts (a pact broker is overkill for a single-developer monorepo). Keep `contracts/schemas/*.json` as canonical validation schemas. Evolve contracts incrementally — when Sprint 1 adds new parser fields, update relevant pacts. No infrastructure changes needed.

T0-6: Update `04-storage.md` to document all undocumented participant fields (`physical_damage`, `magic_damage`, etc.) and add a DDragon cache section.

T0-7: Add Phase 24 TODO block to CLAUDE.md per directive.

---

## Sprint 1 — Foundation: Extended Data Extraction

**Goal:** Extract and store all match data needed by Sprints 2-4.

| ID | Task | Size | Service | New Storage |
|----|------|------|---------|-------------|
| T1-1 | Extract gold-over-time from timeline frames | S | parser | `gold_timeline:{match_id}:{puuid}` (7d TTL) |
| T1-2 | Extract team objective totals | S | parser | Fields on `match:{match_id}` hash |
| T1-3 | Store full rune sub-selections | S | parser | Fields on `participant:` hash |
| T1-4 | Extract kill events with positions + assists | M | parser | `kill_events:{match_id}` (7d TTL, cap 200) |

### T1-1: Gold Timeline

Parse `participantFrames[id].totalGold` from each 1-minute frame. Map participant IDs to PUUIDs using existing `pid_to_puuid` pattern. Cap at 120 frames.

**Acceptance criteria:**
- `gold_timeline:{match_id}:{puuid}` written when `FETCH_TIMELINE=true`
- JSON integer array, values are `totalGold` (not `currentGold`)
- Capped at 120 frames
- If non-monotonic sequence detected, log warning (do not reject)
- Writes batched into existing `_store_timeline_data` pipeline (zero extra RTTs)

### T1-2: Team Objectives

Parse `info.teams[].objectives`. Map via explicit `teamId` comparison (100=blue, 200=red), not array index.

**New fields on `match:{match_id}`:**
`team_blue_dragons`, `team_blue_barons`, `team_blue_towers`, `team_blue_inhibitors`, `team_blue_heralds`, `team_blue_first_blood`, and `team_red_*`.

### T1-3: Full Rune Selections

Expand `_extract_perks()` to return all perk IDs. Extract available elements; do not assert exact array lengths.

**New fields on `participant:{match_id}:{puuid}`:**
`perk_primary_selections` (JSON array), `perk_sub_selections` (JSON array), `perk_stat_shards` (JSON array).

- Empty arrays stored as `"[]"`, never omitted
- Parser provider test updated to assert new fields exist in participant hash
- No stream message schema changes needed

### T1-4: Kill Events with Assists

Extract `CHAMPION_KILL` events. **Include assist participant IDs.**

**Format:** `[{"t": ms, "killer": "ChampName", "victim": "ChampName", "assists": ["ChampName", ...], "x": int, "y": int}, ...]`

Denormalize champion names directly into events (eliminates PID-to-PUUID join at render time).

**Acceptance criteria:**
- Capped at 200 events, sorted by timestamp ascending
- Champion names resolved from `participantId` → participant's `championName`
- Empty list stored as `"[]"`
- Writes batched into existing timeline pipeline

### Sprint 1 Doc Updates
- Update `04-storage.md` with all new keys
- Update `02-services.md` parser section with new fields

---

## Sprint 2 — Core Visualizations: Tabs + Charts + Layout

**Goal:** Transform flat match detail into tabbed view; add damage charts, donut, and two-column layout.

**Dependencies:** T1-2 (for T2-3). T0-2 module split prerequisite.

| ID | Task | Size | Service | JS? |
|----|------|------|---------|-----|
| T2-1 | Tabbed match detail + responsive tab strip | M | ui | ~20 lines vanilla |
| T2-2 | Damage breakdown bars (physical/magic/true) | S | ui | No |
| T2-3 | Team Analysis tab: stat comparison bars | M | ui | No |
| T2-4 | Win rate SVG donut on player profile | S | ui | No |
| T2-5 | Sticky two-column layout on stats page | S | ui | No |

### T2-1: Tabbed Match Detail

Tab strip: **Overview | Build | Team Analysis | AI Score | Timeline**

(Reordered per UX review: Build before AI Score since it's higher-frequency. "Etc" renamed to "Timeline".)

**Implementation:** Vanilla JS `classList.toggle` (NOT CSS radio hack — incompatible with AJAX-loaded fragments). Use event delegation matching existing `toggleMatchDetail` pattern. Call `initMatchTabs(container)` after `detail.innerHTML = h`.

**Tab visibility rules:**
- If `FETCH_TIMELINE=false`: suppress Timeline tab entirely (server-side). Build tab shows items from participant hash; skill order shows "Skill data requires timeline."
- All tabs always show Overview, Build, Team Analysis, AI Score.

**Responsive:** Tab strip gets `overflow-x: auto; -webkit-overflow-scrolling: touch; white-space: nowrap` from day one.

### T2-2: Damage Bars

Segmented bar: physical (orange `--color-dmg-physical`) / magic (blue `--color-dmg-magic`) / true (white `--color-dmg-true`).

CSS `display:flex` container with `min-width:0` on segments. Bar width: `min(100%, 200px)` (not the current 60px).

### T2-3: Team Analysis Tab

6 stat rows: Gold, Damage, Kills, CS, Vision, Objectives. Each row: blue value | dual-fill percentage bar | red value.

Bar: single `<div>` with `background: linear-gradient(to right, var(--color-win) var(--blue-pct), var(--color-loss) var(--blue-pct))`. Set `--blue-pct` inline.

Objectives row hidden gracefully if T1-2 fields absent.

### T2-4: Win Rate Donut

Inline SVG. `r=40, circumference=251.3`. Add `transform="rotate(-90 50 50)"` for 12-o'clock start. `stroke-linecap="round"`.

**SVG generation:** Use string concatenation (NOT f-strings) for any SVG containing literal `{}` braces. Extract coordinate math as pure functions, test math not markup.

### T2-5: Sticky Two-Column Layout

CSS grid: `grid-template-columns: 300px 1fr` (widened from 280px per UX review). Left column: rank card + top 5 champions. Use `max-height: 100vh; overflow-y: auto; align-self: start`.

**Mobile:** `@media (max-width: 767px) { grid-template-columns: 1fr; .stats-sidebar { position: static; } }` — shipped in this sprint, not deferred.

---

## Sprint 3 — Build Tab: Items, Runes, Skills, Spells

**Goal:** Complete Build tab with item order, rune page, skill grid, summoner spells.

**Dependencies:** T1-3, T2-1.

| ID | Task | Size | Service | New Cache |
|----|------|------|---------|-----------|
| T3-1 | Item purchase order display | S | ui | None |
| T3-2 | Skill order grid (Q/W/E/R x 18) | S | ui | None |
| T3-3 | Rune page with DDragon icons | M | ui | `ddragon:runes` (24h) |
| T3-4 | Summoner spell icons | S | ui | `ddragon:summoners` (24h) |
| T3-5 | Unified DDragon cache helper | S | ui | None |

### T3-2: Skill Order Grid

Wrap in `.table-scroll` container from day one (18 columns = 504px min, overflows mobile).

Level numbers as column headers. R-unlock columns (6/11/16) highlighted with `background: rgba(232,64,87,0.08)`.

### T3-3: Rune Page

`runesReforged.json` does NOT include stat shards. Stat shards render as text labels ("+9 Adaptive Force") with a hardcoded ID-to-label map. Old matches without T1-3 data degrade to keystone-only display.

### T3-5: DDragon Cache Helper (moved from Sprint 5)

Extract `_get_ddragon_json(r, key, url, ttl)` helper. Validate DDragon version string format (`^\d+\.\d+\.\d+$`). Cap response size at 5MB. Used by version, runes, summoners caches.

### Sprint 3 Doc Updates
- Update `04-storage.md` with DDragon cache keys

---

## Sprint 4 — Advanced: Gold Graph, AI Score, Timeline, Insights

**Goal:** Ship headline features: gold chart, AI Score, kill timeline.

**Dependencies:** T1-1 (gold), T1-4 (kills), T2-1 (tabs).

| ID | Task | Size | Service | JS? |
|----|------|------|---------|-----|
| T4-1 | Gold-over-time SVG line chart (Timeline tab) | L | ui | Optional hover (~30 lines) |
| T4-2 | AI Score computation + tab | L | ui | No |
| T4-3 | Kill event timeline list (Timeline tab) | M | ui | No |
| T4-4 | Rule-based AI Insight blurb | M | ui | No |

### T4-1: Gold Chart

**10 polylines with per-player colors** (5 blue shades, 5 red shades from `--chart-b0..b4`, `--chart-r0..r4`). Focused player line: `stroke-width: 2.5`, full opacity. Others: `stroke-width: 1.5`, 60% opacity.

**SVG requirements:**
- `viewBox="0 0 600 300"` + `width="100%"` + `preserveAspectRatio="xMidYMid meet"` (responsive)
- `shape-rendering="geometricPrecision"` + `stroke-linecap="round"` (anti-aliasing)
- Y normalization: global max across ALL 10 players (not per-player)
- X-axis labels every 5 minutes (not every minute — avoids label collision)
- 20px top padding, 50px left padding for axis labels
- Use string concat, not f-strings, for SVG generation
- Legend panel: champion name + color swatch + final gold value

### T4-2: AI Score

**Formula (7 components, 7 stat bars):**

| Stat | Weight |
|------|--------|
| KDA | 25% |
| Damage share (team %) | 20% |
| Gold share (team %) | 15% |
| CS + Neutral (per min) | 15% |
| Vision Score | 10% |
| Kill Participation | 10% |
| Objective contribution | 5% |

**Normalization:** Min-max across 10 participants per stat. When `max == min`, that component = 50 (midpoint). Score scaled 0-10.

**Display:**
- Grade: S (>=8), A (>=6.5), B (>=5), C (>=3.5), D (<3.5)
- Grade badges have `title` tooltips (S="Exceptional", A="Great", etc.)
- AI Score tab shows 10 players ranked by score, grade badges only
- Sub-component breakdown (7 stat bars) shown ONLY for focused player (avoids 70-element density)
- Grade badge ALSO appears on collapsed match row in Overview

### T4-3: Kill Timeline

Chronological list from `kill_events:{match_id}`. Each row: `[MM:SS] [killer icon] → [victim icon] (+assist icons)`.

Assist icons rendered as champion portraits (data from T1-4's `assists` array). Grouped by minute headers.

### T4-4: AI Insight

Rule-based, observational tone (not prescriptive). All text through `t()`.
- "Vision score below average for this role." (NOT "consider more wards")
- Placement: below tilt banner, above match history

---

## Sprint 5 — Polish: Minimap, Responsive, Caching

**Goal:** Complete the visual picture and harden performance.

**Dependencies:** T4-3 (for minimap).

| ID | Task | Size | Service | JS? |
|----|------|------|---------|-----|
| T5-1 | Minimap kill overlay (static + scrubber) | M | ui | ~15 lines |
| T5-2 | 7-day win rate sparkline bar chart | S | ui | No |
| T5-3 | Recently Played With panel | M | ui | No |
| T5-4 | Responsive layout fixes | S | ui | No |
| T5-5 | Match detail fragment caching | S | ui | No |

### T5-1: Minimap Overlay

Static DDragon `map11.png`. Kill dots as SVG circles (team colored, `r=5`), NOT champion portraits on minimap. Container: `max-width: 300px; width: 100%`.

Coordinate conversion: `svg_x = game_x / 15000 * 100%`, `svg_y = (1 - game_y / 15000) * 100%` (Y inverted).

### T5-3: Recently Played With

**Prerequisite:** T0-1 must have restored `match:participants` SADD.

Scan last 20 matches only (capped). Pipeline all `SMEMBERS` calls in one round-trip (2 RTTs total). Minimum 3 shared games to display. Top 5 co-players.

### T5-5: Fragment Caching

Cache key: `ui:match-detail:v{VERSION}:{match_id}:{puuid}` — version prefix bumped on deploys for cache busting. TTL 6h.

`?nocache=1` skips both cache read AND write (prevents cache poisoning).

Re-parsing a match clears `ui:match-detail:*:{match_id}:*` keys.

### Sprint 5 Doc Updates
- Update `04-storage.md` with cache key

---

## Future Sprint — Full Localization

**Goal:** Complete zh-CN translations and add language switching UI.

**Deferred until after Sprint 5.** Sprint 0 establishes the `t()` / `_STRINGS` infrastructure with en + zh-CN placeholders. This future sprint replaces all `[CN]` placeholders with real translations and adds:

| ID | Task | Size | Service |
|----|------|------|---------|
| TL-1 | Replace all zh-CN placeholder strings with real translations | M | ui |
| TL-2 | Migrate all existing hardcoded English strings to use `t()` | L | ui |
| TL-3 | Language switcher UI (cookie-based, persist preference) | S | ui |
| TL-4 | Extract `_STRINGS` to per-language JSON files | S | ui |
| TL-5 | Add additional languages (fr, ko, pt-BR, de, es, it) as needed | M | ui |

**Prerequisites:** All Sprint 0-5 features complete. All new UI text already using `t()`.

---

## Dependency Graph

```
Sprint 0 (Prerequisites)
  T0-1 restore match:participants ────────► ALL match detail features
  T0-2 split main.py ────────────────────► ALL UI sprints
  T0-3 t() function ─────────────────────► ALL UI strings
  T0-4 theme CSS ─────────────────────────► visual consistency

Sprint 1 (Foundation)
  T1-1 gold timeline ────────────────────► T4-1 gold chart
  T1-2 team objectives ──────────────────► T2-3 team analysis
  T1-3 rune selections ──────────────────► T3-3 rune display
  T1-4 kill events (+assists) ───────────► T4-3 timeline ──► T5-1 minimap

Sprint 2 (Core UI)
  T2-1 tab shell ────────────────────────► all tab content (S3, S4)

Sprint 3 (Build Tab) ── needs T2-1, T1-3
Sprint 4 (Advanced) ─── needs T2-1, T1-1, T1-4
Sprint 5 (Polish) ───── needs T4-3, T0-1
```

---

## Naming Convention

**NEVER use "OP Score" anywhere.** The performance rating is **"AI Score"** in all code, UI text, docs, and string tables.

---

## Implementation Rules

### Code Organization (one function per file)

Every new function gets its own module file. This lets AI agents load only the relevant module.

**Source structure:**
- One function per `.py` file (e.g., `win_rate_donut.py`, `compute_ai_score.py`)
- Shared helpers used by 2+ modules → `_helpers.py` colocated with consumers
- Constants/types shared across a package → `_types.py` or `_constants.py`
- Route handlers grouped by feature in `routes/` subpackage

**Test structure (colocated):**
- Unit tests live **next to** the source file: `foo.py` → `test_foo.py` in the same directory
- AI agents find source + test together instantly — no directory jumping
- Regression tests (red/green bug-fix tests) → `tests/regression/` (separate, kept forever)
- Contract tests → `tests/contract/`, **consumer-driven** (no consumer = no test)

**Example layout after Sprint 0 split:**
```
lol-pipeline-ui/src/lol_ui/
  __init__.py
  __main__.py
  main.py                    # FastAPI app, lifespan, health route only
  css.py                     # _CSS constant
  test_css.py                # CSS tests (colocated)
  strings.py                 # _STRINGS, t(), t_raw()
  test_strings.py
  ddragon.py                 # DDragon caching
  test_ddragon.py
  rendering.py               # _page(), _badge(), _empty_state(), icons
  test_rendering.py
  charts/
    __init__.py
    win_rate_donut.py         # SVG donut generator
    test_win_rate_donut.py
    gold_chart.py             # Gold-over-time polyline chart
    test_gold_chart.py
    minimap.py                # Kill overlay on map
    test_minimap.py
  scoring/
    __init__.py
    ai_score.py               # _compute_ai_score()
    test_ai_score.py
    ai_insight.py              # Rule-based insight blurb
    test_ai_insight.py
  routes/
    __init__.py
    dashboard.py
    test_dashboard.py
    stats.py                   # Stats, match detail, match history
    test_stats.py
    champions.py               # Tier list, detail, matchups
    test_champions.py
    streams.py
    test_streams.py
    dlq.py
    test_dlq.py
    logs.py
    test_logs.py
    players.py
    test_players.py
  tests/
    regression/                # Bug-fix red/green tests (kept forever)
    contract/                  # Consumer-driven contract tests
```

### Other Rules

1. **SVG generation:** Use string concatenation or `"".join()`, NOT f-strings (literal `{}` in SVG `<style>` blocks break f-strings)
2. **Tab switching:** Vanilla JS `classList.toggle` with event delegation (NOT CSS radio hack)
3. **Responsive:** Each component gets basic responsive CSS in its own sprint (tab overflow, SVG viewBox, grid collapse). T5-4 handles remaining polish only.
4. **DDragon fetches:** Server-side only. Client JS never fetches external origins.
5. **`html.escape()`:** All Redis-sourced values escaped before HTML interpolation. `t()` auto-escapes. SVG attributes use numeric types only.
6. **Pipeline batching:** All new parser writes added to existing pipelines (zero extra RTTs).
7. **`_page()` title:** Escape inside `_page()` unconditionally.

---

## Data Requirements Summary

### Zero API Cost:
- Damage bars, team analysis, AI Score, win rate donut, rune display, summoner spells, 7-day win rate, recently played with

### Requires FETCH_TIMELINE=true:
- Gold chart, kill timeline, minimap, skill order
- Doubles API calls per match. Document memory cost in `.env.example`.

### Estimated Additional Redis Memory: ~25-40 MB (within 2GB limit)

---

## Out of Scope

- SPA frontend (REJECTED: OT1-R3)
- Win prediction ML model (REJECTED: OT1-R10)
- LLM-generated insights (REJECTED: OT1-R4)
- Redis TimeSeries (REJECTED: OT1-R1)
- Smurf detection (REJECTED: OT1-R11)
- Elasticsearch (REJECTED: OT1-R2)
- LP change per match (Riot API doesn't provide it)
- Live game / spectate integration
- Full-screen timeline modal (deferred — ship as tab first, evaluate modal later)
- CS-over-time chart (deferred — gold chart covers primary use case)
- Profile tabs (Tournament/Mastery/ARAM) — future phase
- Summoner icon + level badge — future phase

---

## Review Findings Integrated

Key changes from the 37-agent review:

| Finding | Source | Resolution |
|---------|--------|------------|
| `match:participants` SADD deleted in B14 | Optimizer, Developer, Database | Added T0-1 |
| main.py will hit ~5,500 lines | DevEx, Developer | Added T0-2 module split |
| Gold chart needs per-player colors | Graphic Designer | 5+5 color palette added |
| Damage colors transposed | Graphic Designer | Physical=orange, magic=blue |
| "Etc" is not a tab name | Content Writer, UX | Renamed to "Timeline" |
| Tab order buries Build | UX | Reordered: Overview > Build > Team Analysis > AI Score > Timeline |
| Kill events need assist IDs | PM, Architect | T1-4 format includes `assists` array with champion names |
| Kill events used participant IDs | Architect | Denormalized to champion names |
| Grade badges conflict with PBI tiers | Design Director | Unified color system |
| Grade-D fails WCAG AA | Code Reviewer | Fixed contrast |
| Surface-to-bg contrast too low | Design Director | Adjusted surface/border values |
| SVG needs viewBox for mobile | Responsive Designer | Required in T4-1 |
| Tab strip needs overflow-x:auto | Responsive, UX | Required in T2-1 |
| Localization migration too large for S | Code Reviewer, PM | T0-3: new strings only, no full migration |
| CSS radio tabs won't work with AJAX | Web Designer, Code Reviewer | Committed to vanilla JS |
| SVG in f-strings breaks | Web Designer | String concat required |
| AI Score 7 stats but "6 bars" | PM, Code Reviewer, Formal Verifier | Fixed to 7 |
| AI Score min==max edge case | Formal Verifier, PM | Added: returns 50 (midpoint) |
| Timeline tab empty when no data | UX | Suppress tab server-side |
| Error message exposes env var | Content Writer | Reworded |
| DDragon stat shards not in JSON | Developer | Text labels fallback |
| Sprint 5 overloaded | PM | Moved T5-6→T3-5 |
| No doc update tasks | Doc Keeper | Added per-sprint doc updates |
| CLAUDE.md needs Phase 24 TODO | Doc Keeper | Added T0-6 |
| Fragment cache stale on deploy | Database | Version prefix in cache key |
| `?nocache=1` cache poisoning | Security | Skip both read and write |
| Sticky sidebar overflow on mobile | Responsive | Mobile CSS in T2-5 |
| Skill grid overflows mobile | Responsive | `.table-scroll` wrapper in T3-2 |
| Responsive deferred too late | Responsive, UX | Each sprint handles its own |
| `t()` should auto-escape | Security | Default escape, `t_raw()` for HTML |
| DDragon version should be validated | Security | Regex check in T3-5 |
| Hardcoded #2ecc40 values | Design Director | Fixed in T0-4 |
| `_page()` title unescaped | Security | Escape in `_page()` itself |
| Recently Played With scan unbounded | PM, Security | Capped at 20 matches |
