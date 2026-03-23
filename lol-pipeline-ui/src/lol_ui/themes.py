"""Theme definitions and theme switcher rendering helpers."""

from __future__ import annotations

import contextvars

from lol_ui.strings import t as _t

_DEFAULT_THEME = "default"
_COOKIE_NAME = "theme"
_COOKIE_MAX_AGE = 365 * 24 * 3600  # 1 year

SUPPORTED_THEMES: list[str] = ["default", "artpop"]

_current_theme: contextvars.ContextVar[str] = contextvars.ContextVar(
    "_current_theme", default=_DEFAULT_THEME
)

_THEME_LABELS: dict[str, str] = {
    "default": "Default",
    "artpop": "Art Pop",
}

# ---------------------------------------------------------------------------
# Art Pop theme — Roy Lichtenstein "The Oval Office" (1992) style
#
# Primary references:
#   - Lichtenstein "The Oval Office" (1992): Bold CMYK primaries, thick
#     black outlines (4-6px), large visible Ben-Day halftone dots, flat
#     color blocks, comic-book interior with strong diagonal perspective
#     lines.  Colors: process red (#FB1D36), primary blue (#0047AB),
#     lemon yellow (#FFE800), black, white.
#   - Lichtenstein "WHAAM!" (1963): Action/combat imagery, explosion
#     starbursts, speed lines, dramatic contrast.
#   - Lichtenstein "Drowning Girl" (1963): Ben-Day dots as large visible
#     pattern (not subtle texture), thick outlines on everything.
#
# Design: BOLD and CRAZY.  Everything has thick black outlines.  Ben-Day
# dots are large and visible.  Decorative SVG vector shapes reference
# League of Legends combat motifs (crossed swords, shield, explosion
# starburst) rendered in Lichtenstein's flat comic-book style with
# primary colors and thick black strokes.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Art Pop CSS variable overrides
# ---------------------------------------------------------------------------

_ARTPOP_CSS_VARS = """\
:root {
  /* --- Light field: white newsprint paper (Lichtenstein comic panels) --- */
  --color-bg: #ffffff;
  /* Light warm grey — comic panel background */
  --color-surface: #f0ece4;
  --color-surface2: #e6e0d4;
  /* Near-black ink — comic book text */
  --color-text: #0d0d0d;
  /* Dark grey — secondary text */
  --color-muted: #555555;

  /* --- Pop Art outlines: thick, black, bold --- */
  --color-border: #0d0d0d;

  /* --- Accent palette: Lichtenstein CMYK primaries --- */
  /* Primary blue — Lichtenstein "The Oval Office" */
  --color-info: #0047ab;
  /* Primary blue — win state */
  --color-win: #0047ab;
  --color-win-bg: rgba(0, 71, 171, 0.08);
  /* Primary yellow — Lichtenstein flat yellow */
  --color-warning: #cc9900;
  --color-gold: #cc9900;
  /* Green on white — success */
  --color-success: #008800;
  /* Lichtenstein red — THE signature red */
  --color-error: #fb1d36;
  --color-error-bg: #fb1d36;
  --color-critical: #cc0000;
  /* Red — loss state */
  --color-loss: #fb1d36;
  --color-loss-bg: rgba(251, 29, 54, 0.08);

  /* --- Pop Art special tokens --- */
  --color-tier-s: #cc9900;
  --color-rank-purple: #7b2d8e;
  --color-rank-teal: #008080;

  /* --- Chart series: Lichtenstein CMYK primaries on white --- */
  --chart-b0: #0047ab;   /* primary blue */
  --chart-b1: #008800;   /* green */
  --chart-b2: #7b2d8e;   /* purple */
  --chart-b3: #cc9900;   /* gold */
  --chart-b4: #0047ab;   /* blue */
  --chart-r0: #fb1d36;   /* Lichtenstein red */
  --chart-r1: #cc0000;   /* dark red */
  --chart-r2: #ff4500;   /* orange */
  --chart-r3: #fb1d36;   /* red */
  --chart-r4: #cc9900;   /* gold */

  /* --- Damage type colors --- */
  --color-dmg-physical: #ff4500;
  --color-dmg-magic: #7b2d8e;
  --color-dmg-true: #0d0d0d;
}"""

