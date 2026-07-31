## Parent

[#15 — Spec: Delete and tombstone ordering contract](https://github.com/jtstothard/hermes-tiered-memory/issues/15)

## What to build

Make concrete SQLite `memory_delete` atomically persist the L1 tombstone and a durable outbox delete operation using the existing shared transaction boundary.

## Acceptance criteria

- [ ] Given a configured concrete SQLite L1 adapter, a successful delete commits exactly one tombstone state and one delete outbox operation for the stable source identity and revision.
- [ ] Given tombstone persistence failure or outbox insertion failure, when delete returns an error, then neither state is durable.
- [ ] Given an injected commit failure, then both changes roll back and the same adapter can be retried successfully.
- [ ] Given a pre-existing incompatible outbox schema, configuration fails closed without leaving a partially attached database.
- [ ] Promotion metadata and identity semantics remain consistent with the existing atomic upsert path.
- [ ] Focused tests prove commit, rollback, retry, and ownership behavior.

## Blocked by

- #16 — Schema and tombstone state
