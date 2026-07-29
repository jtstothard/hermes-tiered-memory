# Issue #6 research: reconciliation, observability, and operator recovery contract

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`

## Scope and evidence policy

This is a grilling-resolution decision record produced through a HITL `wayfinder:grilling` session with Jay. The decisions below were explicitly selected by Jay. No production service, estate data, or credentials were used. This document defines an implementation contract; it does not claim that the implementation, tests, or live measurements already exist.

This ticket depends on:

- Issue #4 — durable outbox crash recovery and replay contract.
- Issue #5 — Hindsight revision, deletion, metadata, and batch semantics.
- Issue #7 — outbox compaction and performance policy.

For the restart path, this report is the superseding contract: its lease-expiry/CAS/fence rule replaces Issue #4 §6's former blanket `in_flight` reset wording. Issue #4 remains normative for the five-attempt retry schedule and 30-minute quarantine sweep, but not for recovery of an owned or uncertain `in_flight` row.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative. Parameters marked **implementation gate** must be selected, recorded, and tested before publish readiness; they are not permission to leave behavior unspecified.

## Decision

V1 uses complete outbox reconciliation; deterministic, sampled cross-tier drift checks; structured content-free telemetry; bounded dry-run-first operator replay; and persisted two-level alert escalation. Recovery is lease- and fence-based, not a blind `in_flight` reset. Quarantine has two deliberately different retry paths: an automatic 30-minute reconciliation retry inherited from Issue #4, and an explicitly authorized operator replay. They have different transition names, budgets, actors, and alert/audit semantics. Hindsight promotion MUST use async retain with caller-supplied operation IDs; synchronous retain is not a valid v1 path unless the alternative ledger contract in §1.2 is implemented and gated.

## 1. Reconciliation and state contract

### 1.1 State machine

Each outbox row has a monotonically increasing `state_version`, an immutable transition/audit record, and (while active) an ownership lease:

```text
pending --worker_claim(CAS)--> in_flight --worker_success(CAS + fence)--> promoted
pending --worker_retry(CAS)--> pending
in_flight --worker_retry(CAS)--> pending
in_flight --worker_terminal_failure(CAS)--> quarantined
quarantined --automatic_reconciliation_retry(CAS)--> pending
quarantined --authorized_operator_replay(CAS)--> pending
promoted ----------------------------------------------> promoted
```

`promoted` is terminal. `quarantined` is terminal for the current delivery-attempt cycle, not an immutable row. It remains excluded from ordinary draining until either `automatic_reconciliation_retry` or `authorized_operator_replay` explicitly moves it to `pending`; neither transition erases prior quarantine evidence. Automatic retry is the Issue #4 30-minute sweep and is performed by the reconciliation service without human authorization. Operator replay is a separate, authenticated, explicitly confirmed action and is never implied by a reconciliation run.

Every transition uses compare-and-set on the expected `state_version` and, for an owned row, the current fencing token. A failed CAS means another worker owns the newer state; the loser MUST abandon the local commit and emit a conflict event. Each transition records its transition name, actor (`worker`, `reconciliation_service`, or principal), run/action ID, expected and resulting state versions, and reason.

### 1.2 Lease, fencing, remote operation identity, and retain mode

An `in_flight` claim MUST persist `owner_id`, `lease_id`, `lease_expires_at`, and a monotonically increasing `fence` before the remote call. The worker renews the lease before expiry. On restart, recovery reclaims only rows whose lease is expired, using a CAS that increments `fence`; it MUST NOT reset every `in_flight` row to `pending`.

V1 MUST call Hindsight `retain` in **async mode**. The caller MUST generate and persist an `operation_id` before sending, and MUST reuse it for the same logical `(document_id, mnemosyne_rev, operation_kind)` attempt after timeout, crash, or an uncertain response. The Issue #5 evidence says caller IDs make async retries idempotent, while `operation_id` is ignored for synchronous retain. Therefore `operation_id` alone MUST NOT be presented as protection for synchronous retain; synchronous retain is prohibited in v1.

An async accepted response is not promotion proof by itself. The selected Hindsight client/provider MUST pass a capability gate before implementation: it must supply the exact status route, request/response schema, authentication/identity fields, polling interval and deadline, and mapping to `success`, `retryable_failure`, `permanent_failure`, `timeout`, and terminal `unknown`. No status API, route, or schema may be invented here. If the selected client cannot supply and test that contract, the integration is blocked. Until then, a missing, malformed, unavailable, or deadline-exceeded lookup is bounded `unknown`; it is not success and MUST NOT trigger resubmission. On a lost/uncertain response, status lookup MUST happen before a new submission, and no new submission is permitted after bounded `unknown` without an explicit operator/provider-resolution path that first establishes the prior outcome.

### 1.2.1 Attempt identity and per-document ordering

An operation attempt is identified by `(document_id, mnemosyne_rev, operation_kind, operation_id)`. The same `operation_id` MUST be reused only while that logical operation is `in_flight` or its outcome is uncertain (including process crash, timeout, lost response, or bounded `unknown`); it MUST NOT be reused for a different revision or operation kind. After a confirmed terminal retryable failure, a subsequent retry creates a new `operation_id` and retains the prior operation record and outcome. A confirmed permanent failure is terminal for that attempt and creates no automatic remote resubmission.

For each document, the durable ordering state is:

```text
no_operation -> in_flight -> success -> promoted
in_flight -> retryable_failure -> retry_pending -> in_flight (new operation_id)
in_flight -> permanent_failure -> quarantined
in_flight -> timeout -> uncertain
in_flight -> unknown -> uncertain
uncertain -> status_success -> promoted
uncertain -> status_retryable_failure -> retry_pending (new operation_id)
uncertain -> status_permanent_failure -> quarantined
uncertain -> status_timeout|status_unknown -> uncertain (no resubmission)
any non-terminal state -> newer_revision_waiting -> newer_revision_eligible only after the older operation is terminal
```

The record MUST retain every operation outcome and its evidence. A newer revision MUST wait behind an older `in_flight` or uncertain operation; it may proceed only after the older operation is confirmed terminal (success or failure) and the per-document ordering lease/CAS advances. Local promotion still requires the current row state/version and fence.

If a future implementation elects synchronous retain, it must instead add a durable deduplication ledger keyed by logical operation, persist request/response/uncertain status, perform mandatory post-timeout reconciliation/status lookup, and prove duplicate suppression under crash injection. That is an alternative **implementation gate**, not an optional fallback and not part of the v1 decision.

A fence prevents a stale worker from committing a stale **local** state transition after reclamation. It cannot cancel or roll back a remote request already accepted by Hindsight. Remote ordering/status lookup and per-document coordination above are therefore mandatory; claims that fencing alone prevents duplicate remote side effects are out of contract.

### 1.3 Automatic reconciliation retry versus operator replay

The two quarantine retry paths are defined separately:

| Path | Transition | Authorization/actor | Budget and cadence | Alert/audit semantics |
|---|---|---|---|---|
| Automatic reconciliation | `automatic_reconciliation_retry` | No human approval; `reconciliation_service` | Every 30 minutes; starts a fresh worker retry cycle with the Issue #4 five-attempt schedule; one row per sweep at most | Durable system audit event with reconciliation run ID and reason; no per-sweep warning for an unchanged condition; persistent-failure alert after 10 failed sweep cycles (~5 hours) |
| Operator replay | `authorized_operator_replay` | Authenticated principal with replay role and profile/bank authorization; explicit mutation confirmation | Bounded target snapshot, max 100 rows/invocation, 10 remote operations/minute, 30 seconds/operation, concurrency 1; all are implementation gates | Action/audit event containing principal, authorization decision, reason, policy version, target hash, dry-run/mutation result; existing warning/page remains until clear criteria are met |

Automatic reconciliation MUST NOT be described as operator replay, and operator replay MUST NOT silently bypass the automatic retry budget, remote ordering, or evidence rules. Automatic retry failure returns the row to `quarantined` after the worker cycle; it does not make the row healthy. A successful retry records the prior quarantine and the new promotion separately.

### 1.4 Complete reconciliation and drain semantics

A reconciliation run MUST inspect every non-archived outbox row and verify state, lease expiry, retry eligibility, transition integrity, and remote-operation status for unresolved async operations. It MUST NOT silently delete or rewrite a row. State transitions, claims, lease expiry/reclamation, CAS conflicts, remote operation IDs (never payloads), automatic retry outcomes, and operator actions are audit events.

The health surface MUST expose at least:

- counts by `pending`, `in_flight`, `promoted`, and `quarantined`;
- oldest pending age and oldest quarantine age;
- expired-lease and CAS-conflict counts;
- unresolved remote-operation count and status-lookup failures;
- last successful drain time and last reconciliation completion time;
- reconciliation run ID, start/end time, and status (`running`, `succeeded`, `failed`, `unknown`);
- worker liveness: heartbeat time, worker count, and stale-worker count;
- freshness age of the health snapshot;
- `queue_drained = (pending == 0 and in_flight == 0)`;
- `operationally_healthy = queue_drained and quarantined == 0 and unresolved_remote_operations == 0 and reconciliation_status == succeeded and worker_liveness == healthy`.

`queue_drained` deliberately does not hide quarantine. A quarantined row makes `operationally_healthy` false and produces a visible alert even when `queue_drained` is true. Missing or stale inputs produce `unknown`/`degraded`, never a clean result.

### 1.5 Compaction, reconciliation snapshots, and watermarks

Compaction and reconciliation MUST use a common monotonic transition watermark. Snapshot/watermark membership MUST be durable and visible to compaction: an atomic registry transaction (or equivalent lease/CAS protocol) MUST create the snapshot record, capture `(snapshot_id, transition_watermark, captured_at)`, register each selected row identity and expected `state_version`, and mark the run active before compaction can select candidates. The registry MUST expose active snapshot IDs, watermarks, row membership, lease expiry, and completion/abort state. Compaction MUST atomically check that registry membership is absent or the snapshot lease is expired and safely fenced before archival; a process-local list is insufficient. At the start of a reconciliation run, materialize the eligible row identities, current `state_version`, `mnemosyne_rev`, and category. The snapshot is immutable for that run. Rows or transitions created after the watermark are excluded from that run and MUST be eligible for the next run; the report records the watermark and exclusion count.

Compaction MUST acquire the per-row archival lease (or an equivalent CAS) and archive only rows whose last transition is at or below the compaction watermark, whose state is terminal for compaction, and which are not currently selected by a reconciliation/replay snapshot. The archive marker/deletion CAS MUST include the expected `state_version`; any concurrent transition causes compaction to skip the row and emit `compaction_skip_concurrent_update`. Reconciliation MUST treat an archived row as valid only when the archive manifest contains the same row ID, final state, state version, and digest. Compaction MUST never delete a row that is newer than its snapshot watermark or one that lacks a verified archive record.

Before compaction, write an immutable archive record containing the row identity, final state, final `state_version`, all transition IDs, attempt/operation IDs, timestamps, error taxonomy, and a canonical keyed digest of the redacted audit record. Retain the archive and audit records for the **implementation gate** retention period, recorded with the compaction run. A hot row MAY then be deleted or summarized only after verified archive write. Every deletion/expiry is itself an audit event containing archive ID, retention-policy version, actor, timestamp, watermark, and archive digest. Historical reconciliation verifies archive counts, transition continuity, and digests against the manifest. A row MUST NOT disappear from both live and archive sets.

Issue #7 may choose storage tiers and a retention period, but it MUST not weaken this concurrency or evidence-preservation invariant. Deletion is an always-ask/destructive operation at rollout and requires explicit approval and a tested recovery path.

## 2. Deterministic L1↔L2 drift checks

Full outbox reconciliation is mandatory. Direct L1↔L2 comparison is sampled in v1 to bound remote load, but the sample, denominator, and SLO calculation are reproducible.

At the start of each run, materialize the population snapshot described in §1.5. For each eligible identity, encode the tuple `(scope, identity, category, state_version, mnemosyne_rev)` with length-prefixed UTF-8 fields, sort by the canonical byte encoding, and record the snapshot ID, transition watermark, population count, category counts, and SHA-256 hash of the ordered tuple list. Select samples by the first 64 bits of `SHA-256(run_id || policy_version || category || identity)` against recorded category quotas; no unordered database iteration or unseeded random generator is allowed. The exact ordered selected list and selection hash MUST be retained.

Coverage is mechanically defined as `distinct sampled eligible tuples / distinct eligible tuples` for a run/category. The 24-hour rolling coverage denominator is the union of eligible identity tuples across all run snapshots in the window; the numerator is the union of those tuples sampled at least once. An identity that changes `state_version` or revision is a new tuple and does not inherit prior coverage. Empty categories are excluded from category ratios but recorded. Overall coverage is `sum(numerator across categories) / sum(denominator across categories)`, not an average of category percentages.

The drift result MUST contain:

- population, sample, numerator, and denominator counts by category and overall;
- per-run and 24-hour rolling coverage ratios;
- sample-policy version, snapshot ID/watermark, population hash, selected-list hash, and canonical encoding version;
- mismatch count and mismatch category;
- a keyed `metadata_digest` over canonical identity/revision/delete metadata from each side, never over memory content;
- run start/end, remote-call count, status-lookup count, and failure/unknown count.

The default policy is a 30-minute cadence, at least 100 identities per run (or all identities when the population is smaller), at least 10 identities from each non-empty category, and at least 20% of each category when that category has fewer than 500 identities. Every category quota is applied as `min(population_in_category, configured_quota)`; no quota may demand more identities than exist. Allocation is deterministic: compute category quotas from the immutable population snapshot, select by the canonical hash ordering above, and break equal hash/order ties by canonical tuple bytes. These defaults are **implementation gates**: the disposable live spike may justify a revision, but selected values, remote-load budget, and rationale must be recorded before publish readiness.

The detection SLO is also a gate and MUST state maximum time-to-detect for a seeded mismatch, required 24-hour coverage, and pass/fail calculation. The proposed v1 target is detection of every seeded mismatch in a covered category within 2 hours at the default cadence, with at least 95% rolling coverage within 24 hours. For a seeded mismatch, pass means the first result whose canonical comparison reports that identity/category as mismatched has `detected_at - injected_at <= SLO`; missing, unknown, or late observations fail. A mismatch is an observable error and enters operator recovery; it never emits payload content.

## 3. Alerts, deduplication, and hysteresis

Alert state is durable across process restarts. Each alert has a dedupe key, first-seen time, last-seen time, severity, occurrence count, and clear time. Re-emitting the same condition updates one alert rather than creating an unbounded stream. Alerts have explicit `open`, `clearing`, and `closed` states; closure is an auditable state transition, not deletion. A closed condition may create a new alert only when its dedupe condition is observed again after closure, with a new first-seen time and occurrence count.

Default thresholds are exact and configurable only through a versioned policy:

- **Warning:** one warning for each `quarantined` transition, deduped by `(row_id, state_version)`; automatic reconciliation retries do not create a new warning unless they create a new quarantine state version.
- **Page:** when any quarantine age is at least 5 hours (10 consecutive 30-minute sweeps), or when the oldest pending age increases on 3 consecutive sweeps while non-zero, or when no successful drain has completed for 5 hours.
- **Warning clear/closure:** a quarantine warning remains open while its `(row_id, state_version)` condition exists. It enters `clearing` only after that exact row/state is promoted or otherwise confirmed terminally resolved; it closes on the next successful persisted reconciliation observation that confirms resolution. A failed, stale, or unknown observation leaves it open. A later quarantine with a new state version is a new warning key.
- **Page clear/closure:** a page condition must be absent for 2 consecutive successful sweeps, and the second sweep MUST persist the closure before notification state becomes closed. A single failed/unknown sweep does not clear an existing page; any re-observed threshold during clearing returns it to open and resets the clear streak.
- **Unknown/degraded:** stale health, missing reconciliation completion, unresolved remote-operation status, stale worker heartbeat, or failed alert-state persistence is emitted as degraded/unknown and cannot be represented as healthy.

The alert event includes policy version, threshold, dedupe key, affected counts/ages, transition/run ID, and whether the condition arose from automatic retry or operator replay. Threshold overrides require an operator action and audit record; they do not silently alter the contract.

## 4. Operator replay contract

Replay requires an authenticated principal with an explicit replay role and authorization for the selected profile/bank. Authorization, target scope, policy version, reason, dry-run flag, and result are written to the audit trail. Unauthorized requests are rejected without changing row state.

A replay command MUST:

- default to dry-run and require explicit confirmation for mutation;
- accept one document or a bounded batch, never an implicit full-queue target;
- create an immutable target snapshot containing `(row_id, document_id, target_state_version, target_mnemosyne_rev, target_fence)` and a snapshot hash before processing;
- before each item, perform a CAS eligibility check against the current row `state_version` and `mnemosyne_rev` (and require the target state to be `quarantined`); no remote call is allowed when the check fails;
- record a deterministic skip reason for every ineligible item: `state_version_changed`, `revision_changed`, `not_quarantined`, `already_promoted`, `archived`, `lease_conflict`, `remote_operation_unresolved`, or `authorization_scope_changed`;
- enforce the default maximum of 100 rows per invocation, 10 remote operations per minute, 30 seconds per operation, and concurrency 1; each is an **implementation gate** that may be changed only by versioned policy and load evidence;
- acquire the same lease/fence and async operation-ID rules as normal workers, including remote status lookup before retrying an uncertain response;
- report eligible, skipped-by-reason, promoted, quarantined, rate-limited, timed-out, and CAS-conflict counts;
- preserve all prior quarantine and attempt evidence, including when replay succeeds;
- record completion, partial failure, cancellation, and authorization decisions.

A future full-queue replay requires a separate explicit control, authorization, load/rate-limit evidence, and rollback/abort procedure. It is not part of v1.

## 5. Telemetry safety contract

Telemetry is an allowlist, not a blacklist. Each event has a taxonomy (`state_transition`, `retry`, `quarantine`, `reconciliation`, `replay`, `drift`, `startup_recovery`, `shutdown`, `health`, or `alert`) and may contain only these fields:

- event ID/time, schema and policy versions;
- profile/bank scope using deployment-approved opaque identifiers;
- row/document identity in approved keyed form;
- state, operation kind, revision number, attempt number, fence, and operation ID in approved keyed/opaque form;
- counts, durations, ages, retry classification, HTTP status, and bounded error class/code;
- reconciliation/replay/alert run IDs and result status.

Raw identifiers MUST NOT be emitted. Telemetry identifiers and linkable digests MUST use HMAC-SHA-256 with a deployment secret, a versioned key ID, and a domain-separation label (for example `telemetry-row-v1` versus `telemetry-document-v1`). The key MUST be held by the deployment secret store and never written to events, reports, fixtures, or source. Key rotation changes the key ID and intentionally breaks cross-key linkability; old keys are retained only for the approved retention period and access-controlled reprocessing. `metadata_digest` MUST be keyed HMAC over a canonical, length-prefixed encoding of approved identity/revision/delete metadata, not an unkeyed hash that permits offline guessing. Operation IDs and run IDs may remain opaque random values; if they are linkable, their access is governed by the same rule.

Linkability is permitted only within the minimum deployment scope needed for deduplication, reconciliation, and incident correlation. Telemetry consumers MUST have read access only to the profile/bank scopes they operate; cross-profile joins and export to public/shared analytics are prohibited unless separately authorized and re-keyed. Audit access is narrower than operational dashboards, and raw-to-opaque lookup (if any) is a separately authorized service, never included in the event.

Field limits are mandatory: strings are bounded to 256 bytes, error messages are normalized to taxonomy codes, collections are capped at 100 items, and unknown fields are rejected. Scrubbing applies before serialization and again at sink/export boundaries. It MUST remove payloads, prompts, metadata values, URLs containing credentials, bearer/API tokens, connection strings, and arbitrary exception text. A debug flag MUST NOT bypass the allowlist.

The implementation evidence must include table-driven tests with representative secrets, credentials, prompts, payloads, nested metadata, exception text, oversized fields, HMAC key separation/rotation, and unauthorized cross-scope reads, plus sink-level assertions that forbidden values cannot appear.

## 6. Evidence and publish-readiness gates

This report records requirements, not completed evidence. Publish readiness requires two distinct evidence classes:

### Deterministic local/CI evidence

Tests and CI artifacts MUST cover lease expiry and fencing (including proof limited to stale local commits), CAS conflicts, async retain operation-ID persistence/reuse and status lookup, uncertain-response recovery, per-document remote ordering, crash recovery without duplicate logical promotion, complete reconciliation and drain/health semantics, both quarantine retry transitions and their distinct audit/alert behavior, compaction/archive/deletion watermark races, deterministic sampling and mechanically reproducible digest/coverage/SLO calculations, alert dedupe/hysteresis/restart persistence, replay limits/timeouts/authorization/audit/per-item skip reasons, and telemetry redaction/HMAC scope controls at logger and sink boundaries.

A mock provider, fake clock, synthetic rows, or fixture-only test is **synthetic evidence**. It proves code paths but does not prove that a real Hindsight integration honors async operation-id idempotency, operation status lookup, remote ordering, or rate limits.

### Disposable live integration evidence

Before publish readiness, a disposable isolated environment (no production data or credentials) MUST exercise the real Hindsight client/API against synthetic content. Fault injection is a harness contract, not an informal checklist. The harness MUST expose a named injection seam, deterministic trigger, bounded failure, expected state transitions, retry/resubmission rule, and oracle for every case; it MUST record whether the seam was reached. The minimum manifest is:

| Case / injection seam | Trigger | Expected state/retry | Oracle |
|---|---|---|---|
| request-persistence boundary | crash before durable request record | row remains recoverable; no send | row exists or atomic transaction rolled back; no lost promotion |
| operation-ID/send boundary | crash after ID persistence, before send | same ID reused; no new ID | operation record and next request share ID |
| async response boundary | drop response after send | `uncertain`; status lookup before any resubmission | no duplicate logical operation; status resolves or remains bounded unknown |
| status lookup seam | timeout, malformed response, provider 5xx | bounded `unknown`; no resubmission | unknown is visible and no send occurs |
| lease/fence seam | expire lease while worker is active | expired row reclaimed by CAS with higher fence; stale worker loses commit | stale worker cannot commit locally; remote cancellation is not claimed |
| per-document ordering seam | newer revision arrives behind unresolved older op | newer revision waits | no newer remote submission before older terminal outcome |
| provider response classes | inject 429, 5xx, and documented 4xx | retryable uses budget/Retry-After; permanent quarantines | exact classification, attempt, and transition audit |
| reconciliation retry seam | automatic sweep fails, then succeeds | quarantine remains on failure; distinct automatic retry promotes on success | correct path/budget and separate audit/alert behavior |
| replay snapshot seam | mutate target after snapshot | item skipped with specified reason; no remote call | `state_version`/revision CAS rejection and skip count |
| compaction/registry seam | race archival with active snapshot/reconciliation | compaction skips or safely fences; archive-before-delete | active membership visible; no loss from live and archive |
| drift seam | inject seeded cross-tier mismatch | mismatch reported within selected SLO | recomputed coverage/selection hashes match and detection timestamp passes |
| alert-store seam | restart/reject durable alert store | state remains open or becomes unknown, never falsely clears | dedupe and closure require policy-success observations |
| telemetry sink seam | reject or inspect emitted event | event is scrubbed/rejected safely | forbidden values absent at logger and sink boundaries |

The live run MUST execute every manifest row, with injection seam, trigger, expected state, retry policy, oracle result, and reached/not-reached status recorded before and after the run. It must also record environment identity, software/config/policy versions, timestamps, workload size, raw outcome counts, and teardown proof. If no disposable live integration or a provider-supplied status capability contract is available, readiness remains blocked; local fakes must not be relabeled as live evidence.

The live spike measures drain latency, status-lookup/drift remote-call load, async retry behavior, and validates or rejects default thresholds. Any parameter it changes becomes a versioned gate with an explicit rationale.

## Safety invariants

1. A stale worker cannot commit a stale local state transition after lease reclamation; fencing alone is not claimed to cancel remote work.
2. Remote operations for one document are ordered and uncertain async outcomes are status-reconciled before resubmission.
3. Reconciliation and compaction never silently lose, delete, or overwrite evidence, including across snapshot/watermark races.
4. Automatic reconciliation retry is the unauthorized 30-minute Issue #4 path; operator replay is separately authorized, bounded, and audited.
5. Replay is per-item CAS-eligible, bounded, rate-limited, timeout-bounded, snapshotted, audited, and idempotent.
6. Deterministic drift checks report reproducible coverage, detection SLO status, and keyed metadata digests without content.
7. Telemetry is allowlisted, bounded, HMAC-keyed, access-controlled, scrubbed, and cannot become a memory-content or credential exfiltration path.
8. `queue_drained` never hides quarantine; stale or incomplete health is degraded/unknown, not clean.

## Explicit successor gates

1. **Operation semantics:** freeze async retain mode, caller operation-ID persistence/reuse, the provider-supplied status capability (route/schema/polling/terminal mapping), per-document ordering, and the uncertain-response oracle; synchronous retain requires the separate ledger gate in §1.2.
2. **Drift policy:** approve cadence, category quotas, canonical encoding, coverage denominator/formula, detection SLO, and remote-call budget from the disposable spike.
3. **Retention and concurrency policy:** approve archive format, retention period, watermark protocol, deletion authorization, and recovery/race test with Issue #7.
4. **Alert policy:** approve threshold version, notification routing, and durable alert-state store.
5. **Replay authorization:** define the deployment's principal/RBAC boundary, target-snapshot schema, per-item skip reasons, rate limits, and audit sink.
6. **Telemetry schema and privacy:** freeze event names, HMAC/key IDs and rotation, linkability/access rules, allowlist, limits, and sink-scrubbing tests.
7. **Live readiness:** obtain disposable real-integration evidence with the listed fault injections and pass/fail oracles; synthetic tests alone cannot pass this gate.
8. **Metrics exporter:** add Prometheus/OpenTelemetry only after this event and health contract is stable.

## Concise recommendation for issue #6

Adopt complete lease/fence/CAS-protected outbox reconciliation with async Hindsight retain, persisted caller operation IDs, status lookup, and per-document remote ordering; preserve Issue #4's automatic 30-minute quarantine retry while keeping it distinct from authorized bounded operator replay; enforce per-item replay CAS checks and skip reasons; preserve evidence through watermark-coordinated archive-before-delete compaction; use canonical deterministic sampled drift checks with reproducible coverage and detection SLOs; persist deduplicated alerts with exact thresholds and hysteresis; expose fresh worker/reconciliation-aware health; use keyed, access-controlled content-free telemetry; and require both synthetic CI evidence and disposable fault-injected real-integration evidence before publish readiness.
