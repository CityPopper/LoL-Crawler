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
| `reseed <GameName#TagLine> [region]` | Force re-seed bypassing cooldown |
| `system-halt` | Set `system:halted=1` (stops all services) |
| `system-resume` | Clear `system:halted` |
| `dlq list` | List DLQ entries |
| `dlq clear --all` | Clear all DLQ entries |
| `streams` | Show stream lengths |
