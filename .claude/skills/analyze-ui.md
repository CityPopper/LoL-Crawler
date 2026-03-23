---
name: analyze-ui
description: Capture UI screenshots via analyze_ui.py and launch ALL design + translator agents in parallel for visual review. Use for validating UI changes, finding visual bugs, or reviewing themes.
user_invocable: true
---

# UI Visual Analysis

Capture screenshots of all pages across viewports, languages, and themes, then have every design agent review them.

## Step 1: Capture Screenshots

Run the screenshot tool:
```
cd lol-pipeline-ui && python3 tests/regression/analyze_ui.py --url http://localhost:8080
```

Output: `lol-pipeline-ui/tests/regression/screenshots/` — all pages x viewports (desktop 1280x720, mobile 375x812) x languages x themes.

## Step 2: Launch ALL 6 Agents in Parallel

Spawn all simultaneously in a single message. Each reads ALL screenshots and reviews from their lens:

| Agent | Reviews for |
|-------|------------|
| `design-director` | Cross-surface consistency, design system compliance, brand coherence |
| `graphic-designer` | Color, typography, spacing, contrast, visual quality |
| `ui-ux` | Usability, information hierarchy, interaction flow, error states |
| `responsive-designer` | Breakpoints, touch targets, viewport behavior, mobile layout |
| `web-designer` | HTML semantics, CSS quality, browser compatibility, accessibility |
| `chinese-translator` | **Pretend you do NOT understand English at all.** Review zh-CN screenshots as a native Chinese speaker who cannot read English. Flag any untranslated English text, confusing UI elements, labels that don't make sense in Chinese, or mixed-language content. If something on the page would confuse a Chinese-only user, it must be reported. |

Each agent must:
- Read ALL screenshots in `lol-pipeline-ui/tests/regression/screenshots/`
- Apply confidence threshold >=80%
- Respond with **APPROVE** or **REQUEST CHANGES** + specific findings referencing screenshot filenames

## Step 3: Synthesize Findings

1. Collect all 6 agents' feedback
2. De-duplicate (multiple agents often flag the same issue)
3. Prioritize: broken rendering > confusing UX > visual quality > nit
4. Create consolidated action list with screenshot references

## Step 4: Report

1. **Screenshots captured**: Count, viewport/theme/language matrix
2. **Per-agent verdict**: APPROVE or REQUEST CHANGES with finding count
3. **Consolidated findings**: De-duplicated, prioritized
4. **Recommended actions**: Ordered by severity

This skill analyzes and reports only. It does NOT implement fixes. Use `/think` or direct implementation for fixes.
