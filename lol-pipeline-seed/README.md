# lol-pipeline-seed

Resolves a Riot ID (GameName#TagLine) to a PUUID and publishes it to `stream:puuid` to kick off pipeline crawling.

## Usage

```bash
just seed "Pwnerer#1337" na1
```

## Behaviour

1. Checks `system:halted` — refuses to run if set
2. Checks local Redis cache (`player:name:{name}#{tag}`) before calling Riot API
3. Resolves PUUID via Account-v1 API and caches it
4. Checks cooldown (`SEED_COOLDOWN_MINUTES`, default 30) — skips if recently seeded
5. Publishes `MessageEnvelope` with type `puuid` to `stream:puuid`

## Key env vars

| Variable | Default | Description |
|----------|---------|-------------|
| `SEED_COOLDOWN_MINUTES` | `30` | Minutes before a player can be re-seeded |
| `RIOT_API_KEY` | required | Riot Games API key |
| `REDIS_URL` | required | Redis connection string |
