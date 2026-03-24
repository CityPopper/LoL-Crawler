# Multi-Source ETL Extensibility — Decisions

Locked decisions from architect agent review. Implementation tasks in `TODO.md`.

---

## ❓ Needs Your Input

| # | Question | Answer |
|---|----------|--------|
| A1 `[H]` | Beyond op.gg and Riot API, what other sources are you considering? (u.gg, Blitz.gg, community APIs) | |

---

## Locked Decisions

- **No ABC.** Each source is a standalone client module (`riot_api.py`, `opgg_client.py`, `ugg_client.py`, ...) with convergent method signatures: `get_match_ids(puuid, region, ...) -> list[str]` and `get_match(match_id, region) -> dict`. Extract a `Protocol` only when a 3rd source exists — not speculatively.
- **Conflict resolution: first-write-wins** (already in `questions-opgg.md`). `match:{match_id}` hash `source` field uses NX semantics to record provenance.
- **Separate storage at all layers.** Disk: `pipeline-data/{source}/{platform}/{YYYY-MM}.jsonl`. Redis: `raw:{source}:match:{match_id}` (Riot keeps `raw:match:` for backward compat; all others use prefix). Unified downstream after ETL normalization.
- **Source registry: static list from Config env vars.** Built at startup: `{SOURCE}_ENABLED`, `{SOURCE}_RATE_LIMIT_PER_SECOND`, `{SOURCE}_MATCH_DATA_DIR`. No runtime feature flags (hot path overhead, matches REJECTED.md [OT1-R7] pattern). Alternative sources first in list, Riot always last (fallback).
- **ETL boundary: per-source module.** Each client's public methods return match-v5 format. Mapping is private to `_{source}_etl.py` + `_{source}_schema.py`. No shared normalizer — each source has a radically different input schema. PACT schemas in `contracts/schemas/` are the shared output contract.
- **Failure isolation: explicit fallback chain function.** Non-last sources fail silently (log warning + try next). Only the last source (Riot) triggers DLQ routing and `system:halted`. New DLQ codes per source (`{source}_429`, `{source}_5xx`, `{source}_timeout`, `{source}_blocked`) route to `stream:dlq` but never halt.
- **`RawStore` needs `key_prefix` parameter.** Add `key_prefix: str = "raw:match:"` to constructor. Each source gets its own `RawStore` instance with source-specific prefix and `data_dir`. This is a prerequisite for OPGG-4/OPGG-5 (tracked as OPGG-4.5 in TODO.md).

## Adding a New Source — File Count

| File | Action |
|------|--------|
| `lol_pipeline/{source}_client.py` | New: client with `get_match_ids`, `get_match` |
| `lol_pipeline/_{source}_etl.py` | New: JSON → match-v5 mapping |
| `lol_pipeline/_{source}_schema.py` | New: response validation |
| `lol_pipeline/config.py` | Add: 3 fields (`{source}_enabled`, rate limit, data dir) |
| `.env.example` | Add: 3 env vars |
| `lol_fetcher/main.py` | Add: 3 lines to `_SOURCES` list |
| `lol_crawler/main.py` | Add: 3 lines to `_SOURCES` list |
| `match_id_payload.json` | Add: `"{source}"` to `source` enum |

**Total: 3 new files, 4 files with trivial additions. No shared abstractions to maintain.**
