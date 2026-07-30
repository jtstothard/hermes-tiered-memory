## Parent

[#15 — Spec: Delete and tombstone ordering contract](https://github.com/jtstothard/hermes-tiered-memory/issues/15)

## What to build

Make the local SQLite L1 model represent deletion as a durable tombstone that is excluded from normal reads while retaining source identity and revision metadata for ordering.

## Acceptance criteria

- [ ] Given a visible source record, when it is deleted, then the source becomes tombstoned with a strictly greater revision and no longer appears in `get` or `recall`.
- [ ] Given a tombstoned source, when `get_revision` is called, then the tombstone revision remains available for stale-event checks.
- [ ] Given an unknown source, when delete is requested, then the result is deterministic and no unowned tombstone is created.
- [ ] Existing schema versions that cannot represent the contract fail closed with an explicit compatibility error; no silent migration occurs.
- [ ] Mock/custom L1 adapter behavior remains unchanged.
- [ ] Focused tests cover visible, tombstoned, unknown-delete, revision, and restart behavior.

## Blocked by

None — can start immediately.
