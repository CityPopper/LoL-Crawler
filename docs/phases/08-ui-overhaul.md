# Phase 08 — FACELIFT

**Codename:** Facelift
**Purpose:** Transform the Web UI from a functional prototype into a polished, responsive, dark-themed monitoring dashboard — then clean up all deferred Phase 7 items.

**Status:** Planning (reviewed by 12 agents, Round 1 complete)
**Prerequisite:** Phase 7 IRONCLAD complete

---

## Agent Review Summary (Round 1)

12 specialist agents reviewed this plan. Consolidated findings below. All required changes have been incorporated into the plan.

| Agent | Verdict | Confidence | Key Finding |
|-------|---------|------------|-------------|
| web-designer | REQUEST CHANGES | 8/10 | Missing accessibility (focus-visible, prefers-reduced-motion, color-scheme meta, semantic HTML), font-size tokens, .btn class |
| responsive-designer | REQUEST CHANGES | 7/10 | Mobile form pattern should be mobile-first (not max-width), missing .btn class, nav touch targets at risk on mobile |
| design-director | REQUEST CHANGES | 8/10 | --color-info fails WCAG AA (3.88:1), error/muted badge contrast failures, typography tokens missing from :root, Admin CLI lacks status indicators |
| graphic-designer | APPROVE | 8/10 | .badge--muted WCAG fail (2.2:1), bump --color-muted to #999 |
| ui-ux | APPROVE w/ changes | 8/10 | _STATS_ORDER missing 3 stats, win_rate needs formatting, halt banner should be global, DLQ nav should always show with count |
| product-manager | REQUEST CHANGES | 8/10 | Sprint 4 should be a separate phase, DLQ browser belongs in Sprint 3, admin --json is not UI work |
| developer | APPROVE w/ fixes | 8/10 | DLQ route reads wrong field names, _badge needs variant whitelist, payload truncation order wrong, admin --json flag position |
| security | APPROVE w/ conditions | 8/10 | XSS in onclick handlers (use data-* attrs), _badge variant whitelist, player filter must use textContent not innerHTML |
| tester | REQUEST CHANGES | 8/10 | Existing tests WILL break (enumerate per sprint), test count targets inconsistent (10 vs 20 vs 30), missing test specs for nav active, /streams/fragment, depth badges |
| content-writer | REQUEST CHANGES | 8/10 | "Refresh in a minute" unreliable, DLQ column labels ambiguous, "tagline" wrong term, LCU empty state ignores auto-reload, "retry" vs "replay" mismatch |
| qa-tester | REQUEST CHANGES | 8/10 | _page() has 9 call sites (not 6), show_logs() line ref wrong (793->789), match history Win/Loss missed for _badge(), route count inconsistent |
| devex | REQUEST CHANGES | 8/10 | CSS-in-f-string won't scale (extract to constant or static file), need just dev-ui hot-reload, show_stats() will breach complexity limit |

---

## Delivery Order

| Sprint | Focus | Est. Effort |
|--------|-------|-------------|
| 1 | Design System Foundation — dark theme, CSS custom properties, responsive base, accessibility | Medium |
| 2 | Page-by-Page Redesign — every route rebuilt with new design system | Large |
| 3 | Interactive Features — auto-refresh, loading states, empty states, DLQ browser, filters | Medium-Large |
| 5 | Polish — favicon, log colors, stats sorting, player filter, admin CLI status indicators | Medium |

> **NOTE:** Sprint 4 (deferred Phase 7 infra/docs/test items) has been **extracted to Phase 9** per product-manager review. Those 19 items across Docker, docs, and test infra are not UI work and should not block the FACELIFT gate. The admin CLI `--json` flag has also been deferred to Phase 9 (Sprint 5.4).

---

## Sprint 1 — Design System Foundation

Apply the dark theme CSS custom properties from the design-director agent definition to `_page()`. Replace all hardcoded colors. Add responsive foundation.

### 1.0 CSS Extraction (DevEx Fix)

**[REVIEW FIX]** The expanded CSS (~120 lines) makes the f-string `{{ }}` escaping unmanageable. Extract CSS to a module-level plain string constant:

```python
_CSS = """
:root { --color-bg: #1a1a2e; ... }
body { font-family: var(--font-mono); ... }
/* ... all CSS rules ... */
"""

def _page(title: str, body: str, path: str = "") -> str:
    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta name="color-scheme" content="dark">
  <title>{title} — LoL Pipeline</title>
  <link rel="icon" href="data:image/svg+xml,...">
  <style>{_CSS}</style>
</head>
<body>
<header><h1>LoL Pipeline</h1><nav>...</nav></header>
<main>{body}</main>
</body></html>"""
```

Benefits: zero brace escaping, IDE CSS support, CSS linting possible, `_page()` shrinks to ~25 lines.

**Also add** a `just dev-ui` recipe to the Justfile:
```just
dev-ui:
    cd lol-pipeline-ui && uvicorn lol_ui.main:app --reload --host 0.0.0.0 --port 8080
```

### 1.1 CSS Custom Properties

Replace the inline `<style>` block in `_page()` (`main.py:122-137`) with the design system tokens. Also add `<meta name="color-scheme" content="dark">` to `<head>` (alongside the existing viewport meta) so the browser renders form controls and scrollbars in dark mode natively.

**Current CSS (lines 122-137):**
```css
body { font-family: monospace; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
h1 { border-bottom: 2px solid #ccc; padding-bottom: 0.5rem; }
nav a { margin-right: 1rem; }
/* ... light-theme colors throughout ... */
.success { color: green; }
.error { color: red; }
.warning { color: orange; }
.unverified { color: #b8860b; }
th { background: #f0f0f0; }
td, th { border: 1px solid #ccc; }
```

**Target CSS:** *(extract to `_CSS` module-level constant or `static/style.css` -- see 1.0 below)*
```css
:root {
  --color-bg: #1a1a2e;
  --color-surface: #16213e;
  --color-text: #e0e0e0;
  --color-muted: #999;          /* was #888 — bumped for WCAG AA on surface bg (5.0:1) */
  --color-border: #333;
  --color-success: #2ecc40;
  --color-error: #ff4136;
  --color-warning: #ffdc00;
  --color-info: #5a9eff;        /* was #0074d9 — bumped for WCAG AA on dark bg (5.2:1) */
  --font-mono: 'Fira Code', 'JetBrains Mono', 'Cascadia Code', monospace;
  --font-size-sm: 12px;
  --font-size-base: 14px;
  --font-size-lg: 16px;
  --font-size-xl: 20px;
  --font-size-2xl: 24px;
  --line-height: 1.6;
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --radius: 4px;
}
body {
  font-family: var(--font-mono);
  font-size: var(--font-size-base);
  background: var(--color-bg);
  color: var(--color-text);
  max-width: min(900px, 100% - 2rem);
  margin: 2rem auto;
  padding: 0 1rem;
  line-height: var(--line-height);
}
a { color: var(--color-info); }
h1 { border-bottom: 2px solid var(--color-border); padding-bottom: 0.5rem; }
hr { border: none; border-top: 1px solid var(--color-border); }
nav { display: flex; gap: var(--space-sm); overflow-x: auto; padding-bottom: var(--space-xs); }
nav a {
  white-space: nowrap;
  padding: var(--space-sm) var(--space-md);
  min-height: 44px;
  display: inline-flex;
  align-items: center;
  border-radius: var(--radius);
  text-decoration: none;
}
nav a:hover { background: var(--color-surface); }
nav a.active { border-bottom: 2px solid var(--color-info); font-weight: bold; }
:focus-visible { outline: 2px solid var(--color-info); outline-offset: 2px; }
form { margin: 1rem 0; }
input, select {
  background: var(--color-surface);
  color: var(--color-text);
  border: 1px solid var(--color-border);
  padding: var(--space-sm);
  margin: 0.2rem;
  font-size: var(--font-size-lg);
  min-height: 44px;
  border-radius: var(--radius);
}
button, .btn {
  display: inline-flex; align-items: center; justify-content: center;
  background: var(--color-info);
  color: #fff;
  border: none;
  padding: var(--space-sm) var(--space-lg);
  cursor: pointer;
  border-radius: var(--radius);
  min-height: 44px;
  font-size: var(--font-size-lg);
  text-decoration: none;
}
button:hover, .btn:hover { filter: brightness(1.1); }
.success { color: var(--color-success); }
.error { color: var(--color-error); }
.warning { color: var(--color-warning); }
.unverified { color: var(--color-warning); }
table { border-collapse: collapse; width: 100%; margin-top: 1rem; }
td, th { border: 1px solid var(--color-border); padding: 0.4rem 0.8rem; text-align: left; }
th { background: var(--color-surface); color: var(--color-muted); }
pre { background: var(--color-surface); padding: 12px; overflow-x: auto; border-radius: var(--radius); }
code { background: var(--color-surface); padding: 2px 6px; border-radius: var(--radius); }
.streams td:last-child { text-align: right; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important; transition-duration: 0.01ms !important; }
}
```

