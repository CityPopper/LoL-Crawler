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
# Art Pop theme — design rationale
#
# Source references:
#   - Warhol Marilyn portfolio (1967): Day-Glo fluorescent screen inks —
#     hot pink (#FF2D9B), electric yellow (#FFE800), process cyan (#00C8FF),
#     vivid orange (#FF4500).  Warhol described these colors as "artificial"
#     and non-naturalistic by design.
#   - Lichtenstein: pure CMYK primaries — process cyan, magenta, yellow,
#     black.  Signature Lichtenstein red (#FB1D36).  Ben-Day halftone:
#     uniformly-spaced circular dots on a grid, rendered via repeating
#     radial-gradient.
#   - Haring: high-contrast warm/cool juxtaposition, electric lime (#BAFF00)
#     against black outlines; "electricity on the surface" per studio mgr.
#
# Dark background (#0d0d0d, printing ink black) maximizes the perceived
# saturation of the accent colors — exactly how screen-print color pops
# against dark paper.  Off-white text (#f5f0e6) mimics uncoated paper
# stock.  Neo-brutalist buttons (hard offset shadow, zero border-radius)
# reference the flat graphic style of Pop Art screen prints.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Art Pop CSS variable overrides
# ---------------------------------------------------------------------------

_ARTPOP_CSS_VARS = """\
:root {
  /* --- Dark field: printing ink black (not pure — avoids harsh halation) --- */
  --color-bg: #0d0d0d;
  /* Dark magenta tint — colored paper stock */
  --color-surface: #1a0d2e;
  --color-surface2: #241440;
  /* Off-white — uncoated paper stock */
  --color-text: #f5f0e6;
  /* WCAG AA compliant muted text */
  --color-muted: #b0b0c8;

  /* --- Pop Art outlines: thick, visible, purposeful --- */
  --color-border: #5a2d8a;

  /* --- Accent palette: Warhol/Lichtenstein CMYK screen-print spectrum --- */
  /* Hot pink — Warhol Marilyn "Pink" / Lichtenstein magenta ink */
  --color-info: #ff2d9b;
  /* Electric cyan — Warhol Marilyn "Turquoise" / Lichtenstein process cyan */
  --color-win: #00c8ff;
  --color-win-bg: rgba(0, 200, 255, 0.07);
  /* Vivid yellow — Warhol "Lemon Marilyn" / Lichtenstein yellow ink */
  --color-warning: #ffe800;
  --color-gold: #ffe800;
  /* Electric lime — Keith Haring warm/cool contrast signature */
  --color-success: #baff00;
  /* Lichtenstein red — bold primary */
  --color-error: #fb1d36;
  --color-error-bg: #cc1122;
  --color-critical: #ff0040;
  /* Warhol Orange Marilyn for loss */
  --color-loss: #ff4500;
  --color-loss-bg: rgba(255, 69, 0, 0.07);

  /* --- Pop Art special tokens --- */
  /* S-tier: Warhol gold silk-screen */
  --color-tier-s: #ffe800;
  /* Rank purple: screen-print purple ink (rare in Pop Art — used sparingly) */
  --color-rank-purple: #d44dff;
  /* Rank teal: process cyan variant */
  --color-rank-teal: #00e8d0;

  /* --- Chart series: CMYK screen-print palette order --- */
  --chart-b0: #00c8ff;   /* process cyan */
  --chart-b1: #baff00;   /* electric lime */
  --chart-b2: #ff2d9b;   /* hot pink */
  --chart-b3: #d44dff;   /* screen purple */
  --chart-b4: #ffe800;   /* yellow */
  --chart-r0: #fb1d36;   /* Lichtenstein red */
  --chart-r1: #ff2d9b;   /* magenta */
  --chart-r2: #ff4500;   /* orange */
  --chart-r3: #ff0040;   /* crimson */
  --chart-r4: #ffe800;   /* yellow */

  /* --- Damage type colors: map to Pop Art primaries --- */
  --color-dmg-physical: #ff4500;   /* Warhol orange — physical, kinetic */
  --color-dmg-magic: #d44dff;      /* purple — arcane */
  --color-dmg-true: #f5f0e6;       /* off-white — unavoidable */
}"""

