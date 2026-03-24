# op.gg Enrichment Service — Decisions

Locked decisions from human input + agent review. Implementation tasks are in `TODO.md`.

---

## Scope Decisions

- **Goal:** Circumvent Riot API rate limiting, not get unique data. Match data only (no OP Score or proprietary fields).
- **Coverage:** Player-level and champion-level data from op.gg.
- **Mode:** Best-effort, dual-source. Whichever source has rate-limit budget fetches first.
- **Scraping:** Approved. Rate limiter applied. ETL normalizes to match-v5 format + `source: "opgg"`.
- **Storage:** Raw op.gg data stored at `pipeline-data/opgg/`. Every record includes `source` and `fetched_at`.
- **UI visibility:** op.gg is invisible in the UI — a pipeline fill only.

---

## Architecture Decisions

- **Not a new service.** `OpggClient` lives in `lol-pipeline-common`. Integration is inside existing Fetcher (`stream:match_id`) and Crawler (`stream:puuid`).
- **ETL layer** normalizes op.gg JSON → Riot match-v5 format. Drops all proprietary fields. Fails fast on unexpected schema.
- **First-write-wins.** `RawStore.exists()` idempotency gate prevents overwrites. `source` field on `match:{match_id}` hash records provenance (NX semantics).
- **`system:halted` never triggered by op.gg.** op.gg failure = silent fallback to Riot API + log warning.
- **Separate rate limiter keyspace** (`ratelimit:opgg:short` / `ratelimit:opgg:long`). Requires parameterizing stored-limit key lookup in `acquire_token()` — `KEYS[3]`/`KEYS[4]` must use `key_prefix`, not hardcoded `ratelimit:limits:*`.
- **`stream:match_id` PACT schema** must be updated to add `source` as an optional property. Schema has `"additionalProperties": false` — adding the field without updating the schema breaks contract validation.
- **`RAW_STORE_TTL_SECONDS`** is read via `os.getenv` at module import (not a Config field). op.gg RawStore follows the same env-var pattern.
- **`opgg_api_key`** — if ever required: `opgg_api_key: str | None = None` in Config (not empty string default).
- **Legal:** op.gg internal endpoints are undocumented and unauthenticated. ToS likely prohibits automated access. No CFAA claim is made here. Project accepts these operational risks for private, non-commercial use.
- **DLQ:** New failure codes `opgg_429`, `opgg_5xx`, `opgg_timeout`, `opgg_blocked` route to existing `stream:dlq`. Never `http_403` or `system:halted`.
- **Storage:** `raw:opgg:match:{match_id}` Redis key (separate from `raw:match:{match_id}`). Disk: `pipeline-data/opgg/{platform}/{YYYY-MM}.jsonl`.
- **No new Dockerfile, streams, consumer groups, or UI changes.**
