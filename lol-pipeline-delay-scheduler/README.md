# lol-pipeline-delay-scheduler

Polls the `delayed:messages` Redis sorted set and moves ready messages to their target streams.

## Behaviour

1. Runs a tight loop sleeping `DELAY_SCHEDULER_INTERVAL_MS` between ticks (default 500ms)
2. Each tick: reads all entries in `delayed:messages` with score ≤ current time (ms)
3. Deserialises each entry as a `MessageEnvelope` and `XADD`s it to `env.source_stream`
4. Removes the entry from `delayed:messages`

## Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `DELAY_SCHEDULER_INTERVAL_MS` | `500` | Polling interval in milliseconds |