# ---------------------------------------------------------------------------
# Art Pop decorative layer — Lichtenstein "The Oval Office" style
#
# Bold SVG vector shapes in Lichtenstein's comic-book style with League of
# Legends combat motifs.  All position:fixed, pointer-events:none, z-index:0.
#
# 1. Ben-Day dots: LARGE (4px), visible, classic Lichtenstein halftone
# 2. Comic starburst: WHAAM! explosion shape — yellow with thick black outline
# 3. Crossed swords: League combat — red/blue with black outlines
# 4. Shield/crest: League heraldic — yellow diamond with black outline
# 5. Bold diagonal speed lines: comic action lines across viewport
# 6. Red circle: Lichtenstein primary, thick outline
# ---------------------------------------------------------------------------

# SVG data URIs for vector decorative elements
_SVG_STARBURST = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'%3E"
    "%3Cpolygon points='100,5 115,65 175,25 130,80 195,100 130,120 175,175"
    " 115,135 100,195 85,135 25,175 70,120 5,100 70,80 25,25 85,65'"
    " fill='%23ffe800' stroke='%230d0d0d' stroke-width='5' "
    "stroke-linejoin='round'/%3E%3C/svg%3E"
)

_SVG_CROSSED_SWORDS = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 120 120'%3E"
    # Sword 1: red blade, bottom-left to top-right
    "%3Cline x1='15' y1='105' x2='105' y2='15' stroke='%23fb1d36' "
    "stroke-width='8' stroke-linecap='round'/%3E"
    "%3Cline x1='15' y1='105' x2='105' y2='15' stroke='%230d0d0d' "
    "stroke-width='12' stroke-linecap='round' opacity='0.3'/%3E"
    "%3Cline x1='15' y1='105' x2='105' y2='15' stroke='%23fb1d36' "
    "stroke-width='6' stroke-linecap='round'/%3E"
    # Crossguard 1
    "%3Cline x1='30' y1='78' x2='52' y2='100' stroke='%23ffe800' "
    "stroke-width='6' stroke-linecap='round'/%3E"
    # Sword 2: blue blade, bottom-right to top-left
    "%3Cline x1='105' y1='105' x2='15' y2='15' stroke='%230047ab' "
    "stroke-width='8' stroke-linecap='round'/%3E"
    "%3Cline x1='105' y1='105' x2='15' y2='15' stroke='%230d0d0d' "
    "stroke-width='12' stroke-linecap='round' opacity='0.3'/%3E"
    "%3Cline x1='105' y1='105' x2='15' y2='15' stroke='%230047ab' "
    "stroke-width='6' stroke-linecap='round'/%3E"
    # Crossguard 2
    "%3Cline x1='68' y1='100' x2='90' y2='78' stroke='%23ffe800' "
    "stroke-width='6' stroke-linecap='round'/%3E"
    "%3C/svg%3E"
)

_SVG_SHIELD = (
    "data:image/svg+xml,"
    "%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 120'%3E"
    "%3Cpath d='M50,5 L95,25 L95,65 Q95,100 50,115 Q5,100 5,65 L5,25 Z' "
    "fill='%230047ab' stroke='%230d0d0d' stroke-width='5'/%3E"
    "%3Cpath d='M50,18 L82,33 L82,62 Q82,90 50,102 Q18,90 18,62 L18,33 Z' "
    "fill='%23fb1d36' stroke='%230d0d0d' stroke-width='3'/%3E"
    "%3Cpolygon points='50,35 58,55 78,55 62,68 68,88 50,75 32,88 38,68"
    " 22,55 42,55' fill='%23ffe800' stroke='%230d0d0d' stroke-width='2'/%3E"
    "%3C/svg%3E"
)

