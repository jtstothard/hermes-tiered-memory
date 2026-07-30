## Parent

[#15 — Spec: Delete and tombstone ordering contract](https://github.com/jtstothard/hermes-tiered-memory/issues/15)

## What to build

Verify the complete local delete/tombstone contract and document the exact evidence and remaining production limitations.

## Acceptance criteria

- [ ] Full local test suite, compileall, and diff checks pass.
- [ ] Restart/crash-style probes cover the exercised kill points and show no one-sided tombstone/delete state.
- [ ] An independent adversarial review approves the final implementation.
- [ ] Documentation states that evidence is local SQLite only and does not claim live Hermes, Mnemosyne, Hindsight, TLS, or complete crash-recovery guarantees.
- [ ] Operation IDs, reconciliation, leases/fences, and live integration remain explicitly deferred.

## Blocked by

- #18 — Worker ordering and stale upserts
