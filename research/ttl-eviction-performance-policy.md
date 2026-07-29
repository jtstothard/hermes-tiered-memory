# Issue #7 research: measured TTL, eviction, bootstrap, and performance policy

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`
Pins: Mnemosyne `v3.14.0` (`4e5ed14`), Hindsight `v0.8.6` (`08995e3`).

## Scope and evidence policy

Source-only design report grounded in direct reads of both pinned source trees. No live benchmarks were run, no estate data was touched, no process was started. This defines the performance/eviction policy the composite should adopt and what must be measured at live-spike time to validate it.

This ticket depends on #2, #3, #4, and #5 — it assumes the adapter contracts and outbox semantics are settled.

## Decision

The composite inherits and layers two existing tiered systems rather than inventing a new one:

- **L1 (Mnemosyne)** already has a 3-tier internal lifecycle: working memory → episodic → semantic, with TTL, consolidation (`sleep()`), and decay weights. The composite should **adopt Mnemosyne's defaults** and only override when measurement justifies it.
- **L2 (Hindsight)** is a pgvector/HNSW-backed durable store with no built-in TTL — memories persist until explicitly deleted. Eviction at L2 is therefore **policy-driven, not automatic**: the composite owns the decision of what to delete.
- **The outbox** (from #4) accumulates terminal rows and needs compaction.

The performance budget is defined by Hermes's own lifecycle constraints (from #3): prefetch must be fast (8s timeout), sync_turn is non-blocking, and teardown drains in 5s. The composite's L1 must meet these; L2 is async and bounded by the outbox.

## L1 (Mnemosyne) — native tiering, TTL, and consolidation

### The three internal tiers

Mnemosyne (Beam engine) has three internal storage tiers, configured in `mnemosyne/core/config.py` L226–237:

| Tier | Table | Default cap | TTL / promotion | Default weight |
|---|---|---|---|---|
| **Working memory (WM)** | `working_memory` | `wm_max_items`: 10,000 | `wm_ttl_hours`: 168 (7 days) | `tier1_weight`: 1.0 |
| **Episodic (EP)** | `episodic_memory` | `ep_limit`: 50,000 | promoted from WM via `sleep()` | `tier2_weight`: 0.5 |
| **Semantic (SP)** | `semantic_memory` | `sp_max`: 1,000 | promoted from EP after `tier2_days`: 30 | `tier3_weight`: 0.25 |

Decay timeline: WM items older than `tier2_days` (30d) move to EP; EP items older than `tier3_days` (180d) move to SP. Weights progressively discount older tiers in recall scoring.

### Consolidation via sleep()

`sleep()` / `sleep_all_sessions()` (`beam.py` L8033/L8357) is the consolidation mechanism: it moves eligible WM items to episodic, compresses via `smart_compress` (`tier3_max_chars`: 300), and processes in batches (`sleep_batch`: 5000). `auto_sleep_enabled: True` (default) runs this automatically. `reclaim_orphans()` cleans stale sleep claims (default `stale_after_seconds`: 3600).

### Noise hygiene

`mnemosyne/core/hygiene.py` provides `audit_noise()` (dry-run scan) and `clean_noise()` (batch delete/archive/keep with audit log). Scores rows 0.0–1.0 for noise likelihood; handles secrets detection. This is deterministic (no LLM) for v1.

### Composite policy for L1

**Adopt Mnemosyne's defaults.** Do not invent a parallel eviction system. Specifically:
- Leave `wm_max_items`, `wm_ttl_hours`, `ep_limit`, `sp_max`, tier day/weight defaults as-is for the initial implementation.
- Ensure `auto_sleep_enabled: True` so consolidation runs.
- Wire `sleep()` into the composite's `on_session_end()` or a periodic schedule (Mnemosyne's Hermes provider already does this — verify at live-spike time).
- Use `hygiene.audit_noise()` as the noise-management surface rather than custom deletion.

**What to measure at live-spike time:**
1. Actual WM growth rate per profile under realistic turn load (items/hour).
2. `sleep()` consolidation latency at the default `sleep_batch` of 5000 (does it block the 5s teardown window? It likely must run in the background, not in `close()`).
3. Recall latency at `top_k=5` (Hermes default) vs `top_k=40` (Mnemosyne Beam default) — the composite should use the lower value for prefetch speed.
4. SQLite DB size growth over a realistic session to size the backup boundary.

## L2 (Hindsight) — no native TTL, policy-driven eviction

### Storage and indexing

Hindsight stores memories in PostgreSQL with pgvector embeddings, indexed via **HNSW** (`postgresql_using="hnsw"`, `postgresql_ops={"embedding": "vector_cosine_ops"}`, `models.py` L171). HNSW is the right index for cosine similarity at recall; it trades build-time/memory for fast approximate nearest-neighbor search.

There is **no TTL, no eviction, no automatic expiry** in Hindsight. Memories persist until explicitly deleted via `delete_document` (confirmed in #5). The bank grows unbounded.

### Recall performance controls

- `budget`: `low` / `mid` (default) / `high` — controls retrieval depth/breadth. The composite should use `low` for prefetch (fast lookup) and `mid` for explicit recall.
- `limit` / `top_k`: caps result count.
- `include_chunks` / `include_source_facts`: optional enrichment that increases payload size and latency.

### Composite policy for L2

**Eviction is the composite's responsibility, not Hindsight's.** Options:

1. **No eviction (default for v1).** Hindsight is durable storage; growth is bounded by the promotion volume from L1, which is itself bounded by Mnemosyne's WM TTL. For a single-profile deployment, unbounded L2 growth is acceptable for the initial publish-candidate scope.
2. **Document-level eviction (future).** If L2 grows too large, the composite can delete documents whose `mnemosyne_revision` hasn't changed in N days (i.e. the source L1 record was deleted or hasn't been updated). This requires a sweep that checks L1 state — doable via the outbox metadata, but deferred.
3. **Bank-level reset (operational).** A CLI command to clear a profile's bank entirely (nuclear option).

**What to measure at live-spike time:**
1. Recall latency at `budget=low` vs `budget=mid` against a seeded bank of 100/1000/10000 documents.
2. HNSW index build time on first bulk load (cold start / bootstrap).
3. `retain` latency for a single document (synchronous) — this is the L2 write path the outbox calls.
4. `delete_document` latency.
5. Storage growth per document (bytes/document in pgvector) to estimate bank size at scale.

## Bootstrap and cold start

### L1 cold start

Mnemosyne initializes a fresh SQLite DB on first `initialize()`. The embedding model loads (sentence-transformers by default). **Cold-start cost = embedding model load + SQLite schema creation.** This happens in `initialize()` (within Hermes agent init) and must be fast enough not to delay session start. Measure this at live-spike time.

### L2 cold start

Hindsight bootstrap = PostgreSQL + pgvector extension + HNSW index creation. For a disposable test environment, this is container startup time (Postgres image + extension install). For the composite's `initialize()`, the cost is just the HTTP client connection — the server is already running. The HNSW index builds as documents are inserted; there's no separate "build index" step.

## Outbox compaction (from #4 successor question)

The outbox accumulates `promoted` (completed) and `quarantined` rows. Without compaction it grows unbounded.

**Policy:**
- `promoted` rows: delete after a retention window (default 7 days). They exist only for audit/replay verification; once the retention window passes, they're safe to remove.
- `quarantined` rows: retain indefinitely until reconciled (either successfully promoted via sweep/CLI, or manually cleared). A quarantined row that's been stuck for >30 days should surface as a persistent alert (feeds the #4 sweep alert threshold).
- Compaction runs as part of the drain thread's idle cycle (when no pending rows): `DELETE FROM outbox WHERE status='promoted' AND updated_at < (now - 7 days) LIMIT 1000`.

## Performance budget summary

| Operation | Layer | Budget | Mechanism |
|---|---|---|---|
| Prefetch (per turn) | L1 | <500ms (target) | Mnemosyne SQLite recall, `top_k=5` |
| Prefetch (per turn) | L2 | <8s (Hermes hard limit) | Hindsight `budget=low`; fallback to L1 on timeout |
| Sync turn write | L1 | <100ms (target) | SQLite insert, non-blocking |
| Promotion enqueue | L1+outbox | <50ms (target) | Atomic SQLite transaction |
| Promotion drain | L2 | async, unbounded by turn | Background thread, 5-retry backoff |
| Consolidation (sleep) | L1 | background, non-blocking | Mnemosyne `sleep()`, not in teardown |
| Teardown drain | outbox | 5s hard cap | `_SYNC_DRAIN_TIMEOUT_S` from #3 |

## Newly visible successor questions

1. **L1→L2 promotion volume model.** How many documents/day does a realistic profile generate? This determines L2 growth rate and whether eviction is needed for v1. Needs measurement at live-spike time.
2. **Sleep scheduling.** Should `sleep()` run in `on_session_end()` (bounded by teardown) or on a periodic schedule (cron-like)? If teardown-bounded, does a large WM overflow the 5s cap? Measure.
3. **Embedding model dimension.** Mnemosyne and Hindsight may use different embedding models/dimensions. The composite must either align them or accept that L1 and L2 use separate embedding spaces (recall semantics differ). Verify at live-spike time.
4. **Concurrent profile drain threads.** #4 noted one drain thread per profile multiplies thread count. Measure RSS/thread impact at 5 concurrent profiles.
5. **HNSW index parameters.** Hindsight uses default HNSW params (`m`, `ef_construction`). For large banks, tuning these affects recall/latency trade-off. Deferred — only relevant if L2 grows significantly.

## Concise recommendation for issue #7

Adopt Mnemosyne's native 3-tier defaults (WM 10k items / 7-day TTL → EP 50k / 30-day → SP 1k / 180-day, weights 1.0/0.5/0.25) for L1; do not invent a parallel eviction system. Hindsight (L2) has no native TTL — default to no eviction for v1 (growth bounded by L1 promotion volume), with document-level eviction as a future option. Compact the outbox by deleting `promoted` rows after 7 days and retaining `quarantined` rows until reconciled. Performance budget: L1 prefetch <500ms, L2 prefetch <8s (Hermes hard limit), sync writes non-blocking, promotion async via outbox. Measure L1 growth rate, sleep latency, L2 recall/index latency, and embedding-model alignment at live-spike time before locking final numbers.
