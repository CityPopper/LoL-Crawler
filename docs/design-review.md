# Consolidated Design Review

**Date:** 2026-03-18
**Reviewed by:** web-designer, responsive-designer, graphic-designer, design-director
**Scope:** Web UI, terminal output (Admin CLI, LCU), documentation diagrams
**Phase:** 7 (IRONCLAD)

---

## Agent Reports

### 1. Web Designer Report

#### Phase A -- Docs vs Implementation Gaps

**Documented but NOT implemented:**

| Feature | Documented In | Status in Code |
|---------|--------------|----------------|
| `/players` route | `02-services.md` is MISSING it | Implemented in `main.py:379` |
| `/logs` route | `02-services.md` is MISSING it | Implemented in `main.py:741` |
| `system:priority_count` on `/streams` | `07-next-phase.md` WQ-11 | Not implemented (Sprint 5) |
| Priority indicator on `/stats` | `07-next-phase.md` WQ-11 | Not implemented (Sprint 5) |
| `/health` JSON endpoint | `02-monitoring.md` Future Improvements | Not implemented |
| Auto-refresh on `/streams` | `02-monitoring.md` says "auto-refreshable via browser reload" | No auto-refresh; manual browser reload only |

**Missing from docs:**

1. `/players` and `/logs` routes are not in the `02-services.md` route table (P0-19 already tracks this)
2. No documentation of the log viewer CSS system (`_LOG_CSS`, level-based coloring, flex layout)
3. No documentation of `_merged_log_lines` behavior (file-based tail, JSON parse, timestamp sort)
4. No documentation of the LCU background reload feature (`LCU_POLL_INTERVAL_MINUTES`)
5. No documentation of match history lazy-load UX (what users see during loading)

#### Phase B -- Route-by-Route Code Review

**`_page()` (main.py:110-145) -- Global Layout**

Issues:
- **No dark theme.** CSS uses `body {}` with no background color -- defaults to white. The agent definitions and `02-monitoring.md` describe a dark theme (`#1a1a2e` background) but the actual CSS is light theme with `#f0f0f0` table headers and `#ccc` borders
- **No viewport meta tag.** `<head>` has `charset` and `title` only -- no `<meta name="viewport">`
- **No favicon.** Browser shows default icon
- **No CSS custom properties.** Colors are hardcoded as literals (`green`, `red`, `orange`, `#ccc`)
- **No component reuse.** Each route builds HTML from scratch with ad-hoc string concatenation
- **Inline status colors use CSS named colors** (`green`, `red`, `orange`) instead of the design system hex values (`#2ecc40`, `#ff4136`, `#ffdc00`)
- **Nav has no active state indicator.** All nav links look identical regardless of current page
- **Font stack is just `monospace`.** Design system specifies `'Fira Code', 'JetBrains Mono', 'Cascadia Code', monospace`

Suggested CSS replacement for `_page()` (main.py:117-131):
```css
:root {
  --color-bg: #1a1a2e;
  --color-surface: #16213e;
  --color-text: #e0e0e0;
  --color-muted: #888;
  --color-border: #333;
  --color-success: #2ecc40;
  --color-error: #ff4136;
  --color-warning: #ffdc00;
  --color-info: #0074d9;
  --font-mono: 'Fira Code', 'JetBrains Mono', 'Cascadia Code', monospace;
}
body { font-family: var(--font-mono); background: var(--color-bg); color: var(--color-text);
       max-width: 900px; margin: 2rem auto; padding: 0 1rem; line-height: 1.6; }
a { color: var(--color-info); }
h1 { border-bottom: 2px solid var(--color-border); padding-bottom: 0.5rem; }
nav a { margin-right: 1rem; padding: 4px 8px; }
nav a.active { border-bottom: 2px solid var(--color-info); }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
td, th { border: 1px solid var(--color-border); padding: 0.4rem 0.8rem; text-align: left; }
th { background: var(--color-surface); color: var(--color-muted); }
.success { color: var(--color-success); }
.error { color: var(--color-error); }
.warning { color: var(--color-warning); }
.unverified { color: var(--color-warning); }
pre { background: var(--color-surface); padding: 12px; overflow-x: auto; border-radius: 4px; }
input, select { background: var(--color-surface); color: var(--color-text); border: 1px solid var(--color-border);
                padding: 0.4rem; margin: 0.2rem; }
button { background: var(--color-info); color: #fff; border: none; padding: 0.4rem 1rem; cursor: pointer; border-radius: 4px; }
button:hover { opacity: 0.9; }
```

