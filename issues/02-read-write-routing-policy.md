Type: grilling
Status: resolved
Claimed by: default router session
Blocked by: 01

## Question

What are the exact composite-provider read and write semantics: when does it query L1 only, fall through to L2, pull L2 results into L1, and expose results to Hermes; and which writes are synchronous versus queued?

**Completion test:** a state-transition/read-write policy is recorded with hit, miss, timeout, stale-cache, delete, and cold-start cases; every branch has an observable acceptance test and preserves Hindsight as cross-profile authority.

## Decision notes

- On a successful L2 miss, populate L1 automatically with bounded size/TTL and provenance marking.
- A normal L1 hit returns immediately. Query Hindsight only for L1 misses, explicit cross-profile/deep searches, freshness/repair checks, or background drift detection.
- If an L1 entry is stale and Hindsight is unavailable, serve it immediately with a stale marker and queue refresh/retry.
- Deletes/supersessions create an immediate L1 tombstone, then enqueue a durable L2 invalidation/delete marker.

## Answer

**Decision:** use a cache-first, write-through-L1/read-through-L2 policy with asynchronous durable L2 work.

| Case | Policy |
|---|---|
| Normal L1 hit | Return immediately; do not query L2. |
| L1 miss | Query Hindsight L2; on success return the result and backfill L1 with bounded TTL/size and provenance. |
| Explicit deep/cross-profile query | Query Hindsight directly; optionally refresh L1 for profile-local results. |
| Stale L1 + L2 unavailable | Serve stale value with a stale marker; queue refresh/retry. |
| New write | Commit to L1 synchronously before returning; enqueue L2 retain asynchronously. |
| Delete/supersession | Write an L1 tombstone synchronously; enqueue a durable L2 invalidation/delete marker. |
| Empty/young profile | Lazy cold start: first miss queries L2 and backfills L1; no blocking eager import. |

This preserves the latency/network benefit of L1 while retaining Hindsight as the cross-profile and long-term authority. The successor implementation must add acceptance tests for each table row, including L2 timeout, queue restart, stale markers, tombstone replay, and profile isolation.

**Limitation:** this ticket locks behavior; it does not yet prove live adapter semantics or durable queue implementation.