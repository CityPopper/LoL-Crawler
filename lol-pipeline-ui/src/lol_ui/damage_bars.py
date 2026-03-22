"""Damage breakdown bars — segmented physical/magic/true damage display."""

from __future__ import annotations

_DAMAGE_COLORS: list[tuple[str, str]] = [
    ("physical", "var(--color-dmg-physical)"),
    ("magic", "var(--color-dmg-magic)"),
    ("true", "var(--color-dmg-true)"),
]


def _damage_segments(
    physical: int,
    magic: int,
    true_dmg: int,
) -> list[tuple[str, float]]:
    """Return a list of (color_var, percentage) tuples for non-zero damage types.

    Returns an empty list when total damage is zero.
    """
    total = physical + magic + true_dmg
    if total == 0:
        return []
    values = [physical, magic, true_dmg]
    result: list[tuple[str, float]] = []
    for i, (_, color_var) in enumerate(_DAMAGE_COLORS):
        if values[i] > 0:
            pct = round(values[i] / total * 100, 1)
            result.append((color_var, pct))
    return result


def _damage_bar_html(
    physical: int,
    magic: int,
    true_dmg: int,
) -> str:
    """Return an HTML string rendering a segmented damage bar.

    Uses CSS flex layout. Bar width is capped at 200px.
    Returns an empty bar container when total damage is zero.
    """
    segments = _damage_segments(physical, magic, true_dmg)
    inner = ""
    for color_var, pct in segments:
        inner += (
            '<div style="'
            "background:" + color_var + ";"
            "width:" + str(pct) + "%;"
            "min-width:0;"
            "height:100%"
            '"></div>'
        )
    return (
        '<div class="dmg-bar" style="'
        "display:flex;"
        "width:100%;"
        "max-width:200px;"
        "height:8px;"
        "border-radius:4px;"
        "overflow:hidden;"
        "background:var(--color-surface2)"
        '">' + inner + "</div>"
    )
