# Issue #6 research: reconciliation, observability, and operator recovery contract

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`

## Scope and evidence policy

This is a grilling-resolution decision record produced through a HITL `wayfinder:grilling` session with Jay. The decisions below were explicitly selected by Jay. No production service, estate data, or credentials were used. This document defines an implementation contract; it does not claim that the implementation, tests, or live measurements already exist.

This ticket depends on:

- Issue #4 — durable outbox crash recovery and replay contract.
- Issue #5 — Hindsight revision, deletion, metadata, and batch semantics.
- Issue #7 — outbox compaction and performance policy.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative. Parameters marked **implementation gate** must be selected, recorded, and tested before publish readiness; they are not permission to leave behavior unspecified.

## Decision

V1 uses complete outbox reconciliation; deterministic, sampled cross-tier drift checks; structured content-free telemetry; bounded dry-run-first replay; and persisted two-level alert escalation. Recovery is lease- and fence-based, not a blind `in_flight` reset. Quarantine is retryable only through an explicit, audited transition that preserves prior evidence.

## 1. Reconciliation and state contract

### 1.1 State machine

Each outbox row has a monotonically increasing `state_version`, an immutable transition/audit record, and (while active) an ownership lease:

```text
pending --claim(CAS)--> in_flight --success(CAS + fence)--> promoted
pending --attempt failure--> pending       (retryable failure)
in_flight --attempt failure--> pending      (retryable failure)
in_flight --terminal failure(CAS)--> quarantined
quarantined --authorized replay(CAS)--> pending
promoted --------------------------------> promoted
```

`promoted` is terminal. `quarantined` is terminal for the current delivery attempt, not an immutable row: it remains excluded from automatic draining until an authorized replay explicitly moves it to `pending`. A replay MUST NOT erase the quarantine transition or its failure evidence. A row may be replayed only after the operator has selected it or a bounded, snapshotted batch.

Every transition uses compare-and-set on the expected `state_version` and, for an owned row, the current fencing token. A failed CAS means another worker owns the newer state; the loser MUST abandon the side effect and emit a conflict event.

### 1.2 Lease, fencing, and remote operation identity

An `in_flight` claim MUST persist `owner_id`, `lease_id`, `lease_expires_at`, and a monotonically increasing `fence` before the remote call. The worker renews the lease before expiry. On restart, recovery reclaims only rows whose lease is expired, using a CAS that increments `fence`; it MUST NOT reset every `in_flight` row to `pending`.

The row (or an append-only attempt record referenced by it) MUST persist a Hindsight `operation_id` before the first remote call. Retries after timeout, crash, or uncertain response MUST reuse that operation ID for the same logical `(document_id, mnemosyne_rev, operation_kind)` attempt. The success transition is accepted only with the current fence and operation ID. If Hindsight cannot provide idempotency for that operation, an equivalent durable deduplication ledger is an **implementation gate**; blind re-execution is prohibited.

The lease/fence/operation identity is what prevents a stale worker from promoting a row after a newer worker has reclaimed it. Reconciliation reports expired leases and CAS conflicts separately from ordinary retry failures.

### 1.3 Complete reconciliation and drain semantics

A reconciliation run MUST inspect every non-archived outbox row and verify state, lease expiry, retry eligibility, and transition integrity. It MUST NOT silently delete or rewrite a row. State transitions, claims, lease expiry/reclamation, CAS conflicts, remote operation IDs (never payloads), and operator actions are audit events.

The health surface MUST expose at least:

- counts by `pending`, `in_flight`, `promoted`, and `quarantined`;
- oldest pending age and oldest quarantine age;
- expired-lease and CAS-conflict counts;
- last successful drain time and last reconciliation completion time;
- reconciliation run ID, start/end time, and status (`running`, `succeeded`, `failed`, `unknown`);
- worker liveness: heartbeat time, worker count, and stale-worker count;
- freshness age of the health snapshot;
- `queue_drained = (pending == 0 and in_flight == 0)`;
- `operationally_healthy = queue_drained and quarantined == 0 and reconciliation_status == succeeded and worker_liveness == healthy`.

`queue_drained` deliberately does not hide quarantine. A quarantined row makes `operationally_healthy` false and produces a visible alert even when `queue_drained` is true. Missing or stale inputs produce `unknown`/`degraded`, never a clean result.

### 1.4 Compaction, retention, and historical reconciliation

Compaction MUST distinguish live operational state from historical evidence:

1. Before compaction, write an immutable archive record containing the row identity, final state, all transition IDs, attempt/operation IDs, timestamps, error taxonomy, and a canonical SHA-256 digest of the redacted audit record.
2. Retain the archive and audit records for the **implementation gate** retention period, which MUST be recorded with the compaction run. The hot outbox row MAY then be deleted or summarized only after a successful, verified archive write.
3. Every deletion/expiry is itself an audit event containing the archive ID, retention-policy version, actor, timestamp, and archive digest. No content is required or permitted.
4. Live reconciliation covers non-archived rows. Historical reconciliation periodically verifies archive counts, transition continuity, and digests against the compaction manifest. A live row MUST NOT disappear from both the live set and archive set.

Issue #7 may choose storage tiers and a retention period, but it MUST not weaken this evidence-preservation invariant. Deletion is an always-ask/destructive operation at rollout and requires explicit approval and a tested recovery path.

## 2. Deterministic L1↔L2 drift checks

Full outbox reconciliation is mandatory. Direct L1↔L2 comparison is sampled in v1 to bound remote load, but the sample is reproducible and measurable.

At the start of each run, materialize a population snapshot of eligible identities and category labels (`recent_write`, `revision`, `delete`, `quarantined_or_replayed`). Sort by a canonical identity key and record the snapshot ID, population count, category counts, and hash of the ordered identity list. Select samples by a stable hash of `(run_id, identity, policy_version)` with recorded quotas; do not use unordered database iteration or an unseeded random generator.

The drift result MUST contain:

- population and sample counts by category;
- coverage ratio by category and overall;
- sample-policy version and population-snapshot hash;
- mismatch count and mismatch category;
- a SHA-256 `metadata_digest` over canonical identity/revision/delete metadata from each side, never over memory content;
- run start/end and remote-call count.

The default policy is a 30-minute cadence, at least 100 identities per run (or all identities when the population is smaller), with at least 10 identities from each non-empty category and at least 20% of each category when that category has fewer than 500 identities. These defaults are **implementation gates**: the disposable live spike may justify a revision, but the selected values, remote-load budget, and reason must be recorded before publish readiness.

The detection SLO is also a gate and MUST be explicit before release: the selected policy must state the maximum permitted time-to-detect for a seeded mismatch, the required coverage target, and the pass/fail calculation. A proposed v1 target is detection of every seeded mismatch in a covered category within 2 hours at the default cadence, with at least 95% of eligible identities covered within 24 hours. A mismatch is an observable error and enters operator recovery; it never emits payload content.

## 3. Alerts, deduplication, and hysteresis

Alert state is durable across process restarts. Each alert has a dedupe key, first-seen time, last-seen time, severity, occurrence count, and clear time. Re-emitting the same condition updates one alert rather than creating an unbounded stream.

Default thresholds are exact and configurable only through a versioned policy:

- **Warning:** one warning for each `quarantined` transition, deduped by `(row_id, state_version)`.
- **Page:** when any quarantine age is at least 5 hours (10 consecutive 30-minute sweeps), or when the oldest pending age increases on 3 consecutive sweeps while non-zero, or when no successful drain has completed for 5 hours.
- **Clear:** a page condition must be absent for 2 consecutive successful sweeps. A single failed/unknown sweep does not clear an existing page.
- **Unknown/degraded:** stale health, missing reconciliation completion, stale worker heartbeat, or failed alert-state persistence is emitted as degraded/unknown and cannot be represented as healthy.

The alert event includes policy version, threshold, dedupe key, affected counts/ages, and reconciliation run ID. Threshold overrides require an operator action and audit record; they do not silently alter the contract.

## 4. Operator replay contract

Replay requires an authenticated principal with an explicit replay role and authorization for the selected profile/bank. Authorization, target scope, policy version, reason, dry-run flag, and result are written to the audit trail. Unauthorized requests are rejected without changing row state.

A replay command MUST:

- default to dry-run and require an explicit confirmation for mutation;
- accept one document or a bounded batch, never an implicit full-queue target;
- create an immutable target snapshot (IDs, revisions, and snapshot hash) before processing;
- enforce the default maximum of 100 rows per invocation, 10 remote operations per minute, 30 seconds per operation, and concurrency 1; each is an **implementation gate** that may be changed only by versioned policy and load evidence;
- acquire the same lease/fence and persist/reuse the same operation ID rules as normal workers;
- report eligible, skipped, promoted, quarantined, rate-limited, timed-out, and CAS-conflict counts;
- preserve all prior quarantine and attempt evidence, including when a replay succeeds;
- record completion, partial failure, cancellation, and authorization decisions.

A future full-queue replay requires a separate explicit control, authorization, load/rate-limit evidence, and rollback/abort procedure. It is not part of v1.

## 5. Telemetry safety contract

Telemetry is an allowlist, not a blacklist. Each event has a taxonomy (`state_transition`, `retry`, `quarantine`, `reconciliation`, `replay`, `drift`, `startup_recovery`, `shutdown`, `health`, or `alert`) and may contain only these fields:

- event ID/time, schema and policy versions;
- profile/bank scope (using deployment-approved opaque identifiers);
- row/document identity in approved hashed or opaque form;
- state, operation kind, revision number, attempt number, fence, and operation ID;
- counts, durations, ages, retry classification, HTTP status, and bounded error class/code;
- reconciliation/replay/alert run IDs and result status.

Field limits are mandatory: strings are bounded to 256 bytes, error messages are normalized to taxonomy codes, collections are capped at 100 items, and unknown fields are rejected. Scrubbing applies before serialization and again at sink/export boundaries. It MUST remove payloads, prompts, metadata values, URLs containing credentials, bearer/API tokens, connection strings, and arbitrary exception text. Identifiers are sensitive operational data and must follow deployment access controls.

The implementation evidence must include table-driven tests with representative secrets, credentials, prompts, payloads, nested metadata, exception text, and oversized fields, plus sink-level assertions that forbidden values cannot appear. A debug flag MUST NOT bypass the allowlist.

## 6. Evidence and publish-readiness gates

This report records requirements, not completed evidence. Publish readiness requires two distinct evidence classes:

### Deterministic local/CI evidence

Tests and CI artifacts MUST cover lease expiry and fencing, CAS conflicts, persisted/reused operation IDs, crash recovery without duplicate logical promotion, complete reconciliation and drain/health semantics, quarantine replay transitions, compaction/archive/deletion audit, deterministic sampling and digest/coverage calculations, alert dedupe/hysteresis/restart persistence, replay limits/timeouts/authorization/audit, and telemetry redaction at logger and sink boundaries.

A mock provider, fake clock, synthetic rows, or fixture-only test is **synthetic evidence**. It proves code paths but does not prove that a real Hindsight integration honors operation-id idempotency or remote rate limits.

### Disposable live integration evidence

Before publish readiness, a disposable isolated environment (no production data or credentials) MUST exercise the real Hindsight client/API against synthetic content and verify operation-id retry behavior, lease/restart recovery, actual remote error classification, rate limiting, drift digest behavior, and measured drain/health freshness. The run must record environment identity, software/config versions, timestamps, workload size, results, and teardown proof. If no disposable live integration is available, readiness remains blocked; local fakes must not be relabeled as live evidence.

The live spike measures drain latency, drift remote-call load, and validates or rejects the default thresholds. Any parameter it changes becomes a versioned gate with an explicit rationale.

## Safety invariants

1. A stale worker cannot commit a state transition after lease reclamation.
2. Reconciliation and compaction never silently lose, delete, or overwrite evidence.
3. Quarantine is immediately visible, retryable only through authorized replay, and never cleared by request alone.
4. Replay is bounded, rate-limited, timeout-bounded, snapshotted, audited, and idempotent.
5. Deterministic drift checks report coverage, detection SLO status, and metadata digests without content.
6. Telemetry is allowlisted, bounded, scrubbed, and cannot become a memory-content or credential exfiltration path.
7. `queue_drained` never hides quarantine; stale or incomplete health is degraded/unknown, not clean.

## Explicit successor gates

1. **Operation semantics:** confirm Hindsight operation-id idempotency or approve a durable deduplication ledger.
2. **Drift policy:** approve cadence, category quotas, coverage target, detection SLO, and remote-call budget from the disposable spike.
3. **Retention policy:** approve archive format, retention period, deletion authorization, and recovery test with Issue #7.
4. **Alert policy:** approve threshold version, notification routing, and durable alert-state store.
5. **Replay authorization:** define the deployment's principal/RBAC boundary and audit sink.
6. **Telemetry schema:** freeze event names, allowlist, identifier treatment, limits, and sink scrubbing tests.
7. **Live readiness:** obtain disposable real-integration evidence; synthetic tests alone cannot pass this gate.
8. **Metrics exporter:** add Prometheus/OpenTelemetry only after this event and health contract is stable.

## Concise recommendation for issue #6

Adopt complete, lease/fence/CAS-protected outbox reconciliation with persisted Hindsight operation IDs; make quarantine retryable only through audited bounded replay; preserve evidence through archive-before-delete compaction; use deterministic sampled drift checks with coverage, detection SLO, and metadata digests; persist deduplicated alerts with exact thresholds and hysteresis; expose fresh worker/reconciliation-aware health; and require both synthetic CI evidence and disposable real-integration evidence before publish readiness.