**`/stats` (main.py:281-373) -- Player Stats**

Issues:
- **No loading state.** Auto-seed shows a text message but no visual indicator (spinner, progress bar)
- **System halted state** shows same form with a red message -- should show a prominent banner
- **Stats table sorts keys alphabetically** (main.py:182) -- `avg_assists` before `total_games` is confusing. Should use semantic grouping: totals first, then averages, then derived (KDA, win_rate)
- **Champion/Role tables lack visual emphasis.** No bar charts or visual representation of relative values
- **Empty state for "No verified API stats yet"** is a plain `<p>` tag -- should be a styled card/panel

**`/stats/matches` (main.py:538-568) -- Match History**

Issues:
- **N+1 Redis query pattern** (main.py:563-565): `HGETALL` per match in a loop. Already tracked as CQ-17
- **No loading indicator in JS** -- `container.innerHTML = '<p>Loading...</p>'` is unstyled plain text
- **Error display** (main.py:208): `e` is used directly -- CQ-4 already tracks the `e.message || e` fix
- **No empty state differentiation.** "No match history found" could mean "no matches exist" or "pipeline still processing" -- should check if player is recently seeded

**`/players` (main.py:379-449) -- Player List**

Issues:
- **No search/filter.** With 1000+ players, pagination alone is insufficient
- **Seeded date truncated to 10 chars** (main.py:429) -- shows date but not time; inconsistent with ISO 8601 used elsewhere
- **No indication of player pipeline status** (processing, complete, error)
- **Pagination links are plain text** `<- Prev` / `Next ->` with no touch-target sizing

**`/streams` (main.py:452-485) -- Pipeline Health**

Issues:
- **No auto-refresh.** Unlike `/logs`, the streams page requires manual browser reload
- **No visual health indicators.** Stream depths are plain numbers with no color coding for warning/critical thresholds
- **No consumer group info.** Does not show pending counts, consumer lag, or consumer names
- **System halted banner** uses inline `<p class="error">` -- should be a prominent full-width banner with fix instructions (`just admin system-resume`)

**`/lcu` (main.py:571-615) -- LCU Match History**

Issues:
- **No link to individual player stats.** Players listed by PUUID prefix -- should link to `/stats?riot_id=...`
- **Mode string is dense** (main.py:591-593): `CLASSIC (5/10), ARAM (3/5)` -- should be a sub-table or expandable section
- **Empty state is good** (instructions to run `just lcu`) but could include a code-copy button

**`/logs` (main.py:741-802) -- Log Viewer**

Issues:
- **Log CSS is a separate block** (`_LOG_CSS`, main.py:629-654) that uses a LIGHT theme (white backgrounds: `#ffe0e0`, `#fff0f0`, `#fffbe6`) while the rest of the UI should be dark theme. These colors clash with the design system
- **No level filter.** Cannot filter by ERROR/WARNING/DEBUG
- **No service filter.** All services mixed together
- **No search.** Cannot search log content
- **Pause button has no visual state** -- `paused` class sets `background: #e33` but there is no indication it is a toggle before clicking

---

### 2. Responsive Designer Report

#### Phase A -- Docs Review

**Responsive design is NOT tracked anywhere in Phase 7.** The 07-next-phase.md plan contains zero mentions of responsive, mobile, viewport, breakpoint, or touch. The `doc-review-suggestions.md` file also contains no responsive-related suggestions.

