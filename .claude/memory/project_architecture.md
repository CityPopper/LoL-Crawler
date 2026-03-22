---
name: Architecture decisions and phase history
description: Key architectural decisions, current version, phase progression for LoL-Crawler
type: project
---

Current version: v2.4.1 (Phase 22 TRAJECTORY complete).

Key architecture decisions:
- Redis Streams for inter-service messaging (not Kafka, NATS — see REJECTED.md)
- Single unified Dockerfile.service (replaced ~10 individual Dockerfiles in Phase 21)
- Correlation ID propagation through all pipeline messages (added Phase 21)
- Adaptive rate limiter with Lua wait hints (replaced fixed 50ms polling in Phase 21)
- priority:active SET for O(1) idle detection (replaced SCAN in Phase 22)
- Atomic SADD for parser idempotency (replaced SISMEMBER TOCTOU in Phase 22)
- Pipelined Redis calls in analyzer, fetcher (Phase 22 performance)
- Dev container (Dockerfile.dev) for CI consistency (Python 3.14)

Phase history: v1.0-v1.9 (Phases 10-17), v2.0-v2.2 (Phases 18-20), v2.4.0-v2.4.1 (Phases 21-22).

**How to apply:** Read CLAUDE.md for current directives. Read REJECTED.md before proposing new architecture. Read TODO.md for open work.