_ARTPOP_DECORATIONS = (
    """\
/* Ben-Day halftone on white — Lichtenstein signature, LARGE red dots on white paper */
body.theme-artpop {
  position: relative;
  background-color: #ffffff;
  background-image: radial-gradient(
    circle at center,
    rgba(251, 29, 54, 0.18) 3.5px,
    transparent 3.5px
  );
  background-size: 12px 12px;
  background-repeat: round;
}

/* Comic starburst — WHAAM! explosion, top-right */
body.theme-artpop::before {
  content: '';
  position: fixed; top: 30px; right: 20px;
  width: 220px; height: 220px;
  background: url(\""""
    + _SVG_STARBURST
    + """\") no-repeat center/contain;
  opacity: 0.35;
  pointer-events: none; z-index: 0;
  filter: drop-shadow(0 0 8px rgba(255, 232, 0, 0.4));
}

/* Crossed swords — League combat, bottom-left */
body.theme-artpop::after {
  content: '';
  position: fixed; bottom: 60px; left: 15px;
  width: 180px; height: 180px;
  background: url(\""""
    + _SVG_CROSSED_SWORDS
    + """\") no-repeat center/contain;
  opacity: 0.30;
  pointer-events: none; z-index: 0;
  filter: drop-shadow(0 0 6px rgba(251, 29, 54, 0.3));
}

/* Shield crest — League heraldic, mid-right */
body.theme-artpop main::before {
  content: '';
  position: fixed; top: 45%; right: 10px;
  width: 140px; height: 168px;
  background: url(\""""
    + _SVG_SHIELD
    + """\") no-repeat center/contain;
  opacity: 0.25;
  pointer-events: none; z-index: 0;
  filter: drop-shadow(0 0 6px rgba(0, 71, 171, 0.3));
}

/* Bold diagonal speed lines — comic action across viewport (visible on white) */
body.theme-artpop main::after {
  content: '';
  position: fixed; top: 0; left: 0;
  width: 100vw; height: 100vh;
  background: repeating-linear-gradient(
    -35deg,
    transparent,
    transparent 80px,
    rgba(251, 29, 54, 0.06) 80px,
    rgba(251, 29, 54, 0.06) 84px,
    transparent 84px,
    transparent 200px,
    rgba(0, 71, 171, 0.06) 200px,
    rgba(0, 71, 171, 0.06) 204px,
    transparent 204px,
    transparent 320px,
    rgba(255, 232, 0, 0.08) 320px,
    rgba(255, 232, 0, 0.08) 326px
  );
  pointer-events: none; z-index: 0;
}

/* Stack content above decorations */
body.theme-artpop main,
body.theme-artpop nav,
body.theme-artpop .form-inline,
body.theme-artpop .site-footer,
body.theme-artpop .theme-switcher {
  position: relative; z-index: 1;
}
body.theme-artpop .skip-link:focus { position: relative; z-index: 200; }

/* --- Component overrides — Lichtenstein bold primaries + thick outlines --- */

/* Header: solid Lichtenstein red bar, white text, thick black border */
body.theme-artpop h1 {
  background: #fb1d36;
  color: #ffffff;
  -webkit-background-clip: unset;
  -webkit-text-fill-color: unset;
  background-clip: unset;
  padding: var(--space-sm) var(--space-md);
  border: 4px solid #0d0d0d;
}

/* Nav: blue tint, thick black borders on white */
body.theme-artpop nav {
  background: rgba(0, 71, 171, 0.08);
  border-bottom: 4px solid #0d0d0d;
  border-top: 2px solid #0d0d0d;
}
body.theme-artpop nav a { color: #0d0d0d; }
body.theme-artpop nav a.active {
  color: #fb1d36;
  border-bottom: 3px solid #fb1d36;
  background: rgba(251, 29, 54, 0.08);
  font-weight: 900;
}
body.theme-artpop nav a:hover {
  color: #0047ab;
  border-bottom-color: #0047ab;
}

/* Table headers: Lichtenstein blue, white text, thick black border */
body.theme-artpop th {
  background: #0047ab;
  color: #ffffff;
  font-family: Impact, "Arial Black", sans-serif;
  border: 3px solid #0d0d0d;
  text-transform: uppercase;
}

/* Links: blue on white — classic print */
body.theme-artpop a { color: #0047ab; }
body.theme-artpop a:hover { color: #fb1d36; }

/* Buttons: red, white text, thick black outline + offset shadow */
body.theme-artpop button,
body.theme-artpop .btn {
  background: #fb1d36;
  color: #ffffff;
  font-weight: 900;
  font-family: Impact, "Arial Black", sans-serif;
  border: 4px solid #0d0d0d;
  box-shadow: 5px 5px 0 #0d0d0d;
  border-radius: 0;
  text-transform: uppercase;
  letter-spacing: 0.05em;
  transition: transform 0.1s, box-shadow 0.1s;
}
body.theme-artpop button:hover,
body.theme-artpop .btn:hover {
  transform: translate(3px, 3px);
  box-shadow: 2px 2px 0 #0d0d0d;
  background: #0047ab;
  color: #ffffff;
}

/* Refresh button */
body.theme-artpop .btn--refresh {
  background: #0047ab;
  color: #ffffff;
  border: 3px solid #0d0d0d;
  box-shadow: 3px 3px 0 #0d0d0d;
}

/* Headings: Impact, italic, uppercase — comic book lettering */
body.theme-artpop h1,
body.theme-artpop h2 {
  font-family: Impact, "Arial Black", "Helvetica Neue", Arial, sans-serif;
  font-style: italic;
  text-transform: uppercase;
  letter-spacing: 0.03em;
}

/* h2: thick yellow bottom border, full-width */
body.theme-artpop h2 {
  border-bottom: 5px solid #ffe800;
  padding-bottom: 4px;
  display: block;
  width: fit-content;
}

/* Badges: BOLD blocks — thick black outline, Impact, zero radius */
body.theme-artpop .badge--info {
  background: #0047ab; color: #ffffff;
  border: 3px solid #0d0d0d; border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif; text-transform: uppercase;
}
body.theme-artpop .badge--success {
  background: #008800; color: #ffffff;
  border: 3px solid #0d0d0d; border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif; text-transform: uppercase;
}
body.theme-artpop .badge--warning {
  background: #ffe800; color: #0d0d0d;
  border: 3px solid #0d0d0d; border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif; text-transform: uppercase;
}
body.theme-artpop .badge--error {
  background: #fb1d36; color: #ffffff;
  border: 3px solid #0d0d0d; border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif; text-transform: uppercase;
}

/* Sort controls */
body.theme-artpop .sort-controls a {
  border: 2px solid #0d0d0d; color: #0d0d0d;
}
body.theme-artpop .sort-controls a.active {
  border: 3px solid #0d0d0d;
  color: #ffffff; background: #0047ab;
}

/* Tab strip */
body.theme-artpop .tab-btn--active {
  color: #fb1d36; border-bottom: 3px solid #fb1d36;
}

/* Log level badges — primary colors, white text */
body.theme-artpop .log-badge.log-info {
  background: #0047ab; color: #fff; border: 2px solid #0d0d0d;
}
body.theme-artpop .log-badge.log-warning {
  background: #ffe800; color: #0d0d0d; border: 2px solid #0d0d0d;
}
body.theme-artpop .log-badge.log-error {
  background: #fb1d36; color: #fff; border: 2px solid #0d0d0d;
}
body.theme-artpop .log-badge.log-critical {
  background: #cc0000; color: #fff;
  border: 2px solid #0d0d0d; font-weight: 900;
}
body.theme-artpop .log-svc { color: #0047ab; }

/* Grade badges */
body.theme-artpop .grade--S {
  background: #ffe800; color: #0d0d0d; border: 3px solid #0d0d0d;
}
body.theme-artpop .grade--A { background: #0047ab; color: #fff; border: 3px solid #0d0d0d; }
body.theme-artpop .grade--B { background: #fb1d36; color: #fff; border: 3px solid #0d0d0d; }

/* Match rows: thick borders, bold color — comic panels */
body.theme-artpop .match-row {
  border: 2px solid #0d0d0d;
  border-left-width: 6px;
  margin-bottom: 2px;
}
body.theme-artpop .match-row--win {
  border-left-color: #0047ab;
  background: rgba(0, 71, 171, 0.06);
}
body.theme-artpop .match-row--loss {
  border-left-color: #fb1d36;
  background: rgba(251, 29, 54, 0.06);
}

/* Banners: thick black outline */
body.theme-artpop .banner {
  border: 3px solid #0d0d0d; border-left-width: 6px;
}

/* Focus ring: blue — high contrast on white */
body.theme-artpop :focus-visible {
  outline: 3px solid #0047ab; outline-offset: 2px;
}

/* Tables: thick black borders — comic panel grid */
body.theme-artpop table { border: 3px solid #0d0d0d; }
body.theme-artpop table tr { border-bottom: 2px solid #0d0d0d; }
body.theme-artpop td { color: #0d0d0d; border-right: 1px solid rgba(0,0,0,0.15); }
body.theme-artpop td:last-child { border-right: none; }
body.theme-artpop td a { color: #0047ab; }

/* Inputs: thick black borders, white bg */
body.theme-artpop input, body.theme-artpop select {
  border: 3px solid #0d0d0d; border-radius: 0;
  background: #ffffff; color: #0d0d0d;
}

/* Cards on white: visible panel with shadow */
body.theme-artpop .card {
  background: #f0ece4;
  border: 4px solid #0d0d0d;
  border-left: 6px solid #fb1d36;
  border-radius: 0;
  box-shadow: 4px 4px 0 rgba(0, 0, 0, 0.3);
}

/* Footer + misc text on white */
body.theme-artpop .site-footer { color: #555; border-top: 3px solid #0d0d0d; }
body.theme-artpop hr { border-top: 3px solid #0d0d0d; }
body.theme-artpop code { background: #f0ece4; color: #0d0d0d; border: 1px solid #0d0d0d; }
body.theme-artpop pre { background: #f0ece4; border: 2px solid #0d0d0d; }"""
)