**Recommendation:** Add a responsive item to Sprint 1 (P0) since the missing viewport meta tag is a functional bug, not a nice-to-have. At minimum: viewport meta + table scroll wrappers + form stacking. Full responsive polish can be Phase 8.

#### Phase B -- Responsive Audit by Route

**Global (`_page()` main.py:110-145):**

| Issue | 320px | 768px | 1440px | Fix |
|-------|-------|-------|--------|-----|
| No viewport meta | Page renders at desktop width, requires pinch zoom | OK | OK | Add `<meta name="viewport" content="width=device-width, initial-scale=1">` at main.py:114 |
| `max-width: 900px` hardcoded | Body is 900px wide, overflows screen | OK | Could be wider | Use `max-width: min(900px, 100% - 2rem)` at main.py:117 |
| Nav `margin-right: 1rem` | 5 links wrap messily on 2+ lines | OK | OK | Use `display: flex; gap: 8px; overflow-x: auto; flex-wrap: nowrap;` |
| No touch targets | Links are text-sized (~14px tall) | OK-ish | OK | Add `min-height: 44px; display: inline-flex; align-items: center;` to nav links |

**`/stats` form (main.py:148-172):**

| Issue | 320px | 768px | Fix |
|-------|-------|-------|-----|
| `input size="30"` | Input is ~300px, overflows 320px screen | OK | Remove `size="30"`, use `width: 100%` on mobile via media query |
| Form is inline (label + input + select + button in one line) | All elements on one line, tiny and untappable | OK | Stack vertically below 768px: `flex-direction: column; gap: 8px;` |
| Input `padding: 0.4rem` | Touch target is ~25px tall | OK | Set `min-height: 44px; font-size: 16px;` (16px prevents iOS auto-zoom) |
| Select has no sizing | Tiny on mobile | OK | Match input styling |

**All tables (`/stats`, `/players`, `/streams`, `/lcu`, `/stats/matches`):**

| Issue | 320px | Fix |
|-------|-------|-----|
| Tables overflow horizontally | Match history has 6 columns (Date, Result, Champion, Role, K/D/A, Mode); overflows at 320px | Wrap all `<table>` in `<div class="table-scroll">` with `overflow-x: auto; -webkit-overflow-scrolling: touch;` |
| No sticky first column | When scrolling, player/match identity is lost | Consider `position: sticky; left: 0;` on first `<td>` |

**`/logs` (main.py:741-802):**

| Issue | 320px | Fix |
|-------|-------|-----|
| Flex log lines with 5 spans | Timestamp + badge + service + message + extra all in one row; wraps chaotically on mobile | Use `flex-wrap: wrap;` and make timestamp + badge one row, message the next |
| Pause button is tiny | `padding: 0.4rem 1rem` = ~28px tall | Set `min-height: 44px;` |

**7 Critical Mobile Fixes (Priority Order):**

1. **Viewport meta tag** -- `_page()` main.py:114 -- Add `<meta name="viewport" content="width=device-width, initial-scale=1">`
2. **Input font-size 16px** -- main.py:121 -- Prevents iOS auto-zoom on focus
3. **Touch targets >= 44px** -- All buttons, links, nav items -- `min-height: 44px`
4. **Table scroll wrapper** -- All `<table>` elements -- `overflow-x: auto` wrapper div
5. **Form stacking on mobile** -- main.py:148-172 -- Full-width inputs below 768px
6. **Nav horizontal scroll** -- main.py:135-141 -- `display: flex; overflow-x: auto; flex-wrap: nowrap;`
7. **Fluid max-width** -- main.py:117 -- Replace `900px` with `min(900px, 100% - 2rem)`

---

### 3. Graphic Designer Report

#### Phase A -- Documentation Diagrams

**README.md pipeline diagram (line 7-13):**
```
Seed -> Crawler -> Fetcher -> Parser -> Analyzer
                    ^                    |
               Discovery        Recovery + Delay Scheduler
                                         |
                                   Web UI (port 8080)
```

