# Delete and Tombstone Contract

**Status:** Proposed implementation contract
**Scope:** Local SQLite L1 + attached SQLite outbox path only
**Parent:** Production-candidate composite memory provider (PR #11)

## Problem statement

The current SQLite `memory_delete` path hard-deletes the L1 record and does not emit a durable L2 delete operation. A queued or retried upsert can therefore recreate content that a user already deleted. The system needs an ordered, durable delete state that survives restart and is committed atomically with the L2 invalidation operation.

## Contract outcome

A delete is represented by an L1 tombstone and an outbox delete operation committed in the same SQLite transaction. Tombstones are **invisible to normal L1 reads and recall**, but remain durable for revision ordering, stale-event rejection, replay, and later reconciliation.

The existing hard-delete behavior remains the behavior for mock/custom adapters that do not implement the concrete SQLite transaction seam. This slice does not claim live Mnemosyne or Hindsight behavior.

## Identity and ordering

- Origin identity remains the stable `source_id`/document identity used by the existing atomic promotion path.
- Every source mutation, including delete, has a strictly increasing per-source integer revision.
- The delete tombstone carries the revision that supersedes the prior visible record.
- An outbox upsert or delete with revision lower than the durable tombstone revision is stale and must not become an applicable promotion.
- An equal `(document_id, revision, operation kind)` replay is idempotent; it must not create a duplicate outbox row.
- A delete followed by a new create uses a new source identity or a later explicitly defined revision; it must not be confused with replay of the deleted record.

## State model

### L1 record states

- **Visible:** content is returned by `get`/`recall`.
- **Tombstoned:** deletion metadata and revision are retained; content is not returned by `get`/`recall`.

### Outbox operation states

The existing outbox lifecycle remains authoritative for delivery state (`pending`, retryable, terminal/quarantined as currently supported). This slice adds operation kind:

- **upsert:** publish the content snapshot for the source revision.
- **delete:** invalidate/delete the stable downstream document for the source revision.

A delete operation is processed in revision order relative to upserts for the same document identity. The worker must reject or safely skip stale upserts after a newer tombstone is durable.

## Atomic write behavior

For a concrete SQLite L1 adapter configured with an outbox:

- `memory_store` writes a visible L1 row and an upsert outbox row in one transaction.
- `memory_delete` changes the L1 row to a tombstone and writes a delete outbox row in that same transaction.
- If tombstone or outbox insertion fails, neither change is durable.
- If commit fails, both changes roll back and the adapter remains reusable.
- After restart, a committed tombstone remains invisible to reads and its delete operation remains pending/replayable.
- A failed or interrupted transaction must not leave only the tombstone or only the delete operation.

## Worker behavior

Within one stable document identity:

- A delete operation at revision `R` supersedes all upserts below `R`.
- A stale upsert below an already applied or durable tombstone is skipped and cannot resurrect content.
- A delete replay is idempotent according to the existing outbox identity constraint and does not create duplicate durable work.
- The worker records enough state to distinguish pending delete from completed delete using existing outbox lifecycle fields; operation IDs and reconciliation are later slices.

## Read behavior

- `get(source_id)` returns no record for a tombstoned source.
- `recall(query)` excludes tombstoned sources.
- `get_revision(source_id)` continues to return the tombstone revision so ordering can be enforced.
- `health` counts only visible records, unless an explicitly separate tombstone metric is added later.

## Failure and recovery requirements

The implementation must test:

- Successful delete and durable delete outbox row.
- Outbox insertion failure rolls back the tombstone.
- Commit failure rolls back both and permits retry.
- Restart after a committed delete preserves invisible L1 state and pending delete work.
- Stale upsert after delete does not become applicable or resurrect content.
- Replaying the same delete does not create a duplicate durable operation.
- Delete of an unknown source is deterministic and does not create an unowned downstream delete.
- Mock/custom adapter behavior remains unchanged and is not described as atomic.

## Explicit non-goals

- Operation IDs and caller-visible operation status.
- Reconciliation or drift repair.
- Leases, fencing, transition history, or full crash-recovery machinery.
- Live Mnemosyne, Hindsight, or Hermes `MemoryProvider` integration.
- Existing-database migration beyond an explicit schema compatibility/rejection path.
- Production deployment, TLS, credentials, or cross-profile live evidence.

## Implementation decisions

- Reuse the existing SQLite L1 transaction boundary and attached outbox topology.
- Extend the concrete local schema only through explicit compatibility checks; do not silently migrate unknown existing schemas.
- Keep provider-specific metadata construction outside the adapter through the existing callback boundary.
- Keep the delete contract separate from operation-ID and reconciliation work.

## Acceptance evidence

The slice is complete only when the focused and full local suites pass, compileall and diff checks are clean, and an independent adversarial review approves the implementation. Evidence must identify local SQLite tests separately from any live integration evidence; no live integration claim is valid without a real live probe.

## Pre-publish self-review

- **Backward traceability:** Requirements derive from the agreed next slice, existing routing/identity issues, the current hard-delete gap, and the merged atomic SQLite boundary.
- **Forward traceability:** Each contract section has corresponding acceptance behavior in the failure/recovery and read/worker requirements.
- **Coverage:** Create/update/delete/read/health are covered; migration/live integration are explicitly out of scope.
- **Failure-mode coverage:** Invalid/unknown delete, outbox failure, commit failure, restart, stale replay, and duplicate replay are specified.
- **Ambiguity scan:** Tombstones are explicitly invisible to reads; revision precedence and identity rules are explicit.
- **Perspective review:** User (delete does not resurrect), tester (deterministic probes), ops (restart and schema boundaries), security (no new credentials or access) covered.
- **Competent-implementer test:** No unresolved implementation question is required for this local slice.
- **Outcome/solution boundary:** Behavioral requirements are separated from implementation decisions; ATTACH reuse is an existing architectural constraint, not a new live-backend claim.
