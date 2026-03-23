#!/usr/bin/env python3
"""UI visual analysis tool — NOT a pytest test, run standalone.

Takes screenshots of all pages and saves them for visual review by Claude.
Run: python3 tests/regression/analyze_ui.py [--url http://localhost:8080]

Requires: playwright installed with chromium:
  pip install playwright && python3 -m playwright install --with-deps chromium

Output: screenshots saved to tests/regression/screenshots/
Then share the screenshots/ folder with Claude and ask it to
"analyze these UI screenshots for bugs."

This file is NOT discovered by pytest (no test_ prefix, no Test class).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("Install playwright: pip install playwright && python3 -m playwright install chromium")
    sys.exit(1)

PAGES = [
    ("/", "dashboard"),
    ("/stats", "stats_empty"),
    ("/champions", "champions"),
    ("/matchups", "matchups"),
    ("/players", "players"),
    ("/streams", "streams"),
    ("/dlq", "dlq"),
    ("/logs", "logs"),
]

VIEWPORTS = [
    (1280, 720, "desktop"),
    (375, 812, "mobile"),
]


def capture(base_url: str, out_dir: Path) -> list[Path]:
    """Capture screenshots of all pages at all viewports."""
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        for width, height, viewport_name in VIEWPORTS:
            for route, page_name in PAGES:
                for lang in ("en", "zh-CN"):
                    ctx = browser.new_context(
                        viewport={"width": width, "height": height},
                    )
                    page = ctx.new_page()
                    # Set language cookie
                    ctx.add_cookies(
                        [
                            {
                                "name": "lang",
                                "value": lang,
                                "url": base_url,
                            }
                        ]
                    )
                    url = f"{base_url}{route}"
                    try:
                        page.goto(url, wait_until="networkidle", timeout=10000)
                    except Exception:
                        page.goto(url, timeout=10000)

                    fname = f"{page_name}_{viewport_name}_{lang}.png"
                    fpath = out_dir / fname
                    page.screenshot(path=str(fpath), full_page=True)
                    paths.append(fpath)
                    print(f"  Captured: {fname}")
                    page.close()
                    ctx.close()

        browser.close()

    return paths


def main() -> None:
    parser = argparse.ArgumentParser(description="Capture UI screenshots for analysis")
    parser.add_argument("--url", default="http://localhost:8080", help="Base URL")
    parser.add_argument("--out", default="tests/regression/screenshots", help="Output dir")
    args = parser.parse_args()

    out_dir = Path(args.out)
    print(f"Capturing screenshots from {args.url} → {out_dir}/")
    paths = capture(args.url, out_dir)
    print(f"\nDone. {len(paths)} screenshots saved to {out_dir}/")
    print("Open them in Claude or any image viewer to analyze for UI bugs.")


if __name__ == "__main__":
    main()
