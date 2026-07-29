# ADR-shaped handoff — 08 prototype acceptance

## Context

Tickets 01–07 lock a single Hermes-facing composite provider: cache-first L1 reads, L2 fallback/backfill, synchronous L1 writes, durable asynchronous outbox promotion, consolidation plus reconciliation triggers, deterministic `(profile, bank, mnemosyne_id)` identity, revision/tombstone updates, metadata provenance, stale-while-revalidate during L2 outage, bounded retry/quarantine/replay, profile/bank isolation, and one tool namespace. The earlier boundary prototype did not cover the full lifecycle.

## Alternatives

1. Extend the earlier in-memory/threaded boundary prototype — smaller diff, but it cannot demonstrate durable outbox restart/replay, revisions, tombstones, or reconciliation comprehensively.
2. Use live Hermes/Mnemosyne/Hindsight services — higher fidelity, but unavailable and unsafe for this throwaway acceptance ticket; would also conflate adapter/deployment questions with contract composition.
3. Build a separate deterministic stdlib harness with a file-backed outbox — chosen; proves the policy state machine while making its fake boundary explicit.

## Decision

Build and accept `/home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-acceptance-handoff/` as the handoff artifact. The harness passes all 11 contract cases and an independent evidence validator. Verdict: **PASS within the tested fake/stdlib boundary**. This is not a live-integration pass or production-readiness claim.

The harness models L2 `retain` only and deliberately never calls reflect. It proves L1 writes are visible before durable outbox enqueue, promotion can be triggered by consolidation and recovered by reconciliation, replay is identity-safe, changed revisions emit old-version tombstone behavior, and L2 failure is contained from foreground L1 operations.

## Consequences

- The locked policy is internally coherent enough to hand to a harden + publish successor.
- The artifact is intentionally throwaway and must not be imported as production code.
- The file-backed JSON outbox makes persistence/restart semantics testable, but is not a concurrency-safe or crash-safe production queue.
- Deterministic identity and metadata envelope fields are explicit enough to drive adapter contracts.

## Evidence

- Harness: `/home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-acceptance-handoff/acceptance_harness.py`
- Tests: `/home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-acceptance-handoff/tests/test_acceptance.py`
- Generated evidence: `/home/hermes/.scratch/mnemosyne-hindsight-promotion/prototype-acceptance-handoff/evidence.json`
- Reproducible runner: `python3 run_evidence.py`
- Independent check: `python3 verify_evidence.py`
- Syntax check: `python3 -m compileall -q acceptance_harness.py tests run_evidence.py verify_evidence.py`

## Known gaps

- No live Hermes loader/plugin lifecycle or actual one-provider guard.
- No live Mnemosyne SQLite or Hindsight HTTP/Postgres/pgvector/API schema, credentials, timeouts, rate limits, batch partial failure, or network behavior.
- JSON snapshot is not a production transactional outbox; no multi-process locking, fsync protocol, worker crash recovery at arbitrary instruction boundaries, metrics, alerts, or drift detector.
- Hindsight update/delete mechanics are represented by fake revision/tombstone records, not verified against its real API. TTL/size eviction, bootstrap seeding, and conflict review are not implemented.
- No load, latency, security, license, install/update, rollback, or deployment evidence.

## Successor hardening / publish prerequisites

1. Implement real adapter contract tests against pinned Hermes/Mnemosyne/Hindsight versions and a disposable integration environment.
2. Replace JSON outbox with transactional SQLite (or approved durable queue), per-item checkpoints, locking, fsync/recovery tests, metrics, alert thresholds, and operator replay tooling.
3. Verify Hindsight-native revision/tombstone semantics, metadata round-trip, batch partial failure, rate-limit handling, and cross-profile bank mapping.
4. Add consolidation hook and periodic reconciliation with drift/lag observability; define TTL/size/eviction and cold-start policy from measurements.
5. Add config/install fail-closed tests, secret handling, compatibility matrix, security/license review, staged rollout, rollback, and production canary evidence.
6. Keep one composite provider/tool namespace and prove no Hermes core modification is needed before publish.