**Key changes:**
- `#f0f0f0` (light) table headers become `var(--color-surface)` (dark)
- `#ccc` borders become `var(--color-border)` (`#333`)
- Named colors `green/red/orange` become design-system hex values via CSS vars
- `.unverified` uses `--color-warning` instead of `#b8860b`
- Font stack expanded from `monospace` to the full design-system stack
- `max-width` becomes fluid: `min(900px, 100% - 2rem)`
- All inputs get `font-size: 16px` (prevents iOS auto-zoom) and `min-height: 44px` (touch target)
- **[REVIEW FIX]** `--color-info` lightened from `#0074d9` to `#5a9eff` for WCAG AA (>=4.5:1 on `#1a1a2e`)
- **[REVIEW FIX]** `--color-muted` bumped from `#888` to `#999` for WCAG AA on surface backgrounds
- **[REVIEW FIX]** Typography tokens added: `--font-size-sm/base/lg/xl/2xl`, `--line-height`
- **[REVIEW FIX]** `body { font-size: var(--font-size-base); }` explicitly set (was relying on browser default 16px)
- **[REVIEW FIX]** `:focus-visible` outline added for keyboard accessibility
- **[REVIEW FIX]** `prefers-reduced-motion` media query added (WCAG 2.1 AA)
- **[REVIEW FIX]** `.btn` class defined (was referenced but never defined)
- **[REVIEW FIX]** `hr` styled for dark theme
- **[REVIEW FIX]** `button:hover` changed from `opacity: 0.9` to `filter: brightness(1.1)` for visibility
- **[REVIEW FIX]** `nav a.active` gets `font-weight: bold` for stronger visual signal

### 1.2 Component Classes

Add reusable component classes to the `<style>` block in `_page()`:

```css
/* Cards / Panels */
.card { background: var(--color-surface); border: 1px solid var(--color-border);
        border-radius: var(--radius); padding: var(--space-md); margin: var(--space-md) 0; }
.card__title { margin-top: 0; font-size: var(--font-size-lg); color: var(--color-muted); }

/* Badges — [REVIEW FIX] contrast-checked against WCAG AA */
.badge { display: inline-block; padding: 2px 8px; border-radius: var(--radius);
         font-size: var(--font-size-sm); font-weight: bold; }
.badge--success { background: var(--color-success); color: #111; }
.badge--error { background: #cc3333; color: #fff; }  /* darkened from #ff4136 for 4.5:1 with white */
.badge--warning { background: var(--color-warning); color: #111; }
.badge--info { background: var(--color-info); color: #fff; }
.badge--muted { background: var(--color-border); color: var(--color-text); }  /* was --color-muted (2.2:1 fail) */

/* Stat counters */
.stat { display: inline-block; text-align: center; padding: var(--space-md); }
.stat__value { display: block; font-size: var(--font-size-2xl); font-weight: bold; }
.stat__label { display: block; font-size: var(--font-size-sm); color: var(--color-muted); }

/* Gauge bars */
.gauge { position: relative; height: 20px; background: var(--color-border);
         border-radius: var(--radius); overflow: hidden; }
.gauge__fill { height: 100%; background: var(--color-info); transition: width 0.3s; }
.gauge__label { position: absolute; top: 0; left: 8px; line-height: 20px;
                font-size: var(--font-size-sm); color: var(--color-text); }

/* Form layout */
.form-inline { display: flex; flex-wrap: wrap; gap: var(--space-sm); align-items: flex-end; }
.form-inline label { display: flex; flex-direction: column; gap: 2px; font-size: var(--font-size-sm);
                     color: var(--color-muted); }
.form-inline input, .form-inline select { flex: 1; min-width: 0; }

/* Table scroll wrapper (responsive) */
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }

/* Banner (system status) */
.banner { padding: var(--space-md); border-radius: var(--radius); margin: var(--space-md) 0;
          border-left: 4px solid; }
.banner--error { background: rgba(255,65,54,0.1); border-color: var(--color-error); }
.banner--success { background: rgba(46,204,64,0.1); border-color: var(--color-success); }
.banner--warning { background: rgba(255,220,0,0.1); border-color: var(--color-warning); }

/* Empty state */
.empty-state { text-align: center; padding: var(--space-xl); color: var(--color-muted); }
.empty-state code { display: block; margin-top: var(--space-sm); }

/* Stats grid (wide desktop) */
.stats-grid { display: grid; grid-template-columns: 1fr; gap: var(--space-md); }

/* Skip to content (a11y) */
.skip-link { position: absolute; top: -40px; left: 0; padding: var(--space-sm); background: var(--color-info); color: #fff; z-index: 100; }
.skip-link:focus { top: var(--space-sm); }
```

**[REVIEW FIX]** Gauge HTML must include ARIA attributes:
```html
<div class="gauge" role="progressbar" aria-valuenow="75" aria-valuemin="0" aria-valuemax="100">
```

### 1.3 Responsive Foundation

The viewport meta tag already exists at `main.py:120`. Add mobile-first media queries to the `<style>` block.

> **[REVIEW FIX]** Responsive-designer: `.form-inline` CSS MUST be mobile-first. The base (no media query) layout is column (stacked). The `@media (min-width: 768px)` query switches to row. Never use `max-width` breakpoints — that is desktop-first and breaks the cascade.

```css
/* [REVIEW FIX] Mobile-first: base styles ARE mobile. Media queries ADD complexity. */

/* Base (mobile, 320px+) — forms stack, full-width inputs */
.form-inline { display: flex; flex-direction: column; gap: var(--space-sm); }
.form-inline input, .form-inline select, .form-inline button { width: 100%; }
body { padding: 0 var(--space-sm); }

/* Tablet (768px+) — forms go inline, more padding */
@media (min-width: 768px) {
  .form-inline { flex-direction: row; flex-wrap: wrap; align-items: flex-end; }
  .form-inline input, .form-inline select, .form-inline button { width: auto; flex: 1; min-width: 0; }
  body { padding: 0 1rem; }
  .log-line { flex-wrap: nowrap; }  /* log lines can be single row on tablet+ */
}

/* Wide desktop (1440px+) */
@media (min-width: 1440px) {
  body { max-width: 1200px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}
```

**[REVIEW FIX]** Log line wrapping moved from Sprint 5 (P5-4) to Sprint 2.5 — mobile log lines must wrap cleanly:
```css
/* Mobile log lines (base) */
.log-line { flex-direction: column; gap: 2px; }
.log-ts, .log-badge, .log-svc { font-size: 0.75em; }
/* Reset to row on tablet+ (in the 768px media query above) */
```

### 1.4 Nav Active State

Add a `current_path` parameter to `_page()` to highlight the active nav link.

**Modify:** `_page()` at `main.py:115` — add `path: str = ""` parameter.