# ---------------------------------------------------------------------------
# Theme CSS blocks
# ---------------------------------------------------------------------------

_THEME_CSS: dict[str, str] = {
    "default": "",
    "artpop": f"{_ARTPOP_CSS_VARS}\n{_ARTPOP_DECORATIONS}",
}


def get_theme_css(theme: str) -> str:
    """Return the CSS override block for the given theme (empty for default)."""
    return _THEME_CSS.get(theme, "")


def get_theme(request: object) -> str:
    """Resolve the active theme from request cookie.

    The *request* object must have a ``cookies`` attribute (dict-like).
    """
    cookie_val: str = getattr(request, "cookies", {}).get(_COOKIE_NAME, "")
    if cookie_val in SUPPORTED_THEMES:
        return cookie_val
    return _DEFAULT_THEME


def set_theme_cookie(response: object, theme: str) -> None:
    """Set the ``theme`` cookie on *response*."""
    safe_theme = theme if theme in SUPPORTED_THEMES else _DEFAULT_THEME
    set_cookie = getattr(response, "set_cookie", None)
    if callable(set_cookie):
        set_cookie(
            key=_COOKIE_NAME,
            value=safe_theme,
            max_age=_COOKIE_MAX_AGE,
            httponly=True,
            samesite="lax",
        )


def theme_switcher_html(current_theme: str) -> str:
    """Render a compact theme switcher dropdown as HTML."""
    options: list[str] = []
    for theme_code in SUPPORTED_THEMES:
        label = _THEME_LABELS.get(theme_code, theme_code)
        selected = " selected" if theme_code == current_theme else ""
        options.append(f'<option value="{theme_code}"{selected}>{label}</option>')
    opts_html = "\n".join(options)
    return (
        '<div class="theme-switcher" style="position:fixed;bottom:12px;right:12px;'
        "z-index:200;font-size:var(--font-size-sm);display:flex;align-items:center;"
        'gap:4px">'
        f'<label for="theme-select" style="color:var(--color-muted)">{_t("theme_label")}</label>'
        '<select id="theme-select" '
        "onchange=\"window.location='/set-theme?theme='+this.value"
        "+'&ref='+encodeURIComponent(window.location.pathname"
        '+window.location.search)" '
        'style="background:var(--color-surface);color:var(--color-text);'
        "border:1px solid var(--color-border);border-radius:var(--radius);"
        'padding:2px 6px;font-size:var(--font-size-sm);cursor:pointer">'
        f"\n{opts_html}\n"
        "</select></div>"
    )
