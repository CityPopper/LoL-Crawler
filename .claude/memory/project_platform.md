---
name: project_platform
description: Platform, container runtime, and LCU migration history for LoL-Crawler
type: project
---

Migrated from WSL2/Windows to macOS. LCU service removed entirely (lol-pipeline-lcu directory deleted, all references purged). Podman is now the default container runtime.

**Why:** Project moved to macOS; LCU (League Client local data collector) was Windows-only and no longer needed.

**How to apply:**
- Default runtime is Podman: `just build`, `just run`, etc. all use `podman compose`
- Switch to Docker for any command: `RUNTIME=docker just <cmd>`
- 11 services remain (was 12): redis, crawler, fetcher, parser, analyzer, recovery, delay-scheduler, ui, discovery, seed, admin
- Tests run on Python 3.14 (CI updated from 3.12 to 3.14). All 533 unit tests + 7 integration tests + 58 contract tests pass.