```python
def _page(title: str, body: str, path: str = "") -> str:
```

In the nav, each link gets a conditional `class="active"`:

```python
f'<a href="/stats" {"class=active" if path == "/stats" else ""}>Stats</a>'
```

Every route that calls `_page()` must pass its path. There are **9 call sites** (corrected from 6):
- `_stats_form()` (line 156) — path="/stats"
- `show_players()` (line 403, empty state) — path="/players"
- `show_players()` (line 458) — path="/players"
- `show_streams()` (line 498) — path="/streams"
- `show_lcu()` (lines 604, 638) — path="/lcu"
- `show_logs()` (lines 780, 789, 836) — path="/logs"

> **[REVIEW FIX]** QA-tester found line 403 (players empty state) was missing, and line 793 was wrong (actual `_page()` call is at 789).

### 1.5 HTML Helper: `_badge()`

Add a reusable badge helper function after `_page()`:

```python
_BADGE_VARIANTS = frozenset({"success", "error", "warning", "info", "muted"})

def _badge(variant: str, text: str) -> str:
    """Render a status badge. text is raw HTML (caller must escape user data).
    variant: success|error|warning|info|muted."""
    assert variant in _BADGE_VARIANTS, f"Invalid badge variant: {variant}"
    return f'<span class="badge badge--{variant}">{text}</span>'
```

> **[REVIEW FIX]** Security/developer agents: variant is validated against a whitelist to prevent class injection. Text is intentionally raw HTML (callers use HTML entities like `&#10003;`).

Replace ad-hoc status HTML across all routes with `_badge()` calls:
- `_stats_table()` line 193: `<span class="success">&#10003;</span>` becomes `_badge("success", "&#10003; Verified")`
- `show_streams()` line 481-483: system status becomes `_badge("success", "&#10003; Running")` or `_badge("error", "&#10007; HALTED")`
- `_lcu_stats_section()` line 261: `<span class="unverified">&#9888;</span>` becomes `_badge("warning", "&#9888; Unverified")`
- `show_lcu()` line 600 and 628: same pattern
- **[REVIEW FIX]** `_match_history_html()` line 524: `<span class="success">Win</span>` / `<span class="error">Loss</span>` becomes `_badge("success", "Win")` / `_badge("error", "Loss")`

### 1.6 Table Scroll Wrappers

Wrap ALL `<table>` elements in `<div class="table-scroll">` for mobile horizontal scrolling.

**Routes with tables to wrap:**
- `_stats_table()` (line 194, 196-197, 199-200) — 3 tables (stats, champs, roles)
- `_lcu_stats_section()` (lines 264-267, 269-272) — 2 tables
- `show_players()` (line 452-456) — 1 table
- `show_streams()` (line 493-496) — 1 table
- `show_lcu()` (lines 633-636) — 1 table
- `_match_history_html()` (line 546) — 1 table

### 1.7 XSS Fix: Remove Inline onclick Handlers

**[REVIEW FIX — SECURITY]** Moved from Sprint 3 to Sprint 1. This is a security defect, not a feature — it must ship in the first sprint.

`_match_history_section()` and `_match_history_html()` embed user-controlled values (`region`, `riot_id`) in inline `onclick` handlers via `html.escape()`. HTML entity decoding happens before JS execution, allowing string breakout.

**Required fix:** Replace ALL inline `onclick` handlers with `data-*` attributes + event delegation:

```html
<a href="..." data-puuid="..." data-region="..." data-riot-id="..." data-page="0"
   class="load-matches">Load match history</a>
```

```javascript
document.addEventListener('click', function(e) {
  var el = e.target.closest('.load-matches');
  if (!el) return;
  e.preventDefault();
  loadMatches(el.dataset.puuid, el.dataset.region, el.dataset.riotId, +el.dataset.page);
});
```

This eliminates all inline JS and prevents JS-context XSS. After this change, `grep onclick= main.py` must return zero matches in match history code.

### 1.8 Tests That Will Break

The following existing tests WILL break due to Sprint 1 changes. They must be updated alongside the code, not after.

| Change | Affected Tests | What Breaks |
|--------|---------------|-------------|
| `_page()` signature gains `path: str = ""` parameter | All `TestPage` tests that assert on `_page()` output | Return value HTML structure changes (nav active class, meta tags, CSS) |
| `_stats_form()` gains `selected_region` parameter | Form-related tests that assert on `_stats_form()` output | Form HTML changes (region `<option selected>` attribute) |
| Inline `onclick` replaced with `data-*` attributes | Tests that assert on match history HTML containing `onclick=` | The `onclick` attribute no longer exists; must assert `data-puuid`, `data-region`, etc. instead |
| CSS extraction to `_CSS` module-level constant | Tests that check for specific inline `<style>` content | Style content is now in `_CSS`, not inlined per-call |

> **[REVIEW FIX]** Tester agent: enumerate breaking tests per sprint so developers do not discover failures after the fact. Update tests in the same commit as the code change.

### Acceptance Criteria — Sprint 1

| ID | Criterion | Verification |
|----|-----------|-------------|
| S1-1 | `_page()` CSS uses `:root` custom properties for all colors, fonts, spacing | Grep for hardcoded `#f0f0f0`, `#ccc`, `green`, `red`, `orange`, `#b8860b` in `main.py` — zero matches |
| S1-2 | Dark theme: `--color-bg: #1a1a2e`, `--color-surface: #16213e` applied | Visual check: dark background in browser |
| S1-3 | Font stack is `'Fira Code', 'JetBrains Mono', 'Cascadia Code', monospace` | Check `_page()` CSS |
| S1-4 | All `<table>` wrapped in `<div class="table-scroll">` | Grep `<table` in `main.py` — each preceded by `table-scroll` div |
| S1-5 | Form inputs have `font-size: 16px` and `min-height: 44px` | Check CSS |
| S1-6 | Nav uses `display: flex` with `overflow-x: auto` | Check CSS |
| S1-7 | Active nav link highlighted via `.active` class | Visual check: current page link has bottom border |
| S1-8 | `_badge()` helper exists and is used for all status indicators | Grep for `_badge(` — at least 5 call sites |
| S1-9 | `.card`, `.badge`, `.stat`, `.gauge`, `.form-inline`, `.banner`, `.empty-state` classes all defined | Check CSS |
| S1-10 | `max-width` is fluid: `min(900px, 100% - 2rem)` | Check CSS |
| S1-11 | Mobile media query stacks form vertically below 768px | Check CSS |
| S1-12 | Wide desktop media query expands to 1200px at 1440px+ | Check CSS |
| S1-13 | All existing unit tests pass after CSS changes (updated alongside code) | `cd lol-pipeline-ui && python -m pytest` |
| S1-14 | Match history uses `data-*` attributes (no inline onclick) | Grep for `onclick=` in match history code — zero matches |
| S1-15 | Tests updated for `_page()` signature change, `_stats_form()` region param, onclick removal | Test suite green with no skips |

---

## Sprint 2 — Page-by-Page Redesign

Rebuild each route using the design system components from Sprint 1.

### 2.1 `/stats` — Player Stats (lines 288-382)

