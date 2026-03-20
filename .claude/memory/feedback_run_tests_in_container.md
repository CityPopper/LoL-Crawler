---
name: Run tests in a container
description: ALL tests must run inside Podman containers, never on the host system Python
type: feedback
---

NEVER run tests (pytest, mypy, ruff) directly on the host system. Always use Podman containers.

**Why:** User was explicit and frustrated ("stop running tests on the main system and use podman holy shit you're ass"). Running on host Python (3.14 on macOS Homebrew) doesn't guarantee CI parity and misses container environment issues.

**How to apply:**
- Unit tests: run them inside service containers via `podman run --rm <service>:ci python -m pytest tests/unit`
- Integration tests: testcontainers approach is acceptable (they spin up Redis via podman automatically)
- Never use `just test` for CI validation — use `just integration` or container-based approach
- If a `just test` convenience is needed, it should exec into a container, not run on host Python
- Before saying "CI will pass", verify with `docker build` + container-based test run
