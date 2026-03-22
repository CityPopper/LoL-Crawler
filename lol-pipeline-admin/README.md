# lol-pipeline-admin

CLI tool for inspecting and managing pipeline state.

## Usage

```bash
just admin <command> [args]
```

Global flags: `--json` (JSON output where supported), `-y` / `--yes` (skip confirmation prompts).

## Commands

| Command | Description |
|---------|-------------|
| `stats <GameName#TagLine> [--region r]` | Show aggregated stats for a player |
| `reseed <GameName#TagLine> [--region r]` | Force re-seed bypassing cooldown |
| `reset-stats <GameName#TagLine> [--region r]` | Wipe player stats and re-trigger analysis |
| `system-halt` | Set `system:halted=1` (stops all consumers) |
| `system-resume` | Clear `system:halted` |
| `dlq list` | List DLQ entries with failure codes |
| `dlq replay [id \| --all]` | Replay DLQ entries back to their source stream |
| `dlq clear --all` | Clear all DLQ entries |
| `dlq archive list` | List DLQ archive entries |
| `dlq archive clear --all` | Clear all DLQ archive entries |
| `replay-parse --all` | Re-enqueue all parsed matches to `stream:parse` |
| `replay-fetch <match_id>` | Re-enqueue a single match ID to `stream:match_id` |
| `clear-priority [GameName#TagLine \| --all]` | Delete priority keys |
| `recalc-priority` | Diagnostic: count `player:priority:*` keys (read-only) |
| `recalc-players` | Rebuild `players:all` sorted set from player hashes |
| `delayed-list` | Show entries in `delayed:messages` sorted set |
| `delayed-flush --all` | Remove all delayed messages |
| `backfill-champions` | Reprocess parsed matches to populate champion stats |

## Module Structure

See `src/lol_admin/main.py` for CLI parser and dispatch.
Command implementations in `cmd_*.py`, shared helpers in `_helpers.py` and `_formatting.py`.