**Desktop layout (1024px):**
```
┌─────────────────────────────────────────────────────┐
│  Player Stats                                       │
├─────────────────────────────────────────────────────┤
│  ┌─ form-inline ──────────────────────────────────┐ │
│  │ Riot ID: [GameName#Tag    ] Region: [na1▼] [Go]│ │
│  └────────────────────────────────────────────────┘ │
│                                                     │
│  Stats for Faker#KR1 (PUUID: abc123...)             │
│                                                     │
│  ┌─ card: Verified (Riot API) [badge:success] ───┐  │
│  │  ┌──────────────┬──────────┐                  │  │
│  │  │ Stat         │ Value    │                  │  │
│  │  ├──────────────┼──────────┤                  │  │
│  │  │ total_games  │ 150      │                  │  │
│  │  │ total_wins   │ 85       │                  │  │
│  │  │ win_rate     │ 0.567    │                  │  │
│  │  │ avg_kills    │ 7.2      │                  │  │
│  │  │ ...          │ ...      │                  │  │
│  │  └──────────────┴──────────┘                  │  │
│  │                                                │  │
│  │  ┌─ Top Champions ─┐  ┌─ Roles ─────────────┐ │  │
│  │  │ Ahri     │ 35   │  │ MID    │ 80         │ │  │
│  │  │ Syndra   │ 28   │  │ TOP    │ 40         │ │  │
│  │  └──────────┴──────┘  └────────┴────────────┘ │  │
│  └────────────────────────────────────────────────┘  │
│                                                     │
│  ┌─ card: Unverified (LCU) [badge:warning] ──────┐  │
│  │  Total: 200  Wins: 110  Losses: 90            │  │
│  │  By Mode: CLASSIC 150, ARAM 50                │  │
│  └────────────────────────────────────────────────┘  │
│                                                     │
│  ┌─ Match History ────────────────────────────────┐  │
│  │  [Load match history]  (lazy-load link)        │  │
│  └────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────┘
```

**Mobile layout (320px):**
```
┌──────────────────────┐
│ Player Stats         │
├──────────────────────┤
│ Riot ID:             │
│ [GameName#Tag      ] │
│ Region:              │
│ [na1           ▼   ] │
│ [   Look Up        ] │
│                      │
│ card: Verified       │
│ ┌─table-scroll─────┐ │
│ │ Stat    │ Value  │←scroll
│ │ ...     │ ...    │ │
│ └─────────────────┘  │
│                      │
│ card: Unverified     │
│ (same, scrollable)   │
└──────────────────────┘
```

**Changes to `_stats_form()` (line 154):**
- Wrap form in `<form class="form-inline" method="get" action="/stats">`
- Remove `size="30"` from input
- Add `class="input"` to input, `class="select"` to select, `class="btn"` to button
- Pass `path="/stats"` to `_page()`

**Changes to `_stats_table()` (line 181):**
- Wrap entire output in `<div class="card">`
- Sort stats semantically instead of alphabetically (line 188): group by `total_*`, then `avg_*`, then `kda`/`win_rate`
- Add `<div class="table-scroll">` around each table
- At 1440px+, use CSS grid to put champions and roles side-by-side

**Stats semantic ordering** — replace `sorted(stats.items())` at line 188 with a custom sort:
```python
# [REVIEW FIX] UI-UX agent: added total_kills/deaths/assists (present in actual data)
_STATS_ORDER = [
    "total_games", "total_wins", "total_losses", "win_rate",
    "total_kills", "total_deaths", "total_assists",
    "avg_kills", "avg_deaths", "avg_assists", "kda",
]

def _sort_stats(stats: dict[str, str]) -> list[tuple[str, str]]:
    ordered = [(k, stats[k]) for k in _STATS_ORDER if k in stats]
    remaining = [(k, v) for k, v in sorted(stats.items()) if k not in _STATS_ORDER]
    return ordered + remaining
```

**[REVIEW FIX]** UI-UX agent: `win_rate` is stored as a decimal (e.g., `0.5432`) but must be displayed as a percentage (e.g., `54.32%`). Add explicit formatting when rendering the stats table:

```python
def _format_stat_value(key: str, value: str) -> str:
    """Format a stat value for display. win_rate is multiplied by 100 and shown as %."""
    if key == "win_rate":
        try:
            return f"{float(value) * 100:.2f}%"
        except ValueError:
            return value
    return value
```

Apply `_format_stat_value()` in the stats table rendering loop so that `win_rate` of `0.5432` renders as `54.32%`.

**Changes to `_lcu_stats_section()` (line 250):**
- Wrap in `<div class="card">`
- Use `_badge("warning", "&#9888; Unverified")` in heading

**Changes to `_match_history_section()` (line 204):**
- Wrap in `<div class="card">`
- Add a styled loading placeholder: `<p class="empty-state">Loading match history...</p>`

**Empty/loading states for `/stats`:**
- "No verified API stats yet" (line 377): wrap in `<div class="empty-state">` with guidance text: "Pipeline is processing. Stats appear after Crawler + Fetcher + Parser + Analyzer complete."
- Auto-seed message (line 363): use `<div class="banner banner--warning">` instead of plain `<p>`
- System halted (line 327): use `<div class="banner banner--error">` with fix instruction: "Run `just admin system-resume` to clear."

### 2.2 `/players` — Player List (lines 388-458)

**Desktop layout:**
```
┌─────────────────────────────────────────────────────┐
│  Players (247 total, page 1)                        │
├─────────────────────────────────────────────────────┤
│  ┌─table-scroll─────────────────────────────────┐   │
│  │ Riot ID          │ Region │ Seeded           │   │
│  ├──────────────────┼────────┼──────────────────┤   │
│  │ Faker#KR1   →    │ kr     │ 2026-03-19       │   │
│  │ Chovy#KR2   →    │ kr     │ 2026-03-18       │   │
│  │ ...              │ ...    │ ...              │   │
│  └──────────────────┴────────┴──────────────────┘   │
│                                                     │
│  ← Prev    Page 1 of 10    Next →                   │
└─────────────────────────────────────────────────────┘
```

**Changes to `show_players()` (line 388):**
- Pass `path="/players"` to `_page()`
- Wrap table in `<div class="table-scroll">`
- Empty state (line 403): use `<div class="empty-state">` with "No players seeded yet. Use `just seed GameName#Tag` or visit /stats to auto-seed."
- Pagination links: style as buttons with `class="btn"` and proper touch targets
- Show full ISO timestamp (not truncated to 10 chars at line 438)
- Add page count display: "Page X of Y"

### 2.3 `/streams` — Pipeline Health (lines 461-498)

**Desktop layout:**
```
┌─────────────────────────────────────────────────────┐
│  Stream Depths                                      │
├─────────────────────────────────────────────────────┤
│  [banner: ✓ System running]   OR                    │
│  [banner: ✗ HALTED — run: just admin system-resume] │
│                                                     │
│  Priority in-flight: [stat: 3]                      │
│                                                     │
│  ┌─table-scroll─────────────────────────────────┐   │
│  │ Stream              │ Depth │ Status          │   │
│  ├─────────────────────┼───────┼─────────────────┤   │
│  │ stream:puuid        │     0 │ [badge:success] │   │
│  │ stream:match_id     │    12 │ [badge:success] │   │
│  │ stream:parse        │   150 │ [badge:warning] │   │
│  │ stream:analyze      │     0 │ [badge:success] │   │
│  │ stream:dlq          │     3 │ [badge:error]   │   │
│  │ stream:dlq:archive  │    42 │ [badge:muted]   │   │
│  │ delayed:messages    │     1 │ [badge:info]    │   │
│  └─────────────────────┴───────┴─────────────────┘   │
│                                                     │
│  Auto-refresh: every 5s  [Pause]                    │
└─────────────────────────────────────────────────────┘
```

**Changes to `show_streams()` (line 461):**
- Pass `path="/streams"` to `_page()`
- Replace `<p class="error">` system status with `<div class="banner banner--error">` or `<div class="banner banner--success">`
- Add a Status column with color-coded badges based on depth thresholds:
  - `depth == 0`: `badge--success` "idle"
  - `depth < 100`: `badge--success` "ok"
  - `depth < 1000`: `badge--warning` "busy"
  - `depth >= 1000`: `badge--error` "backlog"
  - DLQ `depth > 0`: `badge--error` showing count
  - DLQ `depth == 0`: `badge--success` "clear"
- Display `system:priority_count` as a `<div class="stat">` counter
- Wrap table in `<div class="table-scroll">`

### 2.4 `/lcu` — LCU Match History (lines 594-638)

