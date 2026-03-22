"""UI CSS, navigation items, and favicon data."""

from __future__ import annotations

_NAV_ITEMS = [
    ("/", "Dashboard"),
    ("/stats", "Stats"),
    ("/champions", "Champions"),
    ("/matchups", "Matchups"),
    ("/players", "Players"),
    ("/streams", "Streams"),
    ("/dlq", "DLQ"),
    ("/logs", "Logs"),
]

_CSS = """
:root {
  --color-bg: #141418;
  --color-surface: #262636;
  --color-text: #e8e8e8;
  --color-muted: #7b7b8d;
  --color-border: #3a3a50;
  --color-success: #2daf6f;
  --color-error: #ff4136;
  --color-warning: #ffdc00;
  --color-info: #5a9eff;
  --color-critical: #c00;
  --color-error-bg: #cc3333;
  --color-surface2: #2e2e42;
  --color-win: #5383e8;
  --color-win-bg: rgba(83, 131, 232, 0.08);
  --color-loss: #e84057;
  --color-loss-bg: rgba(232, 64, 87, 0.06);
  --color-gold: #f4c874;
  --color-tier-s: #e89240;
  --color-rank-purple: #9e6cd9;
  --color-rank-teal: #3cbec0;
  --color-dmg-physical: #e89240;
  --color-dmg-magic: #5383e8;
  --color-dmg-true: #e8e8e8;
  --chart-stroke-width: 2;
  --icon-champ-xs: 20px;
  --chart-b0: #5383e8; --chart-b1: #3cbec0; --chart-b2: #2daf6f;
  --chart-b3: #9e6cd9; --chart-b4: #f4c874;
  --chart-r0: #e84057; --chart-r1: #e89240; --chart-r2: #ffdc00;
  --chart-r3: #ff6b6b; --chart-r4: #c0a060;
  --font-sans: system-ui, -apple-system, 'Segoe UI', sans-serif;
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
  --icon-champ-sm: 28px;
  --icon-champ-md: 48px;
  --icon-item: 28px;
}
body {
  font-family: var(--font-mono);
  font-size: var(--font-size-base);
  background: var(--color-bg);
  color: var(--color-text);
  max-width: min(1100px, calc(100% - 2rem));
  margin: 2rem auto;
  padding: 0 var(--space-sm);
  line-height: var(--line-height);
}
a { color: var(--color-info); }
h1 { border-bottom: 2px solid var(--color-border); padding-bottom: 0.5rem; }
hr { border: none; border-top: 1px solid var(--color-border); }
nav { display: flex; gap: 0; overflow-x: auto; padding-bottom: 0;
  border-bottom: 2px solid var(--color-border); margin-bottom: var(--space-md); }
nav a { white-space: nowrap; padding: var(--space-sm) var(--space-md);
  min-height: 44px; display: inline-flex; align-items: center;
  border-radius: 0; text-decoration: none; color: var(--color-muted);
  border-bottom: 2px solid transparent; margin-bottom: -2px;
  font-size: var(--font-size-sm); }
nav a:hover { background: transparent; color: var(--color-text);
  border-bottom-color: var(--color-border); }
nav a.active { color: var(--color-win); border-bottom-color: var(--color-win);
  font-weight: 600; border-bottom: 2px solid var(--color-win); }
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
  box-sizing: border-box;
  max-width: 100%;
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
.error-msg { color: var(--color-error); padding: var(--space-sm) 0; }
.warning { color: var(--color-warning); }
.unverified { color: var(--color-warning); }
table { border-collapse: collapse; width: 100%; margin-top: 0.5rem; }
td, th { border: none; border-bottom: 1px solid var(--color-border);
  padding: 0.5rem 0.75rem; text-align: left; font-size: var(--font-size-sm); }
th { background: var(--color-surface2); color: var(--color-muted); font-weight: 600;
  font-family: var(--font-sans); text-transform: uppercase; letter-spacing: 0.06em;
  font-size: 11px; }
pre { background: var(--color-surface); padding: 12px;
  overflow-x: auto; border-radius: var(--radius); }
code { background: var(--color-surface); padding: 2px 6px; border-radius: var(--radius); }
.streams td:last-child { text-align: right; }

/* Cards */
.card { background: var(--color-surface); border: 1px solid var(--color-border);
        border-radius: var(--radius); padding: var(--space-md); margin: var(--space-md) 0; }
.card__title { margin-top: 0; font-size: var(--font-size-lg); color: var(--color-muted); }
.card a { display: inline-flex; align-items: center; min-height: 44px; }

/* Badges */
.badge { display: inline-block; padding: 2px 8px; border-radius: var(--radius);
         font-size: var(--font-size-sm); font-weight: bold; }
.badge--success { background: var(--color-success); color: #111; }
.badge--error { background: var(--color-error-bg); color: #fff; }
.badge--warning { background: var(--color-warning); color: #111; }
.badge--info { background: var(--color-info); color: #fff; }
.badge--muted { background: var(--color-border); color: var(--color-text); }

/* Playstyle pills */
.playstyle-pills { display: flex; flex-wrap: wrap; gap: 6px;
  margin: var(--space-sm) 0; }
.playstyle-pill { display: inline-block; padding: 3px 10px; border-radius: 12px;
  font-size: 11px; font-weight: 700; font-family: var(--font-sans);
  white-space: nowrap; line-height: 1.4; }

/* Stat counters */
.stat { display: inline-block; text-align: center; padding: var(--space-md); }
.stat__value { display: block; font-size: var(--font-size-2xl); font-weight: bold; }
.stat__label { display: block; font-size: var(--font-size-sm); color: var(--color-muted); }

/* Form layout — mobile-first: stacked by default */
.form-inline { display: flex; flex-direction: column; gap: var(--space-sm);
  position: sticky; top: 0; z-index: 100; background: var(--color-bg);
  padding: var(--space-sm) 0; margin: 0 auto; max-width: 700px;
  border-bottom: 1px solid var(--color-border); }
.form-inline input, .form-inline select, .form-inline button { width: 100%; }
.form-inline label { display: flex; flex-direction: column; gap: 2px;
                     font-size: var(--font-size-sm); color: var(--color-muted); }

/* Table scroll wrapper */
.table-scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
.table-scroll td, .table-scroll th { white-space: nowrap; }
.table-scroll td.cell-wrap { white-space: normal; word-break: break-word;
  max-width: 300px; }

/* Small button */
.btn-sm { padding: var(--space-xs) var(--space-sm); font-size: var(--font-size-sm);
          min-height: 44px; }
.btn--refresh { background: var(--color-surface2); color: var(--color-text);
  font-size: var(--font-size-sm); padding: var(--space-xs) var(--space-md);
  min-height: 36px; margin-bottom: var(--space-sm); }

/* Pagination links — accessible touch targets */
.page-link { display: inline-flex; align-items: center; min-height: 44px;
             padding: 0 var(--space-sm); }

/* Utility */
.text-right { text-align: right; }

/* Banners */
.banner { padding: var(--space-md); border-radius: var(--radius); margin: var(--space-md) 0;
          border-left: 4px solid; }
.banner--error { background: color-mix(in srgb, var(--color-error) 10%, transparent);
                border-color: var(--color-error); }
.banner--success { background: color-mix(in srgb, var(--color-success) 10%, transparent);
                  border-color: var(--color-success); }
.banner--warning { background: color-mix(in srgb, var(--color-warning) 10%, transparent);
                  border-color: var(--color-warning); }

/* Empty state */
.empty-state { text-align: center; padding: var(--space-xl); color: var(--color-muted); }
.empty-state code { display: block; margin-top: var(--space-sm); }

/* Stats grid */
.stats-grid { display: grid; grid-template-columns: 1fr; gap: var(--space-md); }

/* Skip to content */
.skip-link { position: absolute; top: -40px; left: 0; padding: var(--space-sm);
             background: var(--color-info); color: #fff; z-index: 100; }
.skip-link:focus { top: var(--space-sm); }

/* Log viewer */
.log-wrap { font-family: var(--font-mono); font-size: 0.82em; }
.log-line { display: flex; flex-direction: column; gap: 2px; padding: 2px 4px;
  border-bottom: 1px solid var(--color-border); flex-wrap: nowrap; }
.log-critical { background: color-mix(in srgb, var(--color-error) 15%, transparent);
               font-weight: bold; }
.log-error { background: color-mix(in srgb, var(--color-error) 8%, transparent); }
.log-warning { background: color-mix(in srgb, var(--color-warning) 8%, transparent); }
.log-debug { color: var(--color-muted); }
.log-ts { color: var(--color-muted); white-space: nowrap; flex-shrink: 0; }
.log-badge { padding: 0 4px; border-radius: 2px;
  font-size: 0.75em; white-space: nowrap; flex-shrink: 0; }
.log-badge.log-critical { background: var(--color-critical); color: #fff; }
.log-badge.log-error { background: var(--color-error); color: #fff; }
.log-badge.log-warning { background: var(--color-warning); color: #111; }
.log-badge.log-debug { background: var(--color-border); color: var(--color-text); }
.log-badge.log-info { background: var(--color-info); color: #fff; }
.log-svc { color: var(--color-info); flex-shrink: 0; }
.log-msg { flex: 1; }
.log-extra { color: var(--color-muted); font-size: 0.9em; }
.log-controls { margin: 0.5rem 0; display: flex;
  gap: 0.5rem; align-items: center; flex-wrap: wrap; }
.log-meta { color: var(--color-muted); font-size: 0.85em; margin-bottom: 0.3rem; }
#pause-btn { padding: var(--space-sm) var(--space-lg); min-height: 44px; cursor: pointer; }
#pause-btn.paused, #streams-pause-btn.paused { background: var(--color-error); color: #fff; }
.log-ts, .log-badge, .log-svc { font-size: 0.75em; }

#player-search { width: 100%; }

/* Mobile overrides */
@media (max-width: 767px) {
  body { margin: 1rem auto; }
  .site-footer { padding: var(--space-md) var(--space-sm); }
  .form-inline label { font-size: var(--font-size-base); }
  td, th { padding: 0.3rem 0.4rem; }
}

/* Tablet (768px+) */
@media (min-width: 768px) {
  .form-inline { flex-direction: row; flex-wrap: wrap; align-items: flex-end; }
  .form-inline label { flex: 1; min-width: 0; }
  .form-inline input, .form-inline select { width: 100%; }
  .form-inline button { width: auto; }
  body { padding: 0 1rem; }
  .log-line { flex-direction: row; gap: 0.5rem; align-items: baseline; }
  .log-ts, .log-badge, .log-svc { font-size: inherit; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
  #player-search { width: auto; }
}

/* Wide desktop (1440px+) */
@media (min-width: 1440px) {
  body { max-width: 1200px; }
  .stats-grid { grid-template-columns: repeat(2, 1fr); }
}

/* Spinner */
@keyframes _spin { to { transform: rotate(360deg); } }
.spinner {
  display: inline-block;
  width: 18px; height: 18px;
  border: 2px solid var(--color-border);
  border-top-color: var(--color-info);
  border-radius: 50%;
  animation: _spin 0.7s linear infinite;
  vertical-align: middle;
  margin-left: var(--space-sm);
}

/* Dashboard grid */
.dashboard-grid {
  display: grid;
  grid-template-columns: 1fr;
  gap: var(--space-md);
  margin: var(--space-md) 0;
}
@media (min-width: 768px) { .dashboard-grid { grid-template-columns: repeat(2, 1fr); } }
@media (min-width: 1440px) { .dashboard-grid { grid-template-columns: repeat(3, 1fr); } }

/* Sort controls */
.sort-controls { display: flex; gap: var(--space-sm); align-items: center;
                 margin-bottom: var(--space-sm); flex-wrap: wrap; }
.sort-controls a { padding: var(--space-xs) var(--space-sm); border-radius: var(--radius);
                   text-decoration: none; color: var(--color-muted);
                   border: 1px solid var(--color-border); font-size: var(--font-size-sm);
                   min-height: 44px; display: inline-flex; align-items: center; }
.sort-controls a.active { color: var(--color-text); border-color: var(--color-info);
                           background: color-mix(in srgb, var(--color-info) 10%, transparent); }
.sort-controls span { font-size: var(--font-size-sm); color: var(--color-muted); }

/* Footer */
.site-footer { text-align: center; padding: var(--space-lg); color: var(--color-muted);
  font-size: var(--font-size-sm); border-top: 1px solid var(--color-border);
  margin-top: var(--space-xl); }

/* Loading state */
.loading-state { display: flex; align-items: center; gap: var(--space-sm);
  color: var(--color-muted); padding: var(--space-lg); }

/* Champion icons */
.champion-icon { width: var(--icon-champ-sm); height: var(--icon-champ-sm);
  border-radius: 50%; vertical-align: middle; margin-right: var(--space-xs);
  object-fit: cover; border: 2px solid var(--color-border); flex-shrink: 0; }

.match-list { display: flex; flex-direction: column; gap: 3px; margin-top: var(--space-sm); }
.match-row { display: flex; align-items: center; gap: var(--space-sm);
  padding: var(--space-sm) var(--space-md); border-radius: var(--radius);
  border-left: 4px solid transparent; background: var(--color-surface);
  min-height: 72px; }
.match-row--win { border-left-color: var(--color-win); background: var(--color-win-bg); }
.match-row--loss { border-left-color: var(--color-loss); background: var(--color-loss-bg); }
.match-result { width: 40px; flex-shrink: 0; font-size: var(--font-size-sm);
  font-weight: 700; font-family: var(--font-sans); text-align: center; }
.match-result--win { color: var(--color-win); }
.match-result--loss { color: var(--color-loss); }
.match-champ { display: flex; flex-direction: column; align-items: center;
  gap: 3px; width: 56px; flex-shrink: 0; }
.match-champ__name { font-size: 10px; color: var(--color-muted); text-align: center;
  max-width: 56px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  font-family: var(--font-sans); }
.match-kda { display: flex; flex-direction: column; gap: 2px; min-width: 80px; flex-shrink: 0; }
.match-kda__score { font-size: var(--font-size-base); font-weight: 700;
  font-family: var(--font-sans); }
.match-kda__sep { color: var(--color-muted); padding: 0 1px; }
.match-kda__deaths { color: var(--color-loss); }
.match-kda__ratio { font-size: var(--font-size-sm); color: var(--color-muted);
  font-family: var(--font-sans); }
.match-kda__ratio--good { color: var(--color-win); }
.match-meta-col { display: flex; flex-direction: column; gap: 2px;
  min-width: 70px; flex-shrink: 0; }
.match-meta-col__value { font-size: var(--font-size-sm);
  font-family: var(--font-sans); }
.match-meta-col__label { font-size: 10px; color: var(--color-muted);
  font-family: var(--font-sans); }
.match-items { display: flex; gap: 2px; flex-wrap: nowrap; flex-shrink: 0; }
.match-item { width: var(--icon-item); height: var(--icon-item); border-radius: 4px;
  background: var(--color-surface2); object-fit: cover; border: 1px solid var(--color-border); }
.match-item--empty { display: inline-block; width: var(--icon-item); height: var(--icon-item);
  border-radius: 4px; background: var(--color-surface2); border: 1px solid var(--color-border);
  opacity: 0.4; }
.match-info-col { margin-left: auto; display: flex; flex-direction: column;
  align-items: flex-end; gap: 3px; flex-shrink: 0; }
.match-info-col span { font-size: 10px; color: var(--color-muted); font-family: var(--font-sans); }
.match-load-more { display: block; width: 100%; padding: var(--space-sm);
  margin-top: var(--space-sm); text-align: center; background: var(--color-surface);
  border: 1px solid var(--color-border); border-radius: var(--radius);
  color: var(--color-muted); cursor: pointer; font-size: var(--font-size-sm); }
.match-load-more:hover { background: var(--color-surface2); color: var(--color-text); }
.match-badges { display: flex; gap: 4px; flex-wrap: wrap; align-items: center; flex-shrink: 0; }
.match-badge { display: inline-block; padding: 1px 6px; border-radius: 10px;
  font-size: 10px; font-weight: 700; font-family: var(--font-sans);
  white-space: nowrap; line-height: 1.4; }
details { margin-bottom: var(--space-md); }
summary { cursor: pointer; list-style: none; }
summary::-webkit-details-marker { display: none; }
summary::before { content: '\\25b6'; display: inline-block; margin-right: var(--space-sm);
  font-size: var(--font-size-sm); transition: transform 0.15s; }
details[open] > summary::before { transform: rotate(90deg); }
.match-row { cursor: pointer; transition: background 0.15s; }
.match-row:hover { filter: brightness(1.08); }
.match-detail { display: none; padding: var(--space-sm) var(--space-md);
  background: var(--color-surface); border-radius: 0 0 var(--radius) var(--radius);
  margin-top: -3px; margin-bottom: 3px; border-left: 4px solid var(--color-border); }
.match-detail.open { display: block; }
.match-detail--win { border-left-color: var(--color-win); }
.match-detail--loss { border-left-color: var(--color-loss); }
.match-detail__team { margin-bottom: var(--space-sm); }
.match-detail__team-label { font-size: var(--font-size-sm); font-weight: 700;
  font-family: var(--font-sans); padding: var(--space-xs) 0;
  border-bottom: 1px solid var(--color-border); margin-bottom: var(--space-xs); }
.match-detail__team-label--blue { color: var(--color-win); }
.match-detail__team-label--red { color: var(--color-loss); }
.match-detail__player { display: flex; align-items: center; gap: var(--space-sm);
  padding: 3px 0; font-size: var(--font-size-sm); font-family: var(--font-sans); }
.match-detail__player--me { background: rgba(255,255,255,0.04);
  border-radius: var(--radius); padding: 3px var(--space-xs); }
.match-detail__name { min-width: 100px; overflow: hidden; text-overflow: ellipsis;
  white-space: nowrap; color: var(--color-text); }
.match-detail__name a { color: var(--color-info); text-decoration: none; }
.match-detail__name a:hover { text-decoration: underline; }
.match-detail__kda { min-width: 70px; color: var(--color-muted); }
.match-detail__stat { min-width: 55px; color: var(--color-muted); text-align: right; }
.match-detail__items { display: flex; gap: 2px; }
.match-detail__items .match-item { width: var(--icon-item); height: var(--icon-item); }
.match-detail__items .match-item--empty { width: var(--icon-item); height: var(--icon-item); }
.match-detail__dmg-bar { width: 60px; height: 8px; background: var(--color-surface2);
  border-radius: 4px; overflow: hidden; }
.match-detail__dmg-fill { height: 100%; border-radius: 4px; }
.match-detail__dmg-fill--blue { background: var(--color-win); }
.match-detail__dmg-fill--red { background: var(--color-loss); }
.match-detail .loading-state { padding: var(--space-sm); }
.match-detail__build { margin-top: var(--space-sm); padding: var(--space-sm);
  background: var(--color-bg); border-radius: var(--radius); }
.match-detail__build-label { font-size: var(--font-size-sm); font-weight: 700;
  font-family: var(--font-sans); color: var(--color-muted); margin-bottom: var(--space-xs); }
.match-detail__build-row { display: flex; align-items: center; gap: var(--space-sm);
  padding: 3px 0; font-family: var(--font-sans); font-size: var(--font-size-sm); }
.match-detail__build-champ { width: 20px; height: 20px; border-radius: 50%;
  object-fit: cover; flex-shrink: 0; }
.match-detail__build-name { min-width: 80px; color: var(--color-muted);
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.match-detail__build-items { display: flex; gap: 2px; flex-wrap: wrap; }
.match-detail__build-items .match-item { width: 24px; height: 24px; }
.match-detail__build-items .match-item--empty { width: 24px; height: 24px; }
.match-detail__build-arrow { color: var(--color-muted); font-size: 10px; }
@media (max-width: 600px) {
  .match-meta-col, .match-items { display: none; }
}

/* Tabular numbers for stat columns */
.stat-num { font-variant-numeric: tabular-nums; }

/* Unified grade badges (AI Score + PBI tiers) */
.grade--S { background: linear-gradient(135deg, #e89240, #f4c874); color: #1c1c1e; }
.grade--A { background: #5383e8; color: #1c1c1e; }
.grade--B { background: #2daf6f; color: #1c1c1e; }
.grade--C { background: #7b7b8d; color: #fff; }
.grade--D { background: #3f3f4a; color: #e8e8e8; }

@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important; }
}
"""

_FAVICON = (
    "data:image/svg+xml,"
    "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'>"
    "<rect width='32' height='32' rx='4' fill='%231a1a2e'/>"
    "<text x='16' y='22' text-anchor='middle' fill='%235a9eff' "
    "font-size='20'>L</text></svg>"
)