Issues:
- Uses ASCII `->` and `^` instead of Unicode box-drawing characters (`-->`, `|`)
- Does not match the graphic-designer template style (no boxes: `[Seed]` or `+---------+`)
- Web UI arrow from Recovery is misleading -- UI is independent, not downstream of Recovery
- Discovery arrow points to Crawler but Discovery publishes to `stream:puuid`, not directly to Crawler
- Width: 56 columns -- fits 80-column terminal
- **Accuracy:** Mostly correct but missing the DLQ flow and the Parser->Discovery fan-out

**ARCHITECTURE.md data flow diagram (line 75-89):**
```
CLI Input
    |
    v
Seed --stream:puuid--> Crawler --stream:match_id--> Fetcher ...
```

Issues:
- Uses plain ASCII (`--`, `-->`, `|`, `v`) instead of Unicode box-drawing
- Inconsistent style with README diagram (this one has stream labels, README does not)
- Width: ~85 columns -- exceeds 80-column limit at the longest line
- Missing Discovery's write to `discover:players` (documented gap in `doc-review-suggestions.md`)
- **Accuracy:** Core pipeline flow is correct. DLQ/Recovery/Scheduler flow is correct. Missing Discovery.

**06-failure-resilience.md DLQ lifecycle (line 53-77):**
```
Service fails to process message
        |
        +-- attempts < max_attempts
        |       ZADD delayed:messages ...
        ...
```

Issues:
- Uses a tree-style layout (`+--`, `|`) -- different style from the pipeline diagrams
- No boxes or Unicode -- plain ASCII throughout
- Width: ~75 columns -- fits 80
- **Accuracy:** Missing the in-process retry phase (`_handle_with_retry` retries up to 3 handler crashes before nack). Already tracked as P0-34
- Good information density -- text labels on each branch are clear

**02-monitoring.md (line 319-325) -- Terminal dashboard:**
```bash
watch -n 5 'just streams && echo "---" && docker compose ps ...'
```
- This is a command, not a diagram. No visual dashboard wireframe exists in this doc
- The "Dashboard Design Suggestions" section mentions the web UI `/streams` page but does not include an ASCII wireframe for a future dashboard
- **Missing:** A visual mockup of what an ideal streams dashboard should look like

**03-streams.md -- Flow diagrams (line 110-138):**
- Uses indented text with arrows (`-->`, `<--`) and boxes implied by whitespace
- Two diagrams: the DLQ-to-delay flow and the Delay Scheduler loop
- Consistent with 06-failure-resilience.md tree style
- Width: ~70 columns -- fits 80
- **Accuracy:** Correct

**Diagram style consistency summary:**

| Diagram | Style | Unicode? | Boxes? | Width | Accurate? |
|---------|-------|----------|--------|-------|-----------|
| README pipeline | Plain ASCII arrows | No | No | 56 | Mostly |
| ARCHITECTURE data flow | Plain ASCII + stream labels | No | No | ~85 (over!) | Missing Discovery |
| DLQ lifecycle | Tree/branch ASCII | No | No | ~75 | Missing in-process retry |
| Streams delayed pattern | Indented text + arrows | No | No | ~70 | Yes |
| Monitoring dashboard | (none -- just a bash command) | N/A | N/A | N/A | N/A |

**Missing diagrams that should exist:**
1. Discovery fan-out flow (Parser -> `discover:players` -> Discovery -> `stream:puuid`)
2. Dashboard wireframe for `/streams` page (what it should look like at 1440px)
3. Priority queue flow (Sprint 5 -- Lua SET+INCR, counter check, DEL+DECR)
4. Match lifecycle state machine (fetched -> parsed -> analyzed, with error states)

#### Phase B -- Terminal Output Review

**Admin CLI (`lol-pipeline-admin/src/lol_admin/main.py`):**

