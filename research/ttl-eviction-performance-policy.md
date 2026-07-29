# Issue #7 research: measured TTL, eviction, bootstrap, and performance policy

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`
Report provenance: this report text is intentionally not self-bound to a future commit. At release packaging/review, the exact checked-in commit and SHA-256 for this file and its dependent contract files **MUST** be recorded after commit. The current source observations are bound to the named local baseline and upstream pins below; do not invent a current commit hash.
Local source baseline for local observations: commit `d80a9b5e4749490db8047ddf9ea7fbfd95ebbfd5`. This identifies the inspected checkout, not the report revision. At packaging/review time, record the exact checked-in commit and SHA-256 for this file and its dependent contract files after commit.

## Immutable source provenance

The source observations below are bound to these exact upstream commits. The upstream source revision, the local repository baseline, and this report's future revision are separate provenance fields; none may be substituted for another.

| Source | Version/ref | Full commit SHA | Retrieval URL | Retrieval/verification metadata | Source/artifact hash record |
|---|---|---|---|---|---|
| Mnemosyne | `v3.14.0` | `4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce` | [`tree`](https://github.com/mnemosyne-oss/mnemosyne/tree/4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce) | The cited evidence identifies this as the peeled commit for `v3.14.0`; a release-grade retrieval timestamp, media type, and verification transcript were not retained in this report. **UNRESOLVED/BLOCKING for release evidence.** | Commit SHA above is the source identity. Review-time SHA-256 observations (raw bytes, not retained here): `mnemosyne/core/config.py` = `9ba8ffa306c7ca9b76481589138214b0fca08f2084dec1d7ebad79329268bb4e`; `mnemosyne/core/beam.py` = `3360b553773d03b9f0c068e2fd01090b76630df92de38128bcc8a5b0ed817ced`. **Artifact bytes and a durable manifest are not retained; unresolved/blocking until archived.** |
| Hindsight | `v0.8.6` | `08995e3013858e705fb4ca27c0ade3a286ef4750` | [`tree`](https://github.com/vectorize-io/hindsight/tree/08995e3013858e705fb4ca27c0ade3a286ef4750) | Exact commit is cited by the reviewed evidence. A release-grade retrieval timestamp, media type, and verification transcript were not retained in this report. **UNRESOLVED/BLOCKING for release evidence.** | Commit SHA above is the source identity. Review-time SHA-256 observation (raw bytes, not retained here): [`hindsight-clients/go/api/openapi.yaml`](https://raw.githubusercontent.com/vectorize-io/hindsight/08995e3013858e705fb4ca27c0ade3a286ef4750/hindsight-clients/go/api/openapi.yaml) = `419131d6744d6d28177b779b9efa02f8a1473ca7b58cc578e4036b5aebaef9d3`. **Artifact bytes and a durable manifest are not retained; unresolved/blocking until archived.** |

The review-time raw-file hashes were computed on 2026-07-29T22:29:29Z from the exact raw URLs above; they are observations, not retained evidence. Do not claim that the source/artifact bytes remain available, reproducible, or verified from this report alone. Before release, archive the exact bytes, URL, media type, UTC retrieval time, commit/ref verification result, SHA-256 manifest, and the command/tool versions used. If any full SHA or artifact hash cannot be re-established, mark the affected claim unresolved/blocking rather than substituting a tag or short SHA.

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

1. **No eviction (default for v1).** Hindsight is durable storage and v1 has no L2 eviction: cumulative L2 growth is unbounded. WM TTL does not bound L2 growth because promoted documents persist in Hindsight after leaving L1. Trigger a documented capacity/retention review when storage reaches an approved threshold or growth-rate threshold; operator approval is required before enabling any eviction. Deletion remains delegated to #6's archive-before-delete contract.
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
- `promoted` rows: eligible for archival and later deletion only after an archive/audit retention period of **365 days**. This period preserves a full annual operational/audit lookback for reconciliation, incident investigation, and release review; it is a policy default, not a claim that deletion is automatically safe. An operator may approve a different period only through a documented retention-policy override naming the approver, rationale, scope, and policy version. Until the retention policy and any override are approved, deletion is blocked. #6's archive-before-delete contract is authoritative for archival, deletion authorization, fencing, and audit evidence.
- `quarantined` rows: retain until reconciled or an explicitly authorized manual-clear operation. A quarantined row stuck for >30 days should surface as a persistent alert (feeds the #4 sweep alert threshold); automatic compaction must not delete it.
- Compaction is a bounded candidate-selection operation, never direct ad-hoc deletion SQL. It must use #6's committed transition watermark, immutable snapshot membership, deterministic ordering, per-row archival lease/CAS, expected `state_version`/revision fence, and exclusion of active reconciliation/replay membership. A concurrent transition or fence failure skips the row and emits the required audit event.
- For each candidate, write and verify an immutable archive containing the row identity, final state, final `state_version`, transition/attempt/operation IDs, timestamps, error taxonomy, and canonical keyed digest of the redacted audit record. Retain archive and audit records for **365 days** by default (or the explicitly approved override period). Deletion is blocked until this retention policy is approved; after the period, only the authorized #6 archive-before-delete path may delete the live row. The deletion/expiry itself must be an audit event with archive ID, policy version, actor, timestamp, watermark, and archive digest. A row must never disappear from both live and archive sets.
- The deletion worker must process the deterministic ordered snapshot in bounded batches and use watermark/state-version/fence CAS. It must not issue an ad-hoc table-wide deletion statement, use `LIMIT` without an ordered immutable candidate set, or treat lease expiry as authorization. Deletion is destructive/always-ask at rollout and requires explicit approval plus a tested recovery path. Manual clear is a separate authorized operation, scoped to an explicit profile/bank/row set, confirmation-gated, archived/audited, and never an implicit consequence of retry or compaction.

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

Adopt Mnemosyne's native 3-tier defaults (WM 10k items / 7-day TTL → EP 50k / 30-day → SP 1k / 180-day, weights 1.0/0.5/0.25) for L1; do not invent a parallel eviction system. Hindsight (L2) has no native TTL — v1 permits unbounded cumulative L2 growth and triggers a documented capacity/retention review at the approved storage threshold or growth-rate threshold. Operator approval is required before enabling eviction, and deletion remains delegated to #6's archive-before-delete contract. Compact the outbox by selecting aged `promoted` rows only through that #6 contract, with a default 365-day archive/audit retention period; deletion is blocked until the retention policy is approved, and any override requires documented operator approval and rationale. Use watermark/state-version/fence CAS, deterministic ordering, audit, retention, and authorized manual-clear semantics; retain `quarantined` rows until reconciled or explicitly cleared under the same evidence-preserving path. No direct DELETE SQL is part of this policy. Performance budget: L1 prefetch <500ms, L2 prefetch <8s (Hermes hard limit), sync writes non-blocking, promotion async via outbox. Measure L1 growth rate, sleep latency, L2 recall/index latency, and embedding-model alignment at live-spike time before locking final numbers.