**Changes to `show_lcu()` (line 594):**
- Pass `path="/lcu"` to `_page()`
- Empty state (lines 599-603): use `<div class="empty-state">` with styled `<code>` for the `just lcu` command
- Wrap table in `<div class="table-scroll">`
- Link player names to `/stats?riot_id=...` where possible (line 610 — if `riot_id` in match data, build link)
- Use `_badge("warning", "&#9888; Unverified")` in heading

### 2.5 `/logs` — Log Viewer (lines 775-836)

**Desktop layout:**
```
┌─────────────────────────────────────────────────────┐
│  Logs                                               │
├─────────────────────────────────────────────────────┤
│  [Pause]  Services: crawler, fetcher, ...           │
│           Last 50 lines, auto-refresh 2s            │
│                                                     │
│  ┌─ log-wrap (dark theme) ────────────────────────┐ │
│  │ 2026-03-19 12:00 [ERROR] crawler  msg...       │ │
│  │ 2026-03-19 12:00 [INFO]  fetcher  msg...       │ │
│  │ 2026-03-19 12:01 [WARN]  parser   msg...       │ │
│  │ ...                                            │ │
│  └────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

**Changes to `_LOG_CSS` (lines 652-677):**
Replace light-theme log colors with dark-theme equivalents:

| Current (light) | Target (dark) | Purpose |
|-----------------|---------------|---------|
| `.log-critical { background: #ffe0e0; }` | `.log-critical { background: rgba(255,65,54,0.15); }` | Critical line highlight |
| `.log-error { background: #fff0f0; }` | `.log-error { background: rgba(255,65,54,0.08); }` | Error line highlight |
| `.log-warning { background: #fffbe6; }` | `.log-warning { background: rgba(255,220,0,0.08); }` | Warning line highlight |
| `.log-debug { color: #999; }` | `.log-debug { color: var(--color-muted); }` | Debug de-emphasis |
| `.log-line { border-bottom: 1px solid #f0f0f0; }` | `.log-line { border-bottom: 1px solid var(--color-border); }` | Line separator |
| `.log-ts { color: #aaa; }` | `.log-ts { color: var(--color-muted); }` | Timestamp |
| `.log-svc { color: #669; }` | `.log-svc { color: var(--color-info); }` | Service name |
| `.log-extra { color: #666; }` | `.log-extra { color: var(--color-muted); }` | Extra fields |

**Badge colors (dark-safe):**
| Current | Target |
|---------|--------|
| `.log-badge.log-critical { background: #c00; }` | Keep (good contrast on dark) |
| `.log-badge.log-error { background: #e33; }` | Keep |
| `.log-badge.log-warning { background: #e80; }` | Keep |
| `.log-badge.log-debug { background: #bbb; }` | `.log-badge.log-debug { background: #555; }` |
| `.log-badge.log-info { background: #888; }` | `.log-badge.log-info { background: var(--color-info); }` |

**Changes to `show_logs()` (line 775):**
- Pass `path="/logs"` to `_page()`
- Replace `#pause-btn.paused { background: #e33; }` with `background: var(--color-error);`
- Empty state (lines 787-793): use `<div class="empty-state">`
- LOG_DIR not configured (lines 779-782): use `<div class="empty-state">` with guidance

### 2.6 `/stats/matches` — Match History Fragment (lines 551-591)

**Changes to `_match_history_html()` (line 504):**
- Wrap table in `<div class="table-scroll">`
- Empty state (line 514): use `<div class="empty-state">` instead of plain `<p>`
- "Load more" link (line 541-543): style as `<button class="btn">` instead of plain `<a>`
- Loading state in JS (line 224): `container.innerHTML = '<div class="empty-state">Loading matches...</div>';`

### Acceptance Criteria — Sprint 2

| ID | Criterion | Verification |
|----|-----------|-------------|
| S2-1 | All routes pass `path=` to `_page()` and active nav link is highlighted | Visual check all 5 pages |
| S2-2 | Stats table sorted semantically (totals, then averages, then derived) | Check `_sort_stats()` function exists; `sorted(stats.items())` is removed |
| S2-3 | All status messages use `<div class="banner banner--*">` | Grep `class="error"` on `<p>` tags — zero matches for status messages |
| S2-4 | All empty states use `<div class="empty-state">` with guidance text | Check each route's empty path |
| S2-5 | All `<table>` wrapped in `<div class="table-scroll">` | Grep confirms |
| S2-6 | `/streams` has Status column with color-coded badges | Visual check |
| S2-7 | `/streams` system status uses banner component | Check HTML output |
| S2-8 | `/lcu` player names link to `/stats` | Check HTML output |
| S2-9 | Log viewer CSS uses dark-theme colors (no `#ffe0e0`, `#fff0f0`, `#fffbe6`) | Grep `_LOG_CSS` |
| S2-10 | Players page shows full ISO timestamp, page count | Check output |
| S2-11 | Match history "Load more" styled as button | Check JS/HTML |
| S2-12 | `_badge()` helper used in all routes | At least 8 call sites |
| S2-13 | All existing tests pass | Full test suite |

---

## Sprint 3 — Interactive Features

### 3.1 Auto-Refresh on `/streams`

Copy the AJAX polling pattern from `/logs` (lines 800-824).

**Add to `show_streams()` (after the table HTML):**
- Add a `/streams/fragment` endpoint that returns just the table + status HTML
- Add JS that polls `/streams/fragment` every 5 seconds
- Add a Pause/Resume button (same pattern as `/logs`)
- The priority count display also refreshes

**New endpoint:**
```python
@app.get("/streams/fragment", response_class=HTMLResponse)
async def streams_fragment(request: Request) -> HTMLResponse:
    """Return just the streams table + status for AJAX polling."""
    # ... same Redis queries as show_streams() ...
    # ... return only the inner HTML (no _page() wrapper) ...
```

### 3.2 Loading States / Spinners

Add a CSS-only spinner (no images, no external deps):

```css
.spinner { display: inline-block; width: 16px; height: 16px;
           border: 2px solid var(--color-border); border-top-color: var(--color-info);
           border-radius: 50%; animation: spin 0.6s linear infinite; }
@keyframes spin { to { transform: rotate(360deg); } }
```

Apply to:
- Match history loading (line 224): `<div class="empty-state"><span class="spinner"></span> Loading matches...</div>`
- Streams auto-refresh: show spinner next to "Auto-refresh" text while fetching

### 3.3 Empty States with Guidance

Create a helper function:

```python
def _empty_state(title: str, guidance: str) -> str:
    """Render an empty-state message. Both params are raw HTML — callers MUST
    pre-escape any dynamic content with html.escape()."""
    return f'<div class="empty-state"><p><strong>{title}</strong></p><p>{guidance}</p></div>'
```

> **[REVIEW FIX]** Security/tester: docstring documents the raw-HTML contract. All current callers use hardcoded strings with `<code>` and `<a>` tags.

Apply to every route's empty/no-data path:
- `/stats` no riot_id: show the form (already handled)
- `/stats` no data + auto-seed: `_empty_state("Pipeline processing", "Your player was auto-seeded. Refresh shortly, or check <a href=\"/streams\">Streams</a> for pipeline progress.")`
- `/stats` no data + already seeded: `_empty_state("Still processing", "Check back soon or view <a href=\"/streams\">Streams</a> for pipeline status.")`
- `/players` empty: `_empty_state("No players yet", "Look up a player on the <a href=\"/stats\">Stats</a> page to auto-seed, or use <code>just seed GameName#Tag</code>.")`
- `/streams`: never empty (always shows stream keys)
- `/lcu` empty: `_empty_state("No LCU data collected", "Run <code>just lcu</code> with the League client open. Data reloads automatically every few minutes, or restart the UI to reload immediately.")`
- `/logs` no LOG_DIR: `_empty_state("LOG_DIR not configured", "Add <code>LOG_DIR=/path/to/logs</code> to docker-compose.yml.")`
- `/logs` no files: `_empty_state("No log files found", "Services may not have started yet. Check <code>docker compose ps</code>.")`
- `/stats/matches` empty: `_empty_state("No match history", "The pipeline is still processing. Matches appear after Fetcher + Parser complete.")`

> **[REVIEW FIX]** Content-writer: removed "about a minute" (unreliable timing), added /streams link, fixed LCU guidance to mention auto-reload, verified `just seed` recipe exists in Justfile.

### 3.4 Global Halt Banner

**[REVIEW FIX]** UI-UX agent: system halt should show on ALL pages, not just /stats and /streams.

Add a `system:halted` check to `_page()` itself. Pass the Redis connection through, or check once per request and inject the banner:

```python
def _page(title: str, body: str, path: str = "", halted: bool = False) -> str:
    halt_banner = (
        '<div class="banner banner--error">System is HALTED. '
        'Run <code>just admin system-resume</code> to clear.</div>'
        if halted else ""
    )
    # ... inject halt_banner after nav ...
```

Every route must pass `halted=bool(await r.get("system:halted"))` to `_page()`. The `/streams` page already reads this value.

### 3.5 Priority Queue Indicators

**`/streams` page:** Already has `system:priority_count` display (lines 486-492). Enhance with:
- Display as a `<div class="stat">` counter with label "Priority In-Flight"
- Color the value: `--color-warning` if >0, `--color-muted` if 0

**`/stats` page:** When viewing a player, check if `player:priority:{puuid}` exists:
```python
priority_key = await r.get(f"player:priority:{puuid}")
if priority_key:
    priority_html = _badge("warning", "&#9733; Priority")
else:
    priority_html = ""
```
Display the badge next to the player name heading.

### 3.6 *(Moved to Sprint 1.7)*

The XSS onclick fix was moved to Sprint 1.7 — it is a security defect and must ship first. See section 1.7 above.

### 3.7 Better Error Messages

Replace generic error text with specific guidance per exception type:

| Exception | Current | Target |
|-----------|---------|--------|
| `NotFoundError` | "Player not found: Name#Tag" | `<div class="banner banner--error">Player not found: Name#Tag. Double-check the spelling and tag (case-sensitive).</div>` |
| `AuthError` | "Riot API error: ..." | `<div class="banner banner--error">Riot API authentication failed. Check your RIOT_API_KEY. <a href="https://developer.riotgames.com">Get a new key</a>.</div>` |
| `RateLimitError` | "Riot API error: ..." | `<div class="banner banner--warning">Rate limit exceeded. Try again in a few seconds.</div>` |
| `ServerError` | "Riot API error: ..." | `<div class="banner banner--warning">Riot API is temporarily unavailable. Try again later.</div>` |

> **[REVIEW FIX]** Content-writer: "tagline" changed to "tag" (matches player terminology).

**Modify:** `show_stats()` exception handling block (lines 311-314). Split the grouped `except (AuthError, RateLimitError, ServerError)` into separate `except` blocks for distinct messages.

### 3.8 Region Dropdown Preserving Selection

Currently the region dropdown always defaults to `na1`. Preserve the user's selection.

**Modify:** `_stats_form()` (lines 163-172) — accept `selected_region: str = "na1"` parameter:

```python
def _stats_form(
    msg: str = "", css_class: str = "", stats_html: str = "",
    selected_region: str = "na1", path: str = "/stats",
) -> str:
```

For each `<option>`, add `selected` if it matches:
```python
f'<option value="{region}" {"selected" if region == selected_region else ""}>{region}</option>'
```

Pass `region` from `show_stats()` (line 291) through to `_stats_form()`.

### 3.9 `show_stats()` Refactor

**[REVIEW FIX]** DevEx agent: `show_stats()` already needs `noqa: PLR0911`. With Sprint 3 additions (priority badge, distinct error handling), it will breach complexity limits. Refactor into:

```python
async def _resolve_puuid(r, riot: RiotClient, game_name: str, tag_line: str, region: str) -> str:
    """Lookup or cache PUUID. Raises NotFoundError/AuthError/etc."""
    ...

async def _auto_seed(r, cfg: Config, puuid: str, game_name: str, tag_line: str, region: str) -> str:
    """Publish to stream:puuid, set priority, record player. Returns status HTML."""
    ...

@app.get("/stats", response_class=HTMLResponse)
async def show_stats(request: Request) -> HTMLResponse:
    # Orchestrator: calls _resolve_puuid, _auto_seed, builds response
    ...
```

### Acceptance Criteria — Sprint 3

| ID | Criterion | Verification |
|----|-----------|-------------|
| S3-1 | `/streams` auto-refreshes every 5s with AJAX polling. On fetch error, previous content preserved (not replaced) | Browser network tab; disconnect test |
| S3-2 | `/streams` has Pause/Resume button | Visual check |
| S3-3 | CSS spinner visible during fetch, replaced by content on success, replaced by error on failure | Check CSS animation and JS states |
| S3-4 | Every route has a styled empty state (not bare `<p>` text) | Check each route with no data |
| S3-5 | `system:priority_count` displayed as a stat counter on `/streams` | Visual check |
| S3-6 | Priority badge appears on `/stats` for players with active priority key | Unit test with mock Redis |
| S3-7 | `NotFoundError`, `AuthError`, `RateLimitError`, `ServerError` each show distinct error messages with guidance | Unit test per exception type |
| S3-8 | Region dropdown preserves selection after form submit | Submit with `euw1`, check it's still selected |
| S3-9 | `_empty_state()` helper exists and is used in all routes | Grep for `_empty_state(` — at least 6 call sites |
| S3-10 | All existing tests pass | Full test suite |
| S3-11 | Halt banner appears on ALL pages when `system:halted` is set | Unit test: mock halted, check banner in all routes |
| S3-12 | *(Moved to S1-14)* | — |
| S3-13 | `show_stats()` refactored: extracted `_resolve_puuid()` and `_auto_seed()` | Function exists; McCabe <=10 |
| S3-14 | DLQ browser route (`/dlq`) exists and shows DLQ entries | Visit `/dlq` in browser |
| S3-15 | `/streams/fragment` returns HTML fragment (no `_page()` wrapper) | Unit test: response has no `<!doctype` |

---

## Sprint 4 — Deferred Phase 7 Items

All items from Phase 7 "Explicitly Deferred to Phase 8" plus outstanding doc and infra work.

### 4.1 Docker / Infrastructure

| ID | Item | Source | Details |
|----|------|--------|---------|
| D4-1 | `docker-compose.prod.yml` | DK-6 | Create production compose with: no volume mounts, baked images, `--requirepass` for Redis, `bind 127.0.0.1`, resource limits (`mem_limit`), `restart: unless-stopped`, log driver config (`json-file` with rotation), `security_opt: [no-new-privileges:true]`, `stop_grace_period: 30s` |
| D4-2 | Integration test CI job | DK-13 | Add a CI workflow job for IT-01 through IT-07 using testcontainers. Run on push to `main` only (not on PRs — too slow). Needs `services: redis` in the workflow. |
| D4-3 | Redis ACLs (per-service users) | Phase 7 deferred | Create `redis-acl.conf` with per-service users: `crawler` (read `stream:puuid`, write `stream:match_id`), `fetcher` (read `stream:match_id`, write `stream:parse`, write `raw:match:*`), etc. Apply via `--aclfile` in prod compose. Document in `01-security.md`. |
| D4-4 | Redis `maxmemory` + eviction policy | Phase 7 deferred | Add `maxmemory 4gb` and `maxmemory-policy noeviction` to Redis config in both dev and prod compose. Document in `04-storage.md`. |
| D4-5 | Image scanning | Phase 7 deferred | Add `trivy image` scan step to CI. Run on Dockerfile changes only (path filter). Fail on CRITICAL/HIGH. |
| D4-6 | TLS reverse proxy for Web UI | Phase 7 deferred | Document Caddy/nginx reverse proxy setup for production in `01-deployment.md`. Provide example `Caddyfile`. Do NOT implement in dev compose. |
| D4-7 | Stream MAXLEN trimming | Phase 7 deferred | Add `MAXLEN ~10000` to all `XADD` calls in `publish()` (`streams.py`). Make configurable via `STREAM_MAXLEN` env var (default 10000, 0 = unlimited). |

### 4.2 Documentation Improvements

| ID | Item | Source | Details |
|----|------|--------|---------|
| D4-8 | Create `lol-pipeline-discovery/README.md` | doc-review #1 | Cover: stream consumption, idle-check algorithm, priority gating, `DISCOVERY_POLL_INTERVAL_MS`, `DISCOVERY_BATCH_SIZE`, scaling caveats |
| D4-9 | Create `lol-pipeline-lcu/README.md` | doc-review #2 | Cover: lockfile discovery, WSL2 setup, `LEAGUE_INSTALL_PATH`, `--poll-interval`, JSONL format, trust model |
| D4-10 | Create `docs/guides/03-ci-workflow.md` | doc-review #3 | Cover: CI matrix structure, job descriptions, failure interpretation, adding new services, mypy gate |
| D4-11 | Create `CONTRIBUTING.md` | doc-review #4 | Cover: branching strategy, PR process, commit conventions, required checks, full check suite, TDD flow |
| D4-12 | Fix remaining P1 doc suggestions | doc-review P1 | Standardize test count, fix naming inconsistency `discover:players`, note `docker-compose.prod.yml` status, update admin README |
| D4-13 | Fix remaining P2 doc suggestions | doc-review P2 | Consolidate env var tables, add "add to CI" to new service checklists, update design comparison section 6 |
| D4-14 | Update `09-design-comparison.md` section 6 | doc-review | Remove "we explicitly reject automatic fan-out" claim; describe Discovery's BFS-like behavior |
| D4-15 | Create `docs/architecture/10-discovery.md` | doc-review | Dedicated architecture doc for Discovery: idle-check, priority gating, fan-out, `discover:players` sorted set |

### 4.3 Test Infrastructure

| ID | Item | Source | Details |
|----|------|--------|---------|
| D4-16 | Shared test fixtures (conftest.py) | Phase 7 deferred | Create `tests/conftest.py` at repo root with shared fakeredis fixture, Config fixture, envelope factory. Each service's conftest imports from shared. Reduces duplication across 12 services. |
| D4-17 | Parallel contract test runner | Phase 7 deferred | Run contract tests in parallel across services using pytest-xdist or Justfile parallelism (`just test-contracts` recipe). Currently sequential. |
| D4-18 | Integration tests IT-08 through IT-11 for priority | Phase 7 deferred | Write integration tests for the weighted priority queue: IT-08 (priority seed pauses discovery), IT-09 (priority key TTL expiry), IT-10 (atomic counter consistency), IT-11 (DLQ priority preservation). |
| D4-19 | Coverage enforcement in CI | doc-review | Add `pytest --cov --cov-fail-under=80` to CI for all services, `--cov-fail-under=90` for common. |

### Acceptance Criteria — Sprint 4

| ID | Criterion | Verification |
|----|-----------|-------------|
| S4-1 | `docker-compose.prod.yml` exists with all required hardening | File exists; `docker compose -f docker-compose.prod.yml config` validates |
| S4-2 | Integration test CI job runs IT-01 through IT-07 | CI workflow includes `integration-test` job |
| S4-3 | Redis ACL config exists with per-service users | `redis-acl.conf` exists; documented in `01-security.md` |
| S4-4 | `maxmemory` configured in Redis | Check compose files |
| S4-5 | Discovery and LCU READMEs exist | Files exist with >50 lines each |
| S4-6 | CI workflow documented in `docs/guides/03-ci-workflow.md` | File exists |
| S4-7 | `CONTRIBUTING.md` exists | File exists with branching, PR, TDD sections |
| S4-8 | XADD calls include MAXLEN | Grep `XADD` or `publish` for maxlen parameter |
| S4-9 | Shared test fixtures reduce per-service conftest duplication | Check common fixture imports |
| S4-10 | Coverage CI gates exist (80% services, 90% common) | Check CI workflow |
| S4-11 | All new docs reviewed for accuracy against code | Cross-reference check |
| S4-12 | All existing tests pass | Full test suite |

---

## Sprint 5 — Polish

### 5.1 Favicon

Add a simple SVG favicon inline in `_page()` `<head>`. No external file needed.

**Add to `_page()` at line 121 (after viewport meta):**
```html
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'><rect width='32' height='32' rx='4' fill='%231a1a2e'/><text x='50%25' y='55%25' dominant-baseline='middle' text-anchor='middle' fill='%232ecc40' font-family='monospace' font-size='20'>L</text></svg>">
```

This renders a dark rounded square with a green "L" (for LoL) — zero network requests, works offline.

### 5.2 Stats Sorting by Importance

Already designed in Sprint 2's `_sort_stats()` function. Verify it is applied and covers edge cases:
- Stats keys that are unknown (not in `_STATS_ORDER`) sort alphabetically after known keys
- Empty stats dict returns empty list
- Add unit test for `_sort_stats()`

### 5.3 DLQ Browser in Web UI

Add a new `/dlq` route that displays DLQ entries in a table, replacing the need to use `just admin dlq list`.

**New route: `/dlq`**

```python
from lol_pipeline.models import DLQEnvelope  # [REVIEW FIX] developer: use typed model

@app.get("/dlq", response_class=HTMLResponse)
async def show_dlq(request: Request) -> HTMLResponse:
    r = request.app.state.r
    entries = await r.xrange("stream:dlq", count=100)
    if not entries:
        return HTMLResponse(_page("Dead Letter Queue", _empty_state(
            "DLQ is empty",
            "No failed messages. The pipeline is healthy."
        ), path="/dlq"))

    rows = ""
    for entry_id, fields in entries:
        # [REVIEW FIX] developer: deserialize via DLQEnvelope, not raw field access
        dlq = DLQEnvelope.from_redis_fields(fields)
        failure_code = html.escape(dlq.failure_code)
        source = html.escape(dlq.original_stream or "?")
        attempts = html.escape(str(dlq.dlq_attempts))
        service = html.escape(dlq.failed_by or "?")
        # [REVIEW FIX] developer: truncate BEFORE escaping to avoid splitting HTML entities
        raw_payload = dlq.payload[:80]
        payload_preview = html.escape(raw_payload)
        if len(dlq.payload) > 80:
            payload_preview += "..."  # [REVIEW FIX] content-writer: indicate truncation
        safe_id = html.escape(entry_id)  # [REVIEW FIX] web-designer: escape entry_id
        rows += (
            f"<tr><td>{safe_id}</td><td>{failure_code}</td>"
            f"<td>{source}</td><td>{attempts}</td>"
            f"<td>{service}</td><td>{payload_preview}</td></tr>"
        )

    body = f"""
    <h2>Dead Letter Queue</h2>
    <p>Showing up to 100 entries.
       Use <code>just admin dlq replay &lt;id&gt;</code> to replay a failed message.</p>
    <div class="table-scroll">
    <table>
      <tr><th>Entry ID</th><th>Failure Code</th><th>Source</th>
          <th>Attempts</th><th>Service</th><th>Payload</th></tr>
      {rows}
    </table>
    </div>
    """
    return HTMLResponse(_page("Dead Letter Queue", body, path="/dlq"))
```

> **[REVIEW FIX]** Developer: DLQ fields accessed via `DLQEnvelope.from_redis_fields()` instead of raw dict. Content-writer: "Failure" -> "Failure Code", "Failed By" -> "Service", "to retry" -> "to replay a failed message", page title "DLQ" -> "Dead Letter Queue". Web-designer: `entry_id` escaped.

**Update nav** in `_page()` — always show DLQ link with `title` for accessibility:
```html
<a href="/dlq" title="Dead Letter Queue">DLQ</a>
```

> **[REVIEW FIX]** UI-UX/content-writer: always show DLQ link (not conditional), add `title` attribute to expand abbreviation. Place after Streams in nav: `Stats | Players | Streams | DLQ | LCU | Logs`.

### 5.4 Admin CLI `--json` Flag

**Deferred to Phase 9.** The `--json` flag is CLI infrastructure, not UI work. Keeping it here would blur the FACELIFT scope.

### 5.5 Dark Theme Log Viewer Colors

Already fully specified in Sprint 2, section 2.5. This sprint verifies they are correctly applied and adds any remaining polish:
- Verify contrast ratios meet WCAG AA (4.5:1 for text)
- Verify badge colors are readable against dark line backgrounds
- Adjust if needed: `rgba(255,65,54,0.15)` critical background must contrast with `#e0e0e0` text

### 5.6 Additional Polish Items

| ID | Item | Details |
|----|------|---------|
| P5-1 | Pagination touch targets | Ensure pagination buttons on `/players` and match history "Load more" have `min-height: 44px` |
| P5-2 | System halted banner with fix instruction | Everywhere the halted banner appears, include: "Run `just admin system-resume` to clear." |
| P5-3 | Code-copy button on LCU empty state | Add a copy-to-clipboard button next to `<code>just lcu</code>` using `data-copy="just lcu"` + event delegation (consistent with Sprint 1.7 XSS fix — no inline `onclick`). JS listener: `document.addEventListener('click', e => { var btn = e.target.closest('[data-copy]'); if (btn) navigator.clipboard.writeText(btn.dataset.copy); })` |
| P5-4 | Log line wrap on mobile | Ensure `flex-wrap: wrap` on `.log-line` so timestamp+badge wrap cleanly above message on narrow screens |
| P5-5 | Player search/filter on `/players` | Add a client-side JS filter input above the table that hides non-matching rows |
| P5-6 | Wide-screen layout at 1440px+ | At 1440px, expand `max-width` to 1200px; on `/stats`, place champions and roles tables side-by-side using CSS grid |

### Acceptance Criteria — Sprint 5

| ID | Criterion | Verification |
|----|-----------|-------------|
| S5-1 | Favicon appears in browser tab | Visual check |
| S5-2 | Stats table sorted by importance (totals first) | Visual check + unit test |
| S5-3 | `/dlq` route exists and displays DLQ entries | Visit `/dlq` in browser |
| S5-4 | DLQ empty state shows "pipeline is healthy" | Visit `/dlq` with empty DLQ |
| S5-5 | *(Deferred to Phase 9)* | — |
| S5-6 | Log viewer dark theme has no light-theme artifacts | Visual check: no `#ffe0e0`, `#fff0f0`, `#fffbe6` backgrounds |
| S5-7 | All pagination controls have 44px+ touch targets | Check CSS |
| S5-8 | LCU empty state has code-copy functionality | Click `just lcu` code, verify clipboard |
| S5-9 | `/players` has client-side search filter | Type in filter, rows hide |
| S5-10 | Nav includes DLQ link | Check `_page()` nav |
| S5-11 | All existing tests pass | Full test suite |
| S5-12 | New tests added for: `_sort_stats()`, `_format_stat_value()`, DLQ route, `_empty_state()`, `_badge()`, priority badge, data-* XSS fix | At least 10 new unit tests |

---

## Phase 8 Definition of Done

Phase 8 is **complete** when ALL of the following are true:

1. Dark theme with CSS custom properties applied — zero hardcoded color literals in `_page()` CSS
2. All 6 routes redesigned with card/badge/banner/empty-state components
3. Responsive layout works at 320px, 768px, 1024px, 1440px — no horizontal overflow
4. Touch targets >= 44px on all interactive elements
5. `/streams` auto-refreshes via AJAX polling
6. Every route has styled empty/loading/error states with user guidance
7. `_LOG_CSS` uses dark-theme colors only
8. Favicon renders in browser tab
9. `/dlq` route exists and shows DLQ entries
10. ~~Admin CLI supports `--json` flag on all commands~~ *(Deferred to Phase 9)*
11. `docker-compose.prod.yml` exists with full production hardening
12. Integration test CI job runs IT-01 through IT-07
13. Redis ACL config documented and included in prod compose
14. Discovery and LCU READMEs exist
15. CI workflow, CONTRIBUTING.md documented
16. Coverage enforcement in CI (80% services, 90% common)
17. All existing tests pass + at least 20 new tests for UI components and routes
18. All documentation reviewed for accuracy against code

---

## Key Files Modified

| File | Sprints | Changes |
|------|---------|---------|
| `lol-pipeline-ui/src/lol_ui/main.py` | 1, 2, 3, 5 | CSS overhaul, component helpers, route redesigns, DLQ route, empty states, auto-refresh |
| `lol-pipeline-ui/tests/unit/test_main.py` | 1, 2, 3, 5 | Tests for `_badge()`, `_sort_stats()`, `_empty_state()`, DLQ route, priority badge |
| `lol-pipeline-admin/src/lol_admin/main.py` | *(Phase 9)* | `--json` flag deferred |
| `lol-pipeline-admin/tests/unit/test_main.py` | *(Phase 9)* | Tests for `--json` output deferred |
| `lol-pipeline-common/src/lol_pipeline/streams.py` | 4 | MAXLEN on XADD |
| `docker-compose.prod.yml` | 4 | New file — production compose |
| `redis-acl.conf` | 4 | New file — per-service ACLs |
| `.github/workflows/ci.yml` | 4 | Integration test job, coverage gates, image scanning |
| `lol-pipeline-discovery/README.md` | 4 | New file |
| `lol-pipeline-lcu/README.md` | 4 | New file |
| `docs/guides/03-ci-workflow.md` | 4 | New file |
| `CONTRIBUTING.md` | 4 | New file |
| `docs/architecture/10-discovery.md` | 4 | New file |
| `docs/architecture/09-design-comparison.md` | 4 | Update section 6 |

---

## Risk Register

| Risk | Impact | Mitigation |
|------|--------|------------|
| CSS changes break existing test assertions on HTML output | Medium | Tests check structure, not exact CSS. Run full suite after Sprint 1. |
| Dark theme readability issues (contrast) | Low | Use WCAG AA contrast checker. All text colors verified against `#1a1a2e` bg. |
| Auto-refresh on `/streams` hammers Redis | Low | 5s interval, same pattern as `/logs` (already proven). Add cache header if needed. |
| DLQ route exposes sensitive payload data | Medium | Truncate payload to 80 chars. No raw JSON dump. Sanitize with `html.escape()`. |
| Sprint 4 scope creep (deferred items expand) | High | Strict scope: only items explicitly listed in Phase 7 "Deferred" section. New items go to Phase 9. |
| Wide-screen CSS grid breaks narrow layouts | Low | Mobile-first approach: grid only activates at 1440px+. Base layout is single-column. |

---

## Summary

| Metric | Phase 7 End | Phase 8 Target |
|--------|-------------|----------------|
| CSS hardcoded colors | ~15 literals | 0 (all via CSS vars) |
| Responsive breakpoints | 0 | 4 (320, 768, 1024, 1440) |
| Empty state designs | 0 | 8+ (every route) |
| UI routes | 6 (`/`, `/stats`, `/players`, `/streams`, `/lcu`, `/logs`) | 8 (+ `/dlq`, `/streams/fragment`) |
| Auto-refresh pages | 1 (`/logs`) | 2 (+ `/streams`) |
| Component helpers | 0 | 4 (`_badge()`, `_empty_state()`, `_sort_stats()`, table-scroll wrappers) |
| New documentation files | 0 | 6 (Discovery README, LCU README, CI guide, CONTRIBUTING, Discovery arch, prod compose) |
| New tests (est.) | 0 | ~25 (UI components + DLQ route + priority badge + empty states + XSS data-attr) |
