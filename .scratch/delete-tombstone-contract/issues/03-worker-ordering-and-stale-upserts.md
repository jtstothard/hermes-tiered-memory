## Parent

[#15 — Spec: Delete and tombstone ordering contract](https://github.com/jtstothard/hermes-tiered-memory/issues/15)

## What to build

Make outbox processing apply delete operations in revision order and prevent stale upserts from resurrecting a source after a newer tombstone is durable.

## Acceptance criteria

- [ ] Given a tombstone at revision R, an upsert below R is skipped or rejected and cannot become an applicable promotion.
- [ ] Given a delete at revision R, the downstream delete operation is processed before any applicable later operation for the same stable identity.
- [ ] Given the same delete is replayed, no duplicate durable work is created under the existing outbox identity rules.
- [ ] Given a committed tombstone and process restart, pending delete work remains discoverable and replayable.
- [ ] Given a delete followed by a later valid create/update, the later revision is not confused with stale replay.
- [ ] Focused worker tests cover ordering, stale upsert, replay, restart, and later revision behavior.

## Blocked by

- #17 — Atomic delete + outbox