| Output | Current | Issue | Suggestion |
|--------|---------|-------|------------|
| `cmd_stats` (line 100-102) | `Stats for Name#Tag (puuid...):\n  key: value` | Good plain-text format | Add `checkmark` prefix: `Stats for Name#Tag (puuid...):` |
| `cmd_dlq_list` (line 117-128) | `json.dumps(record)` per entry | Raw JSON -- hostile to humans (CQ-2 tracks this) | Use tabular format: `ID  FAILURE  STREAM  ATTEMPTS  AGE` |
| `cmd_dlq_replay` (line 144) | `replayed {id} -> {stream}` | Good | Add checkmark: `replayed {id} -> {stream}` |
| `cmd_system_resume` (line 108) | `system resumed -- system:halted cleared` | Good | Add checkmark: `system resumed` |
| `cmd_dlq_clear` (line 158) | `cleared {n} entries from stream:dlq` | Good | Consistent with other outputs |
| `cmd_replay_parse` (line 179) | `replayed {n} entries to stream:parse` | Good | Consistent |
| `cmd_replay_fetch` (line 193) | `enqueued {id} -> stream:match_id` | Good | Consistent |
| `cmd_reseed` (line 221) | `reseeded {id} -> stream:puuid ({entry_id})` | Good | Consistent |
| Error output | `_log.error(...)` -- JSON structured log | Hostile to CLI users | Use `print()` for user-facing errors (CQ-2) |

Overall: Admin CLI output is mostly good plain text. The main issues are `dlq list` outputting raw JSON (CQ-2) and error messages going through the JSON logger instead of `print()`.

**LCU Collector (`lol-pipeline-lcu/src/lol_lcu/main.py`):**

| Output | Current | Issue |
|--------|---------|-------|
| `log.info("Loaded %d existing game IDs for %s", ...)` (line 99) | JSON structured log | Should be `print()` for terminal use (CQ-3) |
| `log.info("Collected %d new matches for %s", ...)` (line 142) | JSON structured log | Should be `print()` for terminal use |
| `log.warning("League client not running -- showing historical summary")` (line 178) | JSON structured log | Should be `print()` with `warning` icon |
| `log.info("Polling every %d minutes", ...)` (line 185) | JSON structured log | Should be `print()` |
| `_show_summary` (line 194-202) | `log.info("%s: %d matches", ...)` per file | Should be a formatted table |
| `log.error("LEAGUE_INSTALL_PATH not set")` (line 173) | JSON structured log | Should be `print("Error: ...")` |

Overall: LCU outputs everything through JSON structured logging, which renders as machine-readable JSON on terminal. CQ-3 already tracks this. The `_show_summary` function should display a formatted table.

**Justfile `just streams` (line 82-91):**

Current output format:
```
stream:puuid:            0
stream:match_id:         0
stream:parse:            0
stream:analyze:          0
stream:dlq:              0
delayed:messages:        0
```

Issues:
- Uses `printf` with `%-24s` alignment -- good
- No header row
- No status indicators (all values are just numbers; no green/red for healthy/warning)
- Missing `stream:dlq:archive` (present in Web UI `/streams` but missing from `just streams`)
- No `system:halted` check

Suggested improvement:
```
Pipeline Streams
---
stream:puuid:            0
stream:match_id:         0
stream:parse:            0
stream:analyze:          0
stream:dlq:              0
stream:dlq:archive:      0
delayed:messages:        0
---
system:halted:           (not set)
```

**Status indicator consistency across surfaces:**

| Concept | Web UI | Admin CLI | LCU CLI | Docs | Consistent? |
|---------|--------|-----------|---------|------|-------------|
| Success/healthy | `<span class="success">checkmark</span>` (green) | Plain text | N/A | `checkmark` | No -- CLI has no icons |
| Error/failed | `<span class="error">warning</span>` (red) | JSON log | JSON log | `X` | No -- CLI uses logger |
| Warning | `<span class="warning">...</span>` (orange) | JSON log | JSON log | `warning` | No -- CLI uses logger |
| System halted | `<p class="error">warning System is HALTED</p>` | `system resumed` text | N/A | `system:halted = 1` | Partial |
| DLQ entry | Table row with entry_id | `json.dumps(record)` | N/A | Text description | No -- CLI outputs raw JSON |

---

