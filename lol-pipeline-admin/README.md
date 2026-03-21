# lol-pipeline-admin

CLI tool for inspecting and managing pipeline state.

## Usage

```bash
just admin <command> [args]
```

## Commands

| Command | Description |
|---------|-------------|
| `stats <GameName#TagLine>` | Show aggregated stats for a player |
| `reseed <GameName#TagLine> [--region r]` | Force re-seed bypassing cooldown |
| `system-halt` | Set `system:halted=1` (stops all consumers) |
| `system-resume` | Clear `system:halted` |
| `dlq list` | List DLQ entries with failure codes |
| `dlq replay [id] --all` | Replay DLQ entries back to their source stream |
| `dlq clear --all` | Clear all DLQ entries |
| `replay-parse --all` | Re-enqueue all parsed matches to `stream:parse` |
| `replay-fetch <match_id>` | Re-enqueue a single match ID to `stream:match_id` |