# ---------------------------------------------------------------------------
# Art Pop decorative layer
#
# Four decorative techniques from Pop Art, all CSS-only, pointer-events none:
#
# 1. Ben-Day dot halftone (body background) — Lichtenstein's signature.
#    Implemented as repeating radial-gradient on a tight 14px grid.
#    Magenta dots at low opacity so it reads as texture, not noise.
#
# 2. Cyan circle top-right (body::before) — Warhol "Turquoise Marilyn"
#    screen-print circle motif, low opacity.
#
# 3. Magenta circle bottom-left (body::after) — Warhol "Pink Marilyn"
#    complement.  Warm/cool contrast as Haring used it.
#
# 4. Yellow diamond mid-right (main::before) — Lichtenstein flat geometric.
#    Rotated 45 degrees (diamond orientation), very low opacity.
#
# 5. Diagonal stripe bottom-left (main::after) — screen-print registration
#    stripe accent, electric lime at low opacity.
#
# All shapes are position:fixed so they stay put during scroll (wallpaper
# effect, not content-relative).  z-index:0; content stacks above at z-index:1.
# ---------------------------------------------------------------------------

_ARTPOP_DECORATIONS = """\
/* Ben-Day halftone field — Lichtenstein dot grid technique */
body.theme-artpop {
  position: relative;
  background-image: radial-gradient(
    circle at center,
    rgba(255, 45, 155, 0.18) 1.5px,
    transparent 1.5px
  );
  background-size: 14px 14px;
  background-repeat: round;
}

/* Cyan circle top-right — Warhol "Turquoise Marilyn" screen-print crop */
body.theme-artpop::before {
  content: '';
  position: fixed; top: -80px; right: -80px;
  width: 320px; height: 320px;
  border-radius: 50%;
  background: radial-gradient(
    circle at center,
    rgba(0, 200, 255, 0.10) 0%,
    rgba(0, 200, 255, 0.04) 55%,
    transparent 75%
  );
  border: 2px solid rgba(0, 200, 255, 0.15);
  pointer-events: none; z-index: 0;
}

/* Magenta circle bottom-left — Warhol "Pink Marilyn" complement */
body.theme-artpop::after {
  content: '';
  position: fixed; bottom: -100px; left: -100px;
  width: 380px; height: 380px;
  border-radius: 50%;
  background: radial-gradient(
    circle at center,
    rgba(255, 45, 155, 0.10) 0%,
    rgba(255, 45, 155, 0.04) 55%,
    transparent 75%
  );
  border: 2px solid rgba(255, 45, 155, 0.12);
  pointer-events: none; z-index: 0;
}

/* Yellow diamond mid-right — Lichtenstein flat geometric */
body.theme-artpop main::before {
  content: '';
  position: fixed; top: 35%; right: -120px;
  width: 220px; height: 220px;
  background: rgba(255, 232, 0, 0.06);
  border: 2px solid rgba(255, 232, 0, 0.12);
  transform: rotate(45deg);
  pointer-events: none; z-index: 0;
}

/* Diagonal stripe — screen-print registration stripe */
body.theme-artpop main::after {
  content: '';
  position: fixed; bottom: 8%; left: -40px;
  width: 300px; height: 6px;
  background: rgba(186, 255, 0, 0.10);
  transform: rotate(-25deg);
  pointer-events: none; z-index: 0;
}

/* Stack content above decorations */
body.theme-artpop main,
body.theme-artpop nav,
body.theme-artpop h1,
body.theme-artpop .form-inline,
body.theme-artpop .site-footer,
body.theme-artpop .theme-switcher,
body.theme-artpop .skip-link {
  position: relative; z-index: 1;
}
body.theme-artpop .skip-link:focus { z-index: 200; }

/* --- Component overrides --- */

/* Nav: hot pink active indicator — Warhol signature magenta */
body.theme-artpop nav a.active {
  color: #ff2d9b;
  border-bottom-color: #ff2d9b;
}
body.theme-artpop nav a:hover {
  border-bottom-color: rgba(255, 45, 155, 0.4);
}

/* Links: process cyan — Lichtenstein primary */
body.theme-artpop a { color: #00c8ff; }
body.theme-artpop a:hover { color: #ff2d9b; }

/* Neo-brutalist buttons — hard offset shadow, translate on hover */
body.theme-artpop button,
body.theme-artpop .btn {
  background: linear-gradient(135deg, #ff2d9b 0%, #00c8ff 100%);
  color: #0d0d0d;
  font-weight: 700;
  border: 2px solid #0d0d0d;
  box-shadow: 3px 3px 0 #0d0d0d;
  border-radius: 0;
  transition: transform 0.1s, box-shadow 0.1s;
}
body.theme-artpop button:hover,
body.theme-artpop .btn:hover {
  transform: translate(2px, 2px);
  box-shadow: 1px 1px 0 #0d0d0d;
}

/* Refresh button: subtle surface, not the full gradient */
body.theme-artpop .btn--refresh {
  background: rgba(255, 45, 155, 0.12);
  color: #ff2d9b;
  border: 1px solid rgba(255, 45, 155, 0.3);
  box-shadow: 2px 2px 0 #0d0d0d;
}

/* Headings: Impact/Arial Black, italic, uppercase */
body.theme-artpop h1,
body.theme-artpop h2 {
  font-family: Impact, "Arial Black", "Helvetica Neue", Arial, sans-serif;
  font-style: italic;
  text-transform: uppercase;
  letter-spacing: 0.02em;
}

/* Page title: tricolor Warhol Marilyn palette sweep */
body.theme-artpop h1 {
  border-bottom: 2px solid #5a2d8a;
  background: linear-gradient(90deg, #ff2d9b 0%, #00c8ff 45%, #ffe800 100%);
  -webkit-background-clip: text;
  -webkit-text-fill-color: transparent;
  background-clip: text;
}

/* h2: yellow underline accent — Warhol "Lemon Marilyn" */
body.theme-artpop h2 {
  border-bottom: 3px solid #ffe800;
  padding-bottom: 4px;
  display: inline-block;
}

/* Cards: border-radius 0, bold colored left border — Pop Art panel */
body.theme-artpop .card {
  background: var(--color-surface);
  border: 1.5px solid #5a2d8a;
  border-left: 3px solid #ff2d9b;
  border-radius: 0;
}

/* Badges: flat Pop Art blocks — border-radius 0, thick border, Impact font */
body.theme-artpop .badge--info {
  background: #ff2d9b;
  color: #0d0d0d;
  border: 2px solid #0d0d0d;
  border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif;
}
body.theme-artpop .badge--success {
  background: #baff00;
  color: #0d0d0d;
  border: 2px solid #0d0d0d;
  border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif;
}
body.theme-artpop .badge--warning {
  background: #ffe800;
  color: #0d0d0d;
  border: 2px solid #0d0d0d;
  border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif;
}
body.theme-artpop .badge--error {
  background: #fb1d36;
  color: #f5f0e6;
  border: 2px solid #0d0d0d;
  border-radius: 0;
  font-family: Impact, "Arial Black", sans-serif;
}

/* Sort controls: active state uses process cyan */
body.theme-artpop .sort-controls a.active {
  border-color: #00c8ff;
  color: #00c8ff;
  background: rgba(0, 200, 255, 0.08);
}

/* Tab strip: active tab uses hot pink underline */
body.theme-artpop .tab-btn--active {
  color: #ff2d9b;
  border-bottom-color: #ff2d9b;
}

/* Log level badges */
body.theme-artpop .log-badge.log-info { background: #ff2d9b; color: #0d0d0d; }
body.theme-artpop .log-badge.log-warning { background: #ffe800; color: #0d0d0d; }
body.theme-artpop .log-badge.log-error { background: #fb1d36; color: #f5f0e6; }
body.theme-artpop .log-badge.log-critical { background: #ff0040; color: #f5f0e6; }
body.theme-artpop .log-svc { color: #00c8ff; }

/* Grade badges: remap to Pop Art primaries */
body.theme-artpop .grade--S {
  background: linear-gradient(135deg, #ffe800, #ff4500);
  color: #0d0d0d;
}
body.theme-artpop .grade--A { background: #00c8ff; color: #0d0d0d; }
body.theme-artpop .grade--B { background: #baff00; color: #0d0d0d; }

/* Match rows: thicker left border to echo Pop Art outlines */
body.theme-artpop .match-row {
  border-left-width: 4px;
}
body.theme-artpop .match-row--win {
  border-left-color: #00c8ff;
  background: rgba(0, 200, 255, 0.07);
}
body.theme-artpop .match-row--loss {
  border-left-color: #ff4500;
  background: rgba(255, 69, 0, 0.07);
}

/* Banners: flat bold left border, Lichtenstein outline weight */
body.theme-artpop .banner {
  border-left-width: 4px;
}

/* Focus ring: process cyan — accessible + on-brand */
body.theme-artpop :focus-visible {
  outline-color: #00c8ff;
}"""

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
        "onchange=\"window.location='/set-theme?theme='+this.value\" "
        'style="background:var(--color-surface);color:var(--color-text);'
        "border:1px solid var(--color-border);border-radius:var(--radius);"
        'padding:2px 6px;font-size:var(--font-size-sm);cursor:pointer">'
        f"\n{opts_html}\n"
        "</select></div>"
    )
