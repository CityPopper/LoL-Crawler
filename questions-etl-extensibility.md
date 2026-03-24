# Multi-Source ETL Extensibility

How to structure the pipeline's data ingestion layer so adding a Nth data source is a minimal, predictable change.

---

## ❓ Needs Your Input (Human-Required)

| # | Question | Answer |
|---|----------|--------|
| A1 `[H]` | Beyond op.gg and Riot API, what other sources are you considering? (u.gg, Blitz.gg, community APIs) | |

---

## Agent-Resolvable Questions (proceeding without human input)

These have been answered by specialist agents based on existing codebase constraints.

| # | Question | Decision | Rationale |
|---|----------|----------|-----------|
| A2 `[A]` | Each source its own client module, or abstract base class? | Each source gets its own module (`riot_api.py`, `opgg_client.py`, `ugg_client.py`, etc.) — no shared ABC | Adding an ABC requires every future source to inherit it, coupling all sources to one interface. If sources have different auth, headers, or API shapes, a shared interface becomes a leaky abstraction. Loose coupling is better: each client is independent, and the Fetcher/Crawler call them via an explicit fallback chain. |
| A3 `[A]` | When multiple sources have the same match, which wins? | **First-write-wins** (already decided in `questions-opgg.md`) | `RawStore.exists()` idempotency gate prevents overwrites. The first source to succeed owns the raw data. `source` field on `match:{match_id}` hash records provenance. |
| A4 `[A]` | Separate per-source disk storage or unified with `source` tag? | **Separate per-source directories** (`pipeline-data/riot/`, `pipeline-data/opgg/`, etc.) — already decided in `questions-opgg.md` | Sources may have different JSONL schemas. Separate directories make it trivial to purge one source's data, audit per-source volume, and restore without cross-contamination. |

---

## Open Architecture Proposals (awaiting architect agent review)

These are being reviewed by the architect agent (launched separately). Decisions will be locked here once consensus is reached.

- **Source registry:** How does Fetcher/Crawler discover available sources and priority order?
- **Rate limiter scoping:** How is `key_prefix` determined per source?
- **ETL boundary:** Where does normalization (source JSON → match-v5) live?
- **Failure fallback chain:** How does source A failure trigger fallback to source B?
- **Integration test strategy:** Per-source or parameterized test?
