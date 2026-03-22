---
name: project_platform
description: Platform, container runtime, dev environment setup for LoL-Crawler
type: project
---

Platform: macOS (migrated from WSL2/Windows). Podman is default container runtime.

Dev environment: Dockerfile.dev builds a Python 3.14 container with all deps. Use `just dev-ci` to run full CI. Pre-commit hook uses dev container when available.

Container runtime: `RUNTIME=docker just <cmd>` to switch. Default is podman.

11 services: crawler, fetcher, parser, analyzer, recovery, delay-scheduler, discovery, ui, seed, admin + common library.

**How to apply:**
- Always run tests in container: `just dev-ci` or `docker run --rm -v .:/workspace -w /workspace lol-crawler-dev just test`
- Build dev container first: `just dev-build` (or `RUNTIME=docker just dev-build`)
- Never trust host Python for CI validation