### 4. Design Director Report

#### Phase A -- Design System Alignment

**Agent definition review:**

| Token | web-designer.md | responsive-designer.md | graphic-designer.md | design-director.md |
|-------|----------------|----------------------|--------------------|--------------------|
| `--color-bg` | `#1a1a2e` | (references web-designer) | `#1a1a2e` (in CSS block) | `#1a1a2e` |
| `--color-surface` | `#16213e` | (references web-designer) | Not listed | `#16213e` |
| `--color-text` | `#e0e0e0` | (references web-designer) | `#e0e0e0` | `#e0e0e0` |
| `--color-success` | `#2ecc40` | (references web-designer) | `#2ecc40` | `#2ecc40` |
| `--color-error` | `#ff4136` | (references web-designer) | `#ff4136` | `#ff4136` |
| `--color-warning` | `#ffdc00` | (references web-designer) | `#ffdc00` | `#ffdc00` |
| `--color-info` | `#0074d9` | (references web-designer) | Not listed | `#0074d9` |
| Font stack | Fira Code, JetBrains Mono, Cascadia Code, monospace | (references web-designer) | Same | Same |
| Spacing scale | 4/8/16/24/32px | (references web-designer) | Not listed | Same as web-designer |

**Alignment verdict:** Agent definitions are well-aligned on color tokens and typography. The responsive-designer correctly defers to the web-designer for design system values. The graphic-designer is missing `--color-surface`, `--color-info`, and the spacing scale -- these should be added.

**Critical finding:** The ACTUAL CODE does not use ANY of these design tokens. The `_page()` CSS (main.py:117-131) uses:
- `green` instead of `#2ecc40`
- `red` instead of `#ff4136`
- `orange` instead of `#ffdc00`
- `#f0f0f0` table header background (LIGHT theme) instead of `#16213e` (dark theme)
- `#ccc` borders instead of `#333`
- `monospace` instead of the full font stack
- No CSS custom properties at all

The design system exists only in agent definitions -- it has never been applied to the code.

#### Phase B -- Cross-Surface Consistency

**Concept: "Player not found"**

| Surface | Display |
|---------|---------|
| Web UI `/stats` | `<p class="error">Player not found: Name#Tag</p>` -- red text in form |
| Admin CLI `stats` | `_log.error("player not found", extra={"riot_id": ...})` -- JSON log line |
| Seed CLI | `_log.error("player not found", extra={"riot_id": ...})` -- JSON log line |

Inconsistency: Web UI gives a human-readable message. CLI tools emit JSON logs. CQ-2 and CQ-3 track this.

**Concept: "System halted"**

| Surface | Display |
|---------|---------|
| Web UI `/streams` | `<p class="error">warning System is HALTED (system:halted is set)</p>` |
| Web UI `/stats` | `<p class="error">System halted. No stats yet for Name#Tag.</p>` |
| Admin CLI | `system resumed -- system:halted cleared` (only on resume; no display of halted state) |
| Justfile `just streams` | No halted check at all |

Inconsistency: No way to see halted status from CLI without `redis-cli GET system:halted`. `just streams` should include it.

**Concept: "Stream depths"**

| Surface | Display |
|---------|---------|
| Web UI `/streams` | HTML table: Key + Length columns; includes `stream:dlq:archive` and `delayed:messages` |
| Justfile `just streams` | `printf` columns: name + count; missing `stream:dlq:archive`; includes `delayed:messages` |

Inconsistency: `just streams` omits `stream:dlq:archive`. Different key set displayed on each surface.

**Concept: "DLQ entries"**

| Surface | Display |
|---------|---------|
| Web UI | Not shown (no DLQ page; only count on `/streams`) |
| Admin CLI `dlq list` | Raw JSON per entry: `{"entry_id": ..., "failure_code": ..., ...}` |

Gap: No way to browse DLQ entries in the web UI. Admin CLI outputs raw JSON.

**Concept: "Verified vs Unverified data"**

