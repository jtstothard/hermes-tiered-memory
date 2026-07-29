# Issue #5 research: Hindsight-native revision, deletion, metadata, and batch semantics

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`
Hindsight pin: `v0.8.6`, commit `08995e3013858e705fb4ca27c0ade3a286ef4750`.

## Scope and evidence policy

Source-only design report. Every claim is grounded in a direct read of the pinned Hindsight source tree (docs, Python client, engine, tests) at the commit above. No Hindsight server was contacted, no estate data was touched, no commit or PR was opened. This resolves the API-semantics question; it does not execute the adapters.

This ticket verifies the assumptions made by #2 (live adapter contract) and #4 (outbox contract) against the real pinned API.

## Decision

**The pinned Hindsight v0.8.6 documentation and source support the stated revision, deletion, metadata, batch, and async-operation semantics.** This is source evidence only: no adapter or Hindsight server was exercised, and exact wire fidelity remains unverified where noted below. The composite may design against these semantics, but publish readiness still requires the disposable live integration evidence specified by #2/#4/#6.

- **Revision:** `document_id` + `update_mode: "replace"` (default) — Hindsight upserts: deletes the prior document and all its memories, then re-processes. This is the revision primitive confirmed by #2 and relied on by #4's idempotency key `(document_id, mnemosyne_revision)`.
- **Deletion:** `DocumentsApi.delete_document(bank_id, document_id)` — permanently removes the document and all associated memories.
- **Metadata:** arbitrary key-value string pairs, included in the fact-extraction prompt AND stored on each memory unit, returned with recall. Round-trips.
- **Batch:** `retain_batch_async` processes multiple items in one operation, auto-chunking large batches by token count into sub-batches. Per-item `document_id` supported.
- **Async idempotency:** caller-supplied `operation_id` makes async retries safe (no-op on re-submit); reuse with a different operation returns HTTP 409.

## API surface (verified from client + docs)

### Revision / upsert via document_id

From `retain.mdx` L114–131 and the client docstring (`memory_api.py` L3640):

> If a memory item has a `document_id` that already exists, the old document and its memory units will be deleted before creating new ones (upsert behavior).

- `update_mode: "replace"` (default) — delete old + reprocess new. **This is the revision primitive.**
- `update_mode: "append"` — concatenate new content onto existing document, reprocess combined; delta retain skips unchanged chunks so only new portion triggers LLM extraction.
- Append requires a `document_id`; without one, Hindsight raises `ValueError("update_mode='append' requires a document_id")` (`memory_engine.py` ~L3658).
- Omitting `document_id` entirely assigns a random UUID — re-ingesting the same content creates duplicate memories. **The composite must always supply a `document_id`.**

**Composite mapping (confirms #2 and #4):** a Mnemosyne record revision maps to a Hindsight document replace. The outbox idempotency key `(document_id, mnemosyne_revision)` is safe because replaying a completed `(document_id, rev)` is a no-op at the outbox level (already `promoted`), and a genuine revision increment triggers a Hindsight replace which is itself idempotent (deletes prior, processes new).

### Deletion

`DocumentsApi.delete_document(bank_id, document_id)` (`documents_api.py` L50). Engine: `MemoryEngine.delete_document` (`memory_engine.py` L5934). From `documents.mdx` L174–176:

> Deleting a document permanently removes all memories extracted from it. This action cannot be undone.

**Composite mapping:** a Mnemosyne `forget(memory_id)` maps to `delete_document(bank_id, document_id)` where `document_id` is the composite's stable mapping of the Mnemosyne source ID. Deletion is permanent and irreversible — there is no tombstone/soft-delete at the Hindsight level. If the composite needs tombstone semantics (e.g. for cross-profile replay safety), it must implement them in the outbox, not rely on Hindsight.

### Metadata round-trip

From `retain.mdx` L110–112:

> Arbitrary key-value string pairs... included in the fact extraction prompt... also stored on each memory unit and returned with every recalled memory, letting you do client-side filtering or static enrichment without extra lookups.

Verified by `test_fact_extraction_metadata.py` — metadata key-value pairs appear in the LLM prompt. The engine stores metadata on memory units (`_write_retain_outcome_metadata`, `memory_engine.py` L2552).

**Composite mapping (supported by the pinned source/docs; wire fidelity unverified):** carry Mnemosyne-only data in Hindsight metadata:
```json
{"origin": "mnemosyne", "profile": "<profile>", "bank": "<bank>", "mnemosyne_id": "<id>", "mnemosyne_revision": <int>}
```
**Unverified at wire level (deferred to live spike):** whether metadata survives a `replace` round-trip *unchanged* (same keys/values) through retain → recall → list/get document. The docs say it's "stored on each memory unit and returned with every recalled memory," but schema/API drift is the primary risk and only a live test confirms exact fidelity. The acceptance matrix from #2 (case 5: "Revision 2 replaces only the owning Hindsight document") must verify metadata round-trip explicitly.

### Batch semantics

`retain_batch_async` (`memory_engine.py` L3537):
- Accepts `contents: list[RetainContentDict]`, each with optional per-item `document_id`, `context`, `event_date`.
- Auto-chunks large batches by token count into sub-batches (`_split_contents_into_sub_batches`) to prevent timeouts.
- Sub-batches processed sequentially; a cancelled bank stops early ("bank deleted, stopping after N/M sub-batches").
- Returns `list[list[str]]` — one list of unit IDs per content item.
- Outbox callback fires only on the last sub-batch (`outbox_callback if i == len(sub_batches) else None`).

**Partial failure behavior:** the engine raises `ValueError` on validation errors (append-without-document_id, duplicate document_id across items in a strategy) *before* processing. During processing, sub-batches are sequential — a failure in sub-batch N does not roll back sub-batches 1..N-1 (they've already committed to the DB). This means:

- **Batch is NOT atomic across sub-batches.** A mid-batch failure leaves earlier sub-batches committed.
- **Per-item document_id isolation:** items with different `document_id`s in the same batch are independent; a failure processing one does not block others (different documents, no shared state).
- **Same-document concurrency is safe** (`test_retain_same_document_concurrency.py`): identical content takes the delta metadata-only path (zero re-extraction); concurrent upserts never deadlock.

**Composite obligation:** treat batch responses as per-item. Do not assume atomicity across a multi-item batch. The outbox (from #4) should promote one document at a time for the first implementation (simpler partial-failure isolation); batch promotion is a later optimization that must handle mid-batch commit boundaries.

### Async and operation_id idempotency

From `retain.mdx` L336–353:
- `async: true` returns immediately with an `operation_id`.
- Caller-supplied `operation_id` makes retries safe: re-submitting with the same id returns the original operation and creates no new work.
- Reusing an `operation_id` that belongs to a different operation returns HTTP 409.
- `operation_id` is ignored for synchronous retain.

**Composite obligation:** for async promotion, generate the `operation_id` before sending and reuse it across retries. This complements the outbox idempotency — the outbox prevents re-enqueue of a completed promotion, and `operation_id` prevents duplicate Hindsight work if the response is lost.

### Tags and visibility scoping

From `retain.mdx` L153–159: tags control visibility — a memory is only returned on recall if its tags intersect with the recall tag filter. This is Hindsight's native isolation mechanism.

**Composite obligation:** use tags for profile/session isolation: `profile:<name>`, `session:<id>`. This is a second isolation layer beyond bank names. The composite's recall must pass the correct tag filter so cross-profile reads are impossible even within a shared bank.

## Confirmation against #2 and #4 assumptions

| Assumption from prior tickets | Status | Evidence |
|---|---|---|
| `document_id` replace is idempotent (#2, #4) | ✅ Confirmed | `retain.mdx` L118; client docstring L3640 |
| Metadata round-trips through recall (#2) | ⚠️ Docs say yes; wire-fidelity unverified | `retain.mdx` L112; needs live test |
| Batch is per-item, not atomic (#2 matrix case 8) | ✅ Confirmed | sub-batch sequential processing; no rollback |
| Delete is permanent, no tombstone (#2) | ✅ Confirmed | `documents.mdx` L174 |
| `document_id` append requires document_id (#2) | ✅ Confirmed | `ValueError` in engine |
| Same-document concurrency is safe (#2) | ✅ Confirmed | `test_retain_same_document_concurrency.py` |
| `operation_id` makes async retries safe (#2) | ✅ Confirmed | `retain.mdx` L336–353 |
| Metadata stored on memory units (#2) | ✅ Confirmed | `_write_retain_outcome_metadata` engine L2552 |

## Newly visible successor questions

1. **Metadata wire-fidelity live test** — the one unverified assumption. The live spike (a future implementation ticket) must explicitly test metadata round-trip through retain → recall → list/get document → replace → delete/re-read.
2. **Tombstone semantics** — Hindsight delete is permanent; if the composite needs soft-delete/tombstone for cross-profile replay safety, it must implement them in the outbox. Decision needed: does the composite need tombstones, or is permanent delete acceptable? (Likely a promotion-policy ticket.)
3. **Batch promotion in the outbox** — #4 recommended one-at-a-time promotion; this confirms batch is safe for independent documents but not atomic. Batch promotion is a valid future optimization if the outbox handles per-item completion.
4. **Tag-based isolation vs bank-based isolation** — two isolation layers available (bank names + tags). The composite should use both, but the exact tag schema and whether to use one bank per profile or one shared bank with tag filters is a policy decision.

## Concise recommendation for issue #5

The pinned Hindsight v0.8.6 docs/source support the composite's intended semantics: `document_id` replace is the revision primitive (idempotent upsert), `delete_document` is the deletion path (permanent), metadata is documented and implemented as attached to memory units and returned on recall, batch is per-item and non-atomic across sub-batches, and `operation_id` is documented to make async retries safe. These are not wire-fidelity or live-integration proofs. Exact metadata fidelity through the full replace→recall→list→delete cycle, and the end-to-end behavior of the real adapter/client/server path, remain unverified and must be confirmed by the disposable live spike. Use tags as a second isolation layer beyond bank names only after that integration gate passes.
