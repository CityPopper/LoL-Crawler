# Web UI Design Reference

**Scope:** Visual system for the Web UI, terminal output, and documentation diagrams.

---

## Dark Theme Rationale

The UI targets developers monitoring a live pipeline — typically in a terminal-adjacent context,
often at night, always on a desktop. A dark theme reduces eye strain during sustained monitoring
and matches the aesthetic of the tools (terminal, editor) used alongside it. Light-colored
elements on a dark background also make status indicators (green/red/yellow) visually prominent
with minimal surrounding noise.

---

## Color Palette

Seven semantic roles. All defined as CSS custom properties in
`lol-pipeline-ui/src/lol_ui/css.py`.

```
┌──────────────┬────────────┬─────────────────────────────────────┐
│ Token        │ Value      │ Meaning                             │
├──────────────┼────────────┼─────────────────────────────────────┤
│ --color-bg   │ #141418    │ Page background                     │
│ --color-surface│ #262636  │ Cards, panels, inputs               │
│ --color-text │ #e8e8e8    │ Body copy                           │
│ --color-muted│ #7b7b8d    │ Secondary labels, timestamps        │
│ --color-border│ #3a3a50   │ Dividers, outlines                  │
│ --color-success│ #2daf6f  │ Healthy, verified, win              │
│ --color-error│ #ff4136    │ Errors, failures, halted            │
│ --color-warning│ #ffdc00  │ Warnings, unverified data           │
│ --color-info │ #5a9eff    │ Links, active nav, info actions     │
│ --color-win  │ #5383e8    │ Match win, active selection         │
│ --color-loss │ #e84057    │ Match loss                          │
└──────────────┴────────────┴─────────────────────────────────────┘
```

Color is semantic, not decorative. Green means verified/healthy. Red means halted/broken.
Yellow means degraded/unverified. Gray means context — never status. These meanings apply
across all three surfaces: Web UI, terminal output, and documentation diagrams.

---

## Typography

Two font roles:

- **Monospace** (`--font-mono`: Fira Code, JetBrains Mono, Cascadia Code) — body text, data,
  code, log lines. Monospace tables align without CSS.
- **Sans-serif** (`--font-sans`: system-ui) — compact data labels within match rows and stat
  counters, where character density matters more than code aesthetics.

Base size is 14px. Data labels drop to 12px or 11px (uppercase, letter-spaced) for table
headers. Touch targets are never smaller than 44px tall regardless of font size.

---

## Layout Philosophy

Single-column, centered, max-width capped at `min(1100px, 100% - 2rem)`. The pipeline produces
one player's data at a time; there is no dashboard that benefits from a wide multi-panel layout.
Pages are deep, not wide.

Navigation is a tab bar pinned to the top of the content column. Active page uses
`--color-win` underline. On mobile the tab bar scrolls horizontally without wrapping.

Forms use a sticky header pattern — the Riot ID search form stays visible while scrolling
through results.

---

## Component Patterns

All components are defined in `lol-pipeline-ui/src/lol_ui/`.

```
┌─────────────────────┬──────────────────────────────────────────┐
│ Component           │ Pattern                                  │
├─────────────────────┼──────────────────────────────────────────┤
│ Status badge        │ _badge(variant, text) → <span.badge>     │
│                     │ variants: success / error / warning /    │
│                     │           info / muted                   │
├─────────────────────┼──────────────────────────────────────────┤
│ Stream depth badge  │ _depth_badge(stream, depth) — thresholds │
│                     │ <100 → success, <1000 → warning, else    │
│                     │ error; DLQ: any depth > 0 → error        │
├─────────────────────┼──────────────────────────────────────────┤
│ Banner              │ .banner--error / --warning / --success   │
│                     │ Left-bordered panel for system alerts    │
├─────────────────────┼──────────────────────────────────────────┤
│ Card                │ .card — surface panel with border-radius │
├─────────────────────┼──────────────────────────────────────────┤
│ Empty state         │ _empty_state(title, body_html)           │
├─────────────────────┼──────────────────────────────────────────┤
│ Match row           │ .match-row--win / --loss — left border   │
│                     │ color + tinted background per outcome    │
├─────────────────────┼──────────────────────────────────────────┤
│ Grade badge         │ .grade--S / A / B / C / D — used for    │
│                     │ AI Score and PBI tier on champions page  │
└─────────────────────┴──────────────────────────────────────────┘
```

Components are built as Python string functions, not a template engine. The rendering layer
(`rendering.py`) and CSS (`css.py`) are the canonical sources — do not duplicate inline styles
in route files.

---

## Status Indicators Across Surfaces

The same semantic mapping applies everywhere:

```
Concept          Web UI                  Terminal / CLI
──────────────   ──────────────────────  ─────────────────────
Healthy          badge--success (green)  plain text
Error / halted   badge--error (red)      stderr text
Warning          badge--warning (yellow) stderr text
Verified data    .success span + ✓       N/A (CLI shows raw)
Unverified data  .unverified span + ⚠    N/A
```

---

## Documentation Diagram Style

All diagrams in `docs/` use Unicode box-drawing characters and stay within 80 columns.
See `README.md` and `docs/architecture/06-failure-resilience.md` for canonical examples.

Rules:
- Boxes: `┌─┐ └─┘ │ ├─┤ ┬ ┴`
- Arrows: `→ ← ↑ ↓` (directional), `─▶` (flow)
- Stream labels go on the arrow: `─stream:match_id─▶`
- State machines: boxes for states, labeled edges for transitions
- Max width: 80 columns (verify before committing)

---

## Source Files

| What | File |
|------|------|
| CSS tokens and all styles | `lol-pipeline-ui/src/lol_ui/css.py` |
| Page wrapper, badge helpers | `lol-pipeline-ui/src/lol_ui/rendering.py` |
| Nav items | `lol-pipeline-ui/src/lol_ui/css.py` (`_NAV_ITEMS`) |
| Route handlers | `lol-pipeline-ui/src/lol_ui/routes/` |
| Pipeline diagram | `README.md` lines 7-13 |
| DLQ state machine | `docs/architecture/06-failure-resilience.md` |
