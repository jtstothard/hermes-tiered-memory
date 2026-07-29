Type: grilling
Status: resolved
Claimed by: default router session
Blocked by: 03

## Question

How are Mnemosyne memories identified, deduplicated, updated, deleted, and reconciled when Hindsight already contains the same or a conflicting fact?

**Completion test:** an identity and conflict matrix defines stable IDs, idempotency keys, update/delete semantics, and conflict outcomes; replaying the same promotion batch is provably safe and produces no duplicate durable facts.

## Answer

**Stable identity:** use the deterministic composite `(profile, bank, mnemosyne_id)`. Carry it in the Hindsight metadata provenance envelope and use it as the promotion idempotency key.

| Situation | Policy |
|---|---|
| Same identity + same payload | Idempotent no-op. |
| Same identity + changed payload | Idempotent replacement/revision; tombstone the old version and retain revision provenance. |
| Delete/supersession | Emit an idempotent tombstone/invalidation under the same identity. |
| Similar/conflicting fact with different origin identity | Never overwrite it; retain both and mark a provenance conflict for later review/reconciliation. |
| Hindsight-generated IDs | Treat as derived references, never as the Mnemosyne promotion identity. |

This makes replay safe without semantic-similarity overwrite risk. Hindsight remains the durable authority, while origin metadata gives the composite a deterministic way to update only records it owns.

**Limitation:** the exact Hindsight API mechanics for durable tombstones and replacement are implementation work; ticket 06 defines retry/checkpoint behavior.
