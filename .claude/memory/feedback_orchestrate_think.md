---
name: Orchestrate think patterns
description: How to run orchestrate-think cycles — confidence thresholds, doc-agent bookend, quantifiable improvements only
type: feedback
---

Orchestrate-think cycles should follow these rules:

1. **Confidence threshold >=80%**: Agents should only propose changes they are >=80% confident will improve things. No filler, no lateral moves.
2. **Quantifiable improvements only**: Every proposal must have a measurable before/after metric. Lateral moves (same quality, different style) are rejected.
3. **Doc-agent bookend**: When running parallel agents, run the doc-keeper agent SEQUENTIALLY — once before (verify docs current) and once after (update docs). Never in parallel with implementation agents.
4. **Research before implementation**: All agents must research the web before implementing non-trivial changes.
5. **No hardcoded counts in docs**: Use order-of-magnitude estimates (~10, ~100) instead of precise counts.
6. **Test validation**: Changes must pass tests in the dev container. If tests fail, roll back and add to REJECTED.md.
7. **Commit after each round**: Push to git and tag a new release after each successful round.

**Why:** User explicitly set these rules during Phase 21-22 to prevent low-quality proposals and ensure all changes are validated.

**How to apply:** Check CLAUDE.md directives section — these rules are codified there.
