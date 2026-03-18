# Design Comparison

How this pipeline compares to existing patterns for LoL match scraping and generic distributed pipelines.

---

## Existing LoL Scrapers

### Category 1: Simple Async Scripts

**Examples:** `fattorib/LeagueMatchScraper`, `christophM/LolCrawler`, most GitHub repos.

**Pattern:**
```
main.py
  → call Riot API (sync or asyncio)
  → write row to SQLite / CSV
  → repeat
```

**Characteristics:**
- Single process, no queue, no retry logic
- Rate limiting via `time.sleep()` between calls
- No deduplication — re-runs produce duplicates
- SQLite locks on write if you add concurrency
- State = whatever's in the DB; no crash recovery

**vs. our design:**
- We separate concerns into services communicating via streams; scripts combine all logic in one place
- Our rate limiter is atomic and shared across N workers; sleep-based rate limiting breaks with multiple workers
- Our at-least-once delivery guarantees no lost matches; scripts silently drop matches on crash
- Their advantage: trivial to run, zero ops overhead — appropriate for a one-time data pull

---

### Category 2: AWS Managed Pipeline

**Pattern (e.g., Arthur Cesarino's approach):**
```
Lambda (seed) → Kinesis Firehose → S3 raw → Glue ETL → Athena (query)
```

**Characteristics:**
- Fully managed; no Redis to run
- S3 = raw store; Glue = parser; Athena = read model (SQL)
- Scales to any volume without ops
- Cold start latency on Lambda
- Cost-based scaling (pay per call, per GB processed)
- Glue crawlers introduce ETL delay (minutes, not seconds)
- Firehose buffering adds 60–900s latency before data lands in S3

**vs. our design:**
- We target sub-second stream latency; Firehose buffers for minutes
- Our Analyzer produces live queryable stats in Redis; Athena requires query-time computation
- AWS managed removes ops burden but adds vendor lock-in and cost unpredictability
- Our `RawStore` abstraction could swap Redis backing for S3 with one env var change — getting most of the durability benefit without the rest of the AWS dependency chain
- Their advantage: infinite scale, no infra management — appropriate for petabyte-scale analytics

---

## Generic Distributed Crawler Patterns

### Category 3: Scrapy-Redis

**Pattern:**
```
Redis Queue (URLs)
    ↓
Scrapy Workers (N)  ←→  Redis dedup set (seen URLs)
    ↓
Item Pipeline → storage
```

**Characteristics:**
- Redis as shared frontier queue; workers pull URLs and push results
- Built-in deduplication via Redis Set (Bloom filter variant available)
- Scrapy middleware handles retry, throttle, cookies, proxies
- No native stream semantics — queue is LPOP/RPUSH, not consumer groups
- If worker crashes mid-item, item is lost (no pending acknowledgement)
- Scrapy's AutoThrottle adjusts concurrency dynamically

**vs. our design:**
- Redis Streams consumer groups give us at-least-once delivery with acknowledgement; Scrapy-Redis LPOP gives at-most-once
- Our DLQ + Recovery service handles structured failure routing; Scrapy's retry middleware handles HTTP retries but has no DLQ
- Scrapy's spider logic mixes fetching and parsing; we split into separate services with a stream between them — enabling independent scaling and replay
- Scrapy has rich middleware ecosystem (proxy rotation, cookie jars, etc.) that we don't need for a single-API target
- Their advantage: battle-tested for general web crawling with diverse targets

---

### Category 4: Scrapy-Cluster (Kafka-based)

**Pattern:**
```
Scrapy Workers → Kafka (raw pages) → Kafka Streams (parse) → Elasticsearch
                       ↓
                 Zookeeper (coordination)
```

**Characteristics:**
- Kafka partitions allow true parallel consumption with ordering guarantees per partition
- Consumer group rebalancing on worker add/remove
- Log-compaction for long-term retention (Kafka as event store)
- Requires Zookeeper (or KRaft) — significant ops overhead
- Horizontal scale to hundreds of workers with no coordination changes
- Message replay is native (seek to offset)

**vs. our design:**
- Kafka is the right choice above ~1,000 msg/s sustained; at Riot API rate limits (100 req/2min = ~0.8 req/s) we are orders of magnitude below that threshold
- Redis Streams has similar consumer group semantics to Kafka but with sub-millisecond latency and no Zookeeper
- Kafka's log retention is its killer feature for replay; we replicate this with our raw blob RawStore — Parser can always re-read the blob and re-emit
- Kafka's at-rest retention means no separate raw store is needed; our design requires explicit RawStore abstraction
- Kafka's ops cost (cluster, monitoring, Zookeeper) is not justified for a single-API rate-limited pipeline
- Their advantage: correct choice if this pipeline processed 100+ APIs simultaneously at high volume

---

### Category 5: CQRS / Event Sourcing Alignment

**Pattern:**
```
Command → Event Store (immutable log) → Projections (read models)
```

**Our pipeline maps directly:**

| CQRS/ES concept    | Our implementation                                  |
|--------------------|-----------------------------------------------------|
| Command            | `seed "Faker#KR1"` → enqueue PUUID                 |
| Event              | Raw match JSON blob in RawStore                     |
| Event Store        | `raw:{match_id}` Redis String (write-once, no TTL)  |
| Projection         | Parser writes structured Redis keys from raw blob   |
| Read Model         | `player:stats:{puuid}`, `player:champions:{puuid}`  |
| Derived Projection | Analyzer re-aggregates stats from parsed events     |
| Replay             | Re-run Parser against existing raw blobs            |

**Key consequence:** Because the raw blob is immutable and the structured data is fully derived, any Redis structured key can be deleted and recomputed by replaying from the raw store. This is exactly the CQRS "rebuild projection" capability:

```bash
# Wipe and rebuild all stats for a PUUID
DEL player:stats:{puuid} player:stats:cursor:{puuid}
admin replay-parse --all   # re-enqueues all match:status:parsed IDs
```

**Where we diverge from strict ES:**
- Our event store is per-entity (one key per match), not a global ordered log — there is no total ordering across all matches
- Analyzer uses a cursor + running totals rather than full projection replay every time (performance optimisation)
- We don't version events or maintain an event schema registry

---

### Category 6: HextechDocs BFS Participant Crawl

**Pattern:**
```
Seed player → get N recent matches
→ for each match, get all 10 participants
→ enqueue each participant as a new seed
→ BFS across the player graph
```

**Characteristics:**
- Exponential fan-out: 1 seed → 10 players → 100 players (at 1 match/player)
- Discovers the broader player graph organically
- Used by datasets like Cassiopeia and community data dumps

**vs. our design:**
- We explicitly reject automatic fan-out — we only crawl players who are explicitly seeded
- Their approach collects population-level data; ours builds a curated dataset for specified players
- BFS requires a frontier queue with deduplication and visited-set; our `player:{puuid}` hash serves as the visited set if we ever added BFS
- At 10 participants/match × 100 req/2min rate limit, BFS would exhaust the rate limit almost immediately without per-player cooldown logic
- Adding BFS to our design would require: (1) Parser publishes participant PUUIDs to a new `stream:seed` stream, (2) Seed service consumes that stream with the same cooldown logic, (3) Crawler fan-out is naturally bounded by cooldown
- Their advantage: richer dataset; ours: controlled scope and predictable API usage

---

## Summary

| Approach | Scale fit | Ops burden | Replay | Ordering guarantee | Our choice? |
|----------|-----------|------------|--------|--------------------|-------------|
| Simple script | 1 worker | None | No | N/A | No — no resilience |
| AWS managed | Unlimited | Low (managed) | Partial | No | No — vendor lock-in, latency |
| Scrapy-Redis | 10–100 workers | Low | No | No | No — at-most-once |
| Kafka pipeline | 100+ workers | High | Yes (native) | Per-partition | No — overkill |
| **This design** | **1–10 workers** | **Low (Docker)** | **Yes (raw store)** | **Per-stream** | **Yes** |

Our design occupies the correct point in the space: rate-limited by a single external API (Riot), requires resilience and replay capability, needs to run on a single VPS or developer machine, and must be maintainable without a dedicated platform team.