| Surface | Display |
|---------|---------|
| Web UI `/stats` | "Verified (Riot API) checkmark" header (green) + "Unverified (LCU) warning" header (dark goldenrod `#b8860b`) |
| Web UI `/lcu` | "LCU Match History warning Unverified" header |
| Docs | "verified (Riot API)" / "unverified (local client)" text |
| CLI | No concept of verified vs unverified |

Mostly consistent on Web UI. The `#b8860b` color for `.unverified` does not match any design token -- should use `--color-warning` (`#ffdc00`).

---

## Synthesized Findings

### Phase 7 Additions (should be in current plan)

These are functional bugs or critical gaps that should be addressed within Phase 7.

| ID | Finding | Source Agent | Priority | Effort | Suggested Sprint |
|----|---------|-------------|----------|--------|-----------------|
| DR-1 | **No viewport meta tag** -- mobile is completely broken | responsive | P0 | Trivial (1 line) | Sprint 1 |
| DR-2 | **Light theme in code vs dark theme in design system** -- `_page()` CSS uses white/light colors while all design docs specify dark theme (`#1a1a2e` bg) | web, director | P1 | Small (CSS rewrite) | Sprint 1 |
| DR-3 | **Log viewer CSS uses light-theme colors** (`#ffe0e0`, `#fff0f0`, `#fffbe6`) -- clashes if/when dark theme is applied | web | P1 | Small | Sprint 1 (with DR-2) |
| DR-4 | **`just streams` missing `stream:dlq:archive`** and `system:halted` check | graphic, director | P1 | Trivial | Sprint 1 |
| DR-5 | **CSS named colors** (`green`, `red`, `orange`) instead of design-system hex values | web, director | P1 | Small | Sprint 1 (with DR-2) |
| DR-6 | **No auto-refresh on `/streams`** -- monitoring doc implies it, but no JS polling exists | web | P2 | Small (copy `/logs` pattern) | Sprint 2 |
| DR-7 | **Stats table sorts alphabetically** -- `avg_assists` before `total_games` is confusing | web | P2 | Small | Sprint 2 |
| DR-8 | **`/streams` page has no visual health thresholds** -- all numbers plain, no green/yellow/red | web, graphic | P2 | Small | Sprint 5 (with WQ-11) |
| DR-9 | **`.unverified` color `#b8860b` not in design system** -- should use `--color-warning` | director | P2 | Trivial | Sprint 1 (with DR-2) |
| DR-10 | **No table scroll wrappers** -- tables overflow on mobile | responsive | P1 | Small | Sprint 1 (with DR-1) |

### Phase 8 Deferrals (nice-to-have, not blocking)

These are improvements that enhance UX but are not functional bugs.

| ID | Finding | Source Agent | Effort |
|----|---------|-------------|--------|
| DR-11 | Player search/filter on `/players` page | web | Medium |
| DR-12 | Log level and service filters on `/logs` page | web | Medium |
| DR-13 | DLQ browser page in Web UI (view entries without CLI) | web, director | Medium |
| DR-14 | Loading spinners/skeleton screens for AJAX requests | web, responsive | Small |
| DR-15 | Nav active state indicator (highlight current page) | web | Trivial |
| DR-16 | Favicon for browser tab | web | Trivial |
| DR-17 | Player pipeline status indicator on `/players` (processing/complete/error) | web | Medium |
| DR-18 | Wide-screen layout (1440px+): multi-column dashboard, expanded stats sidebar | responsive | Medium |
| DR-19 | Dashboard wireframe diagram in `02-monitoring.md` | graphic | Small |
| DR-20 | Discovery fan-out flow diagram for docs | graphic | Small |
| DR-21 | Priority queue flow diagram (Sprint 5 Lua scripts) | graphic | Small |
| DR-22 | Match lifecycle state machine diagram | graphic | Small |
| DR-23 | Code-copy button on LCU empty state instructions | web | Trivial |
| DR-24 | Visual bar charts for champion/role distribution on `/stats` | web | Medium |
| DR-25 | LCU summary as formatted table instead of log lines | graphic | Small |

