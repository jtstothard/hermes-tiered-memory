# Issue #5 research: Hindsight-native revision, deletion, metadata, and batch semantics

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`
Hindsight pin: `v0.8.6`, commit `08995e3013858e705fb4ca27c0ade3a286ef4750`.

## Scope and evidence policy

Source-only design report. Every claim is grounded in a direct read of the pinned Hindsight source tree (docs, Python client, engine, tests) at the commit above. No Hindsight server was contacted, no estate data was touched, no commit or PR was opened. This resolves the API-semantics question; it does not execute the adapters.

This ticket verifies the assumptions made by #2 (live adapter contract) and #4 (outbox contract) against the real pinned API.

## Decision

**The pinned Hindsight v0.8.6 documentation and source support the stated revision, deletion, metadata, batch, and async-operation semantics.** This is source evidence only: no adapter or Hindsight server was exercised, and exact wire fidelity remains unverified where noted below. The composite may design against these semantics, but publish readiness still requires the disposable live integration evidence specified by #2/#4/#6.

- **Revision:** `document_id` + `update_mode: "replace"` (default) — Hindsight's replace/upsert behavior deletes the prior document and all its memories, then re-processes. This is a revision/upsert primitive, not an idempotency guarantee. The pinned source/docs support reusing a caller-supplied async `operation_id` as the provider's documented retry identity, but they do not by themselves prove duplicate suppression or end-to-end idempotence; those claims remain gated on live evidence and provider status reconciliation.
- **Deletion:** `DocumentsApi.delete_document(bank_id, document_id)` — permanently removes the document and all associated memories.
- **Metadata:** the pinned docs/source support arbitrary key-value string pairs being included in the fact-extraction prompt and attached to memory units. Exact wire fidelity through the real retain/replace/recall path is unverified.
- **Batch:** `retain_batch_async` processes multiple items in one operation, auto-chunking large batches by token count into sub-batches. Per-item `document_id` supported.
- **Async operation identity (source/documentation-supported retry semantics only):** the pinned documentation says that a caller-supplied `operation_id` can be reused for an async retry and that reusing it for a different operation returns HTTP 409. This report does not treat that documentation as proof of duplicate suppression or idempotence until live provider behavior is exercised.

## API surface (verified from client + docs)

### Revision / upsert via document_id

From `retain.mdx` L114–131 and the client docstring (`memory_api.py` L3640):

> If a memory item has a `document_id` that already exists, the old document and its memory units will be deleted before creating new ones (upsert behavior).

- `update_mode: "replace"` (default) — delete old + reprocess new. **This is the revision primitive.**
- `update_mode: "append"` — concatenate new content onto existing document, reprocess combined; delta retain skips unchanged chunks so only new portion triggers LLM extraction.
- Append requires a `document_id`; without one, Hindsight raises `ValueError("update_mode='append' requires a document_id")` (`memory_engine.py` ~L3658).
- Omitting `document_id` entirely assigns a random UUID — re-ingesting the same content creates duplicate memories. **The composite must always supply a `document_id`.**

**Composite mapping (confirms the revision primitive only):** a Mnemosyne record revision maps to a Hindsight document replace. The durable outbox logical key `(document_id, mnemosyne_revision)` prevents the composite from enqueueing the same logical work twice, while the documented caller-supplied `operation_id` retry identity and provider-supported status lookup are the mechanisms to exercise for remote retry recovery. A genuine revision increment triggers a Hindsight replace; replace is not itself idempotent, and duplicate suppression/idempotence remain live-evidence gated.

### Deletion

`DocumentsApi.delete_document(bank_id, document_id)` (`documents_api.py` L50). Engine: `MemoryEngine.delete_document` (`memory_engine.py` L5934). From `documents.mdx` L174–176:

> Deleting a document permanently removes all memories extracted from it. This action cannot be undone.

**Composite mapping:** a Mnemosyne `forget(memory_id)` maps to `delete_document(bank_id, document_id)` where `document_id` is the composite's stable mapping of the Mnemosyne source ID. Deletion is permanent and irreversible — there is no tombstone/soft-delete at the Hindsight level. If the composite needs tombstone semantics (e.g. for cross-profile replay safety), it must implement them in the outbox, not rely on Hindsight.

### Metadata support and wire-fidelity gate

From `retain.mdx` L110–112:

> Arbitrary key-value string pairs... included in the fact extraction prompt... also stored on each memory unit and returned with every recalled memory, letting you do client-side filtering or static enrichment without extra lookups.

Verified by `test_fact_extraction_metadata.py` — metadata key-value pairs appear in the LLM prompt. The engine stores metadata on memory units (`_write_retain_outcome_metadata`, `memory_engine.py` L2552).

**Composite mapping (supported by the pinned source/docs; wire fidelity unverified):** carry Mnemosyne-only data in Hindsight metadata:
```json
{"origin": "mnemosyne", "profile": "<profile>", "bank": "<bank>", "mnemosyne_id": "<id>", "mnemosyne_revision": <int>}
```
**Unverified at wire level (deferred to live spike):** whether metadata survives a `replace` through retain → recall → list/get document unchanged (same keys/values). The docs describe storage and recall behavior, and the source shows metadata attachment, but only a live test confirms exact wire fidelity. The #4 outbox contract MUST treat metadata fidelity as a publish gate: promotion cannot be declared publish-ready until the disposable live integration test verifies the exact metadata contract used by the outbox.

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

### Async operation_id retry semantics (source/documentation-supported; live proof pending)

From `retain.mdx` L336–353:
- `async: true` returns immediately with an `operation_id`.
- The pinned documentation states that re-submitting an async request with the same caller-supplied `operation_id` returns the original operation and creates no new work.
- Reusing an `operation_id` that belongs to a different operation returns HTTP 409.
- `operation_id` is ignored for synchronous retain.

**Composite obligation:** for async promotion, generate and durably persist the `operation_id` before sending, then reuse it for the same logical retry while status reconciliation is performed. This is a source/documentation-supported retry contract to verify, not a proven duplicate-suppression or idempotence guarantee. The outbox prevents duplicate logical enqueue; only live provider evidence can establish what remote work occurs after a lost response.

### Tags and visibility scoping

From `retain.mdx` L153–159: tags control visibility — a memory is only returned on recall if its tags intersect with the recall tag filter. This is Hindsight's native isolation mechanism.

**Composite obligation:** use tags for profile/session isolation: `profile:<name>`, `session:<id>`. This is a second isolation layer beyond bank names. The composite's recall must pass the correct tag filter so cross-profile reads are impossible even within a shared bank.

## Confirmation against #2 and #4 assumptions

| Assumption from prior tickets | Status | Evidence |
|---|---|---|
| `document_id` replace is the revision/upsert primitive (#2, #4) | ✅ Source-supported; not an idempotency guarantee | `retain.mdx` L118; client docstring L3640 |
| Metadata is documented/source-supported; exact wire fidelity (#2, #4) | ⚠️ Unverified; explicit #4 publish gate | `retain.mdx` L112; needs live test |
| Batch is per-item, not atomic (#2 matrix case 8) | ✅ Confirmed | sub-batch sequential processing; no rollback |
| Delete is permanent, no tombstone (#2) | ✅ Confirmed | `documents.mdx` L174 |
| `document_id` append requires document_id (#2) | ✅ Confirmed | `ValueError` in engine |
| Same-document concurrency is safe (#2) | ✅ Confirmed | `test_retain_same_document_concurrency.py` |
| `operation_id` is documented as the async retry identity (#2) | ✅ Source/documentation-supported; duplicate suppression and idempotence unverified | `retain.mdx` L336–353; needs live test |
| Metadata stored on memory units (#2) | ✅ Confirmed | `_write_retain_outcome_metadata` engine L2552 |

## Newly visible successor questions

1. **Metadata wire-fidelity live test** — the unverified assumption. The live spike (a future implementation ticket) must explicitly test metadata through retain → recall → list/get document → replace → delete/re-read; this is an explicit #4 publish gate.
2. **Tombstone semantics** — Hindsight delete is permanent; if the composite needs soft-delete/tombstone for cross-profile replay safety, it must implement them in the outbox. Decision needed: does the composite need tombstones, or is permanent delete acceptable? (Likely a promotion-policy ticket.)
3. **Batch promotion in the outbox** — #4 recommended one-at-a-time promotion; this confirms batch is safe for independent documents but not atomic. Batch promotion is a valid future optimization if the outbox handles per-item completion.
4. **Tag-based isolation vs bank-based isolation** — two isolation layers available (bank names + tags). The composite should use both, but the exact tag schema and whether to use one bank per profile or one shared bank with tag filters is a policy decision.

## Concise recommendation for issue #5

The pinned Hindsight v0.8.6 docs/source support the composite's intended revision, deletion, metadata, batch, and async-operation primitives: `document_id` replace is the revision/upsert primitive, not an idempotency guarantee; `delete_document` is the deletion path (permanent); metadata is documented/source-supported as attached to memory units; batch is per-item and non-atomic across sub-batches; and caller-supplied `operation_id` is documented as an async retry identity. The outbox MUST persist and reuse that identity and perform provider status lookup, but this source-only report does not claim duplicate suppression or idempotence until live evidence verifies them. Exact metadata wire fidelity and the end-to-end real adapter/client/server behavior remain unverified and are an explicit #4 publish gate for the disposable live spike. Use tags as a second isolation layer beyond bank names only after that integration gate passes.
