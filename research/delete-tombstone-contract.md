# Delete and Tombstone Ordering Contract

**Status:** Implemented and locally verified. Evidence boundary: local SQLite tests and subprocess SIGKILL probes only. No live Hermes, Mnemosyne, Hindsight, TLS, CI, or production crash/restart evidence is claimed.

**Merged PRs implementing this contract:**

| PR | Scope | Commit |
|---|---|---|
| #12 | Fail-closed lifecycle/config, canonical JSON, file safety | `1a6c11d` |
| #13 | SQLite L1 persistence seam, schema/version rejection, ownership | `af193b1` |
| #14 | Atomic SQLite L1 + outbox via shared transaction + ATTACH; rollback-journal policy | `51b2ddf` |
| #21 | Durable tombstones + atomic delete outbox write (combined #16 + #17) | `c525bb3` |
| #22 | Stale-upsert rejection, worker ordering, batch-size window closure (closes #18) | `fd6fcc5` |

## 1. Durable Tombstones (PR #21)

`SQLiteL1Adapter.forget()` and `forget_with_outbox()` set `is_deleted=1` and increment `revision` on the L1 record. This state is durable across process restarts.

**Read semantics:**
- `get()` and `recall()` exclude tombstoned records (`is_deleted=0` filter).
- `get_revision()` still returns tombstoned revisions (ordering support).
- `health()` counts visible records only.

**Delete behavior:**
- Repeated deletion of the same source is idempotent (no error, no duplicate tombstone).
- Deleting an unknown source returns `False` / `None` without creating any row.
- Tombstones persist across restart (verified by restart persistence test).

## 2. Atomic Delete Outbox Write (PR #21)

`SQLiteL1Adapter.forget_with_outbox()` commits the tombstone and exactly one delete outbox row in a single shared SQLite transaction using the ATTACH topology.

**Atomicity boundary:**
- Tombstone UPDATE and outbox INSERT are in the same transaction.
- If either fails (enqueue failure, commit failure), both roll back — no orphan state.
- The adapter remains reusable after failure and retry succeeds with one identity.

**Outbox row identity:**
- `document_id`: stable document identifier from `document_id_factory(source_id)`.
- `mnemosyne_rev`: the *new* (incremented) revision, ensuring the outbox UNIQUE constraint is not violated.
- `content`: empty string (deletes carry no content).
- `metadata_snapshot`: JSON containing `{"operation": "delete", "origin": "mnemosyne", "profile", "bank", "mnemosyne_id", "mnemosyne_revision"}` — operation type encoded in metadata, not a schema column.

**Provider routing:**
- `CompositeProvider.memory_delete()` routes to `forget_with_outbox()` for concrete `SQLiteL1Adapter` with configured outbox.
- Non-SQLite adapters retain the isolated `forget()` path (no outbox write — consistent with the two-step store behavior).

## 3. Stale-Upsert Rejection (PR #22)

The promotion worker prevents a late upsert (stale content with a lower revision than a tombstone's delete revision) from resurrecting deleted content at L2.

**Worker ordering:**
- `OutboxStore.claim_batch()` expands to include ALL pending items for any document that has at least one item claimed in the batch. This closes the batch-size window gap where an upsert could be claimed and processed before its delete.

**Staleness check:**
- `OutboxStore.latest_delete_revision()` queries only PROMOTED (durable) deletes: `WHERE document_id = ? AND operation = 'delete' AND state = 'promoted'`.
- In-flight and pending deletes do NOT count — they are not durable and must not block upserts.
- If an upsert's `mnemosyne_rev < latest_delete_revision`, the worker quarantines it with `STALE_SUPERSEDED` terminal outcome.
- Quarantined `STALE_SUPERSEDED` items never retry on subsequent drains.

**Delete replay idempotency:**
- Processing the same delete outbox operation twice is safe.
- `MockL2Adapter.delete_document()` is idempotent: deleting a non-existent document succeeds.

**Crash-replay semantics:**
- If a worker crashes after claiming a delete but before marking it PROMOTED, the in-flight delete does NOT block subsequent upserts (it's not durable yet).
- Once the delete reaches PROMOTED state, stale upserts are rejected.

**Concurrent-claim safety:**
- Two workers claiming batches simultaneously via real threads: SQLite serializes the claim writes, and batch expansion ensures whichever worker claims a document's items gets all of them. A genuine concurrent claim race test verifies exactly one worker wins each row.

## 4. Crash-Style Probes (#19 verification)

Subprocess SIGKILL probes were exercised at three points in the delete transaction:

| Kill point | Tombstone applied? | Outbox row applied? | One-sided rows? |
|---|---|---|---|
| Before tombstone write | No | No | Zero |
| After tombstone, before outbox | No | No | Zero |
| After outbox INSERT, before COMMIT | No | No | Zero |

**Result:** SQLite's rollback journal ensures the transaction is atomic — neither the tombstone nor the outbox row is visible until COMMIT succeeds. No one-sided rows survive any SIGKILL probe point.

## 5. Evidence Boundary

The following are explicitly NOT claimed:
- Live Hermes `MemoryProvider` ABC integration or loader evidence.
- Live Mnemosyne, Hindsight, or remote backend connectivity.
- TLS or authenticated remote endpoint evidence.
- CI pipeline execution.
- Complete production crash/restart guarantees beyond the exercised SIGKILL probe points.
- Real L2 adapter delete semantics (only `MockL2Adapter` is verified).
- Operation IDs, reconciliation, lease/fence recovery (out of scope).
- Existing-database migration history (schema compatibility and ownership are explicit, not assumed).

## 6. Test Coverage

- **120 tests pass** (`python3 -m pytest -q -o addopts=''`). Warnings are deprecation (`datetime.utcnow`) and non-deterministic SQLite `ResourceWarning` from GC timing.
- **28 SQLite L1 tests**: tombstone persistence, hidden reads, revision retention, idempotent delete, atomic delete outbox, rollback, commit failure, unknown-id, already-deleted.
- **22 worker tests**: stale upsert, delete/replay, crash-replay (in-flight/promoted), backfill (higher/lower revision), concurrent workers via real threads, genuine claim race, quarantine permanence.
- `python3 -m compileall -q hermes_tiered_memory tests` — clean.
- `git diff --check` — clean.