### Design System Recommendations (standards to adopt)

These are systemic improvements that should be formalized before Phase 8.

| ID | Recommendation | Rationale |
|----|---------------|-----------|
| DS-1 | **Adopt CSS custom properties in `_page()`.** Define the `:root` variables from the design-director's palette. All colors, fonts, spacing, and border-radius referenced via `var(--token)`. | Enables theme consistency, future theme switching, reduces ad-hoc color literals |
| DS-2 | **Apply dark theme to match design system.** The agent definitions all describe dark theme (`#1a1a2e` bg) but the code uses light theme. Pick one and be consistent. Recommendation: dark theme (matches developer-tool aesthetic, reduces eye strain for monitoring). | Design system exists on paper but not in code |
| DS-3 | **Standardize status indicators across all surfaces.** Web: `<span class="badge badge--success">checkmark Running</span>`. CLI: `checkmark text` (via `print()`). Docs: `checkmark` plain. Same icon + color for same concept everywhere. | Currently: Web uses HTML spans, CLI uses JSON logs, docs use text. No consistency. |
| DS-4 | **Add viewport meta and mobile-first media queries.** Even if full responsive polish is Phase 8, the viewport meta tag and basic table scroll wrappers are zero-cost, high-impact. | Currently a functional bug: mobile users see a desktop-width page |
| DS-5 | **Standardize documentation diagram style.** Use Unicode box-drawing characters (`---`, `|`, `+--+`, `-->`) for all diagrams. Max width 80 columns. Include stream/key labels on arrows. | Currently: 3 different diagram styles (plain ASCII, tree-branch, indented text). None use Unicode box-drawing. |
| DS-6 | **Update graphic-designer agent definition** to include `--color-surface`, `--color-info`, and the spacing scale. Currently missing these tokens. | Agent definitions should be the source of truth for the design system |
| DS-7 | **Create a `_badge()` HTML helper** for status indicators: `_badge("success", "checkmark Running")`, `_badge("error", "X Halted")`, `_badge("warning", "warning Slow")`. Reuse across all routes. | Currently each route builds status HTML ad-hoc with different markup patterns |
| DS-8 | **Consolidate font stack.** Replace `monospace` in `_page()` with the full font stack from the design system: `'Fira Code', 'JetBrains Mono', 'Cascadia Code', monospace`. | Design system specifies it; code does not use it |
| DS-9 | **Match `just streams` output to Web UI `/streams` key set.** Both should show the same streams + `system:halted`. Add `stream:dlq:archive` to `just streams`. | Cross-surface consistency: same data on all surfaces |
| DS-10 | **CLI error output via `print()` not `_log.error()`.** Already tracked as CQ-2 (Admin) and CQ-3 (LCU) but the design rationale is: CLI users expect `stderr` text, not JSON structured logs. | Machine-readable logs are for services; human-readable text is for CLI tools |

---

## Implementation Priority

**Sprint 1 quick wins (can bundle with existing P0 items):**
1. DR-1: Add viewport meta tag (1 line)
2. DR-2 + DR-3 + DR-5 + DR-9: Apply dark theme CSS with custom properties (replaces 15 lines of CSS)
3. DR-4: Add `stream:dlq:archive` and `system:halted` to `just streams` (3 lines in Justfile)
4. DR-10: Add `<div class="table-scroll">` wrapper to all tables (string change in each route)
5. DS-8: Update font stack (1 token change)

**Sprint 2 (bundle with CQ items):**
1. DR-6: Auto-refresh on `/streams` (copy `/logs` AJAX pattern)
2. DR-7: Semantic stats ordering
3. DS-3 + DS-7: Status badge helper + cross-surface consistency
4. DS-10: CLI error output via `print()` (already CQ-2, CQ-3)

**Sprint 5 (bundle with WQ-11):**
1. DR-8: Visual health thresholds on `/streams` (color-code by depth)
2. Add `system:priority_count` display (WQ-11)
3. Priority indicator on `/stats` (WQ-11)
