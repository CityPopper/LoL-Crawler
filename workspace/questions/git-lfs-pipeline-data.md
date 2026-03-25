# Git LFS — Pipeline Data

_Created: 2026-03-25_

## Context

Store pipeline data in Git LFS so contributors can check out the repo with seeded values.
10 GB LFS quota available. Long-term the data will be moved elsewhere.

### Current data inventory

| Path | Size | Format | Content |
|------|------|--------|---------|
| `pipeline-data/riot-api/NA1/` | 52 MB | JSONL + JSONL.ZST (monthly buckets) | Raw match JSON from Riot API |
| `lol-pipeline-fetcher/match-data/NA1/` | 40 MB | JSONL + JSONL.ZST | Service-internal raw match data |
| `redis-data/dump.rdb` | 161 MB | Redis RDB snapshot | Full derived state (player profiles, stats, match indexes, champion stats) |
| `redis-data/appendonlydir/` | 194 MB | Redis AOF log | Append-only transaction log |
| **Total (all)** | **447 MB** | | |
| **Total (without AOF)** | **253 MB** | | |
| **Total (JSONL only)** | **92 MB** | | |

### Restore paths

- **With `dump.rdb`**: `redis-server --dbfilename dump.rdb` → instant Redis state, all derived data ready
- **JSONL only**: Requires running Fetcher → Parser → PlayerStats → ChampionStats pipeline to rebuild Redis from raw data (slow, hours of reprocessing)

---

## Questions

### Architecture

**[H-1] Include `dump.rdb` for instant Redis restore?**
_Decision needed: product scope / user experience_
Options:
- **Yes (253 MB in LFS)** — contributor checks out and runs `just up`; Redis is fully seeded with player profiles, stats, champion stats. Zero pipeline reprocessing.
- **No (92 MB in LFS)** — contributor checks out JSONL only; must run `just seed` or manually trigger pipeline to rebuild state. Slower but leaner.

Status: ⏳ Awaiting human answer

---

**[H-2] Include both `pipeline-data/` AND `lol-pipeline-fetcher/match-data/`, or deduplicate?**
_These two directories are separate copies with different content. Both are ~40-52 MB._
Options:
- **Both** — 92 MB JSONL total; both services get their canonical data
- **pipeline-data/ only** — fetcher match-data regenerates on next run
- **Symlink** — keep only one copy; other is a symlink (Docker volume complications)

Status: ⏳ Awaiting human answer (depends on H-1)

---

**[H-3] Full dataset or curated seed subset?**
_52 MB covers 2024-03 through 2026-03 (2+ years). A minimal seed could be just recent months._
Options:
- **Full** — all history; complete stats and match coverage
- **Curated** — last 3 months only (~5-10 MB); faster clone, less data, but stats less meaningful

Status: ⏳ Awaiting human answer

---

### Agent-resolvable (proceeding immediately)

**[A-1] .gitattributes patterns for LFS tracking**
What patterns cover `*.jsonl`, `*.jsonl.zst`, `*.rdb`, `*.aof`?
→ Devops agent

**[A-2] .gitignore changes needed**
Currently ignoring `pipeline-data/**/*.json*` and redis-data. Must remove rules for LFS-tracked files.
→ Devops agent

**[A-3] Compression check**
Are `.jsonl.zst` files already at optimal compression? Should `dump.rdb` be compressed before LFS upload?
→ Optimizer agent

**[A-4] Restore workflow (`just restore-seed`)**
What Justfile recipe lets a fresh checkout restore Redis from LFS data?
→ Devops agent

**[A-5] AOF vs RDB — include AOF?**
AOF (194 MB) is redundant if RDB is included. Can safely exclude.
→ Architect agent (confirm)

**[A-6] LFS pointer vs actual file behavior**
After `git lfs pull`, do files land in the right paths for Docker volume mounts?
→ Devops agent

**[A-7] Performance: loading JSONL into Redis vs RDB restore**
Which is faster for a fresh setup? RDB restore is O(file size); JSONL replay requires pipeline execution.
→ Optimizer agent

---

## Locked Decisions

| # | Decision | Rationale |
|---|----------|-----------|
| D-1 | No `dump.rdb` in LFS | User choice: use JSONL + auto-trigger pipeline instead. RDB restore complex (AOF conflict at startup); JSONL path is self-healing. |
| D-2 | Auto-detect empty Redis → trigger pipeline rebuild | On `just up`, if Redis is empty, seed from disk `.zst` files automatically. |
| D-3 | Process newest months first | Contributor gets recent stats immediately; older history loads in background. |
| D-4 | `pipeline-data/riot-api/` is canonical; delete `lol-pipeline-fetcher/match-data/` | Architect confirmed: match-data is an orphaned duplicate from pre-Docker local dev. 25 `.zst` files are byte-identical; pipeline-data has 157 more recent matches. Zero functional loss. |
| D-5 | Only `*.jsonl.zst` in LFS (not active `*.jsonl`) | Active `.jsonl` changes on every Fetcher run — would burn LFS quota rapidly. `.zst` files are immutable once month rolls over. |
| D-6 | All `.zst` files in LFS; no rotation for now | Disk keeps ALL historical data forever. LFS currently 29 MB vs 10 GB cap — trivial. If cap approached, migrate old files to S3. No rotation logic needed yet. |
| D-8 | Two-stage compression: append-friendly during run, max compression at compaction | Active month: plain `.jsonl` (line-appendable by Fetcher). At compaction: `zstd -19` (max compression, ~15-16x ratio). The `-19` level is slow but only runs at push/rollover time. |
| D-9 | Compress-before-push / decompress-after-pull baked into `just up` and `just compact-data` | `just compact-data` compresses ALL `.jsonl` files (including active month) → `.zst` before committing/pushing. `just up` decompresses the current month's `.zst` back to `.jsonl` at startup if no `.jsonl` exists, so Fetcher can append. This handles the active month growing large (thousands of players). |
| D-10 | Minimize new top-level `just` commands — bake everything into `just up` | New user UX: clone → `just up` → done. `just up` handles: git lfs pull, decompress current month, start services, auto-seed if Redis empty. Only 1 new command exposed: `just compact-data` (pre-push data step). Internal scripts (seed_from_disk.py) not exposed as top-level recipes. |
| D-7 | Exclude AOF entirely | Redundant with RDB; 194 MB; causes silent RDB-ignore on startup if present with `appendonly yes`. |

---

## Implementation Tasks

_(see TODO.md — LFS section)_
