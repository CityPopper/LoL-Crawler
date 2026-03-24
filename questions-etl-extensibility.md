# Multi-Source ETL Extensibility

Consultation on how to structure the pipeline's data ingestion layer so adding a third, fourth, or Nth source is a minimal, predictable change.

---

## ❓ Needs Your Input

| # | Question | Answer |
|---|----------|--------|
| A1 | Beyond op.gg and Riot API, what other data sources are you considering adding? (e.g., u.gg, Blitz.gg, community APIs) | |
| A2 | Should each source have its own client module, or a single abstract `DataSourceClient` interface? | |
| A3 | When multiple sources have the same match, which wins — first-write, highest-quality, or most-recent? | |
| A4 | Should raw data be stored per-source on disk (`pipeline-data/riot/`, `pipeline-data/opgg/`, `pipeline-data/ugg/`) or in a unified store with a `source` tag? | |

---

## Open Technical Questions (pending human input)

- **Source registry:** How does the Fetcher/Crawler discover available sources and their priority order? (Config env vars? A registry dict? Runtime feature flags?)
- **Rate limiter scoping:** Each source needs its own rate limiter keyspace. How should `key_prefix` be determined — per-client, per-config, or per-call?
- **ETL boundary:** Should normalization (source JSON → match-v5) live inside each client module or in a shared `_etl_{source}.py` module?
- **Failure fallback chain:** How does source A failure trigger fallback to source B? (try/except in handler? explicit fallback chain? decorator?)
- **Integration test strategy:** Should each source have its own integration test, or a single parameterized test that runs all sources against the same match?
