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

### 1.0 Durable schema and atomic relationships

The contract is implemented as durable relational records; an in-memory queue, process-local retry counter, or process-local lease is not sufficient. The following logical schema is normative (column types are implementation-specific, but nullability, keys, and semantics are not):

```text
outbox
  row_id                PRIMARY KEY
  document_id           NOT NULL
  mnemosyne_id          NOT NULL
  mnemosyne_rev         NOT NULL
  content_snapshot      NOT NULL
  metadata_snapshot     NOT NULL
  state                 NOT NULL             -- pending | in_flight | uncertain | promoted | quarantined
  state_version         NOT NULL             -- increments on every state/lease/fence mutation
  terminal_outcome      NOT NULL DEFAULT 'none' -- none | confirmed_permanent | automatic_retry_quarantine | unresolved_outcome_quarantine
                         -- durable per-row/per-revision classification; not the document_ordering summary
  owner_id, lease_id    nullable
  lease_expires_at      nullable
  fence                 NOT NULL DEFAULT 0
  next_retry_at         nullable
  attempt_no            NOT NULL DEFAULT 0  -- 1..5 within the current worker cycle
  retry_cycle_no        NOT NULL DEFAULT 0  -- increments on each quarantine retry path
  quarantine_origin_at  nullable            -- durable first quarantine age origin
  last_error_class      nullable
  last_http_status      nullable
  created_at, updated_at NOT NULL

operation_attempt
  attempt_id            PRIMARY KEY
  row_id                FK outbox(row_id) NOT NULL
  document_id           NOT NULL
  mnemosyne_rev         NOT NULL
  operation_kind        NOT NULL
  operation_id          NOT NULL
  retry_cycle_no        NOT NULL             -- copied from outbox at slot allocation
  attempt_no            NOT NULL
  snapshot_id           nullable             -- set for snapshot-owned work
  run_id                NOT NULL             -- reconciliation/replay run or worker action
  snapshot_fence        nullable             -- null only for non-snapshot work
  row_fence             NOT NULL
  ordering_fence        NOT NULL
  status                NOT NULL             -- in_flight | success | retryable_failure | permanent_failure | uncertain
  request_recorded_at   NOT NULL
  outcome_recorded_at   nullable
  provider_status_ref   nullable
  error_class/http_status nullable
  UNIQUE(operation_id)
  UNIQUE(row_id, retry_cycle_no, attempt_no)

transition_audit
  transition_id         PRIMARY KEY
  row_id                FK outbox(row_id) NOT NULL
  expected_state_version, resulting_state_version NOT NULL
  from_state, to_state, transition_name NOT NULL
  transition_seq        NOT NULL             -- allocated from transition_sequence
  actor, run_or_action_id, reason, recorded_at, fence NOT NULL

transition_sequence
  sequence_name         PRIMARY KEY        -- one global sequence per bank/profile
  next_seq              NOT NULL

// One durable ordering ledger row per document. It is the serialization point
// for revisions; it is not reconstructed from the outbox or audit log.
document_ordering
  document_id           PRIMARY KEY
  ordering_version      NOT NULL
  eligible_revision     nullable
  terminal_revision     nullable
  terminal_outcome      nullable             -- promoted | failed | quarantined
  owner_id, lease_id    nullable
  lease_expires_at      nullable
  fence                 NOT NULL DEFAULT 0
  updated_at             NOT NULL
  UNIQUE(document_id, eligible_revision)

sweep_run
  run_id                PRIMARY KEY
  run_kind              NOT NULL             -- automatic_reconciliation
  owner_id, lease_id    NOT NULL
  fence                 NOT NULL
  status                 NOT NULL             -- running | succeeded | failed | aborted | unknown
  cadence_slot          NOT NULL
  started_at, lease_expires_at NOT NULL
  completed_at           nullable
  transition_watermark  nullable
  quota_policy_version, audit_scope, alert_scope NOT NULL
  UNIQUE(run_kind, cadence_slot) WHERE status = 'running'

snapshot_registry
  snapshot_id           PRIMARY KEY
  run_id                FK sweep_run(run_id) nullable
  owner_id, fence       NOT NULL
  transition_watermark, captured_at, lease_expires_at NOT NULL
  status                 NOT NULL             -- active | completed | aborted
  completed_at/aborted_at nullable
snapshot_member(snapshot_id, row_id, expected_state_version, expected_rev, category,
                PRIMARY KEY(snapshot_id, row_id))
```

`outbox.terminal_outcome` is the canonical per-row/per-revision classification used for selection. `none` is required for all non-quarantined states (`pending`, `in_flight`, `uncertain`, and `promoted`); `confirmed_permanent` marks a confirmed permanent failure, `automatic_retry_quarantine` marks ordinary five-attempt exhaustion, and `unresolved_outcome_quarantine` marks only the explicitly authorized unresolved-outcome quarantine. The `document_ordering.terminal_outcome` value is only an ordering summary and MUST NOT be used to select sweep or replay targets.

Ledger bootstrap is explicit: when the first revision observed for a `document_id` is inserted, create its `document_ordering` row with `eligible_revision` equal to that revision and both `terminal_revision` and `terminal_outcome` NULL. That first revision is eligible without a prior terminal revision. The prior-terminal requirement applies only when a newer revision is added: it remains waiting until the currently eligible older revision has a committed terminal outcome and the ordering ledger advances under CAS. A missing ledger row is therefore an initialization condition, not evidence that the first revision is blocked.

`outbox` is the current materialized state; `operation_attempt` is append-only history and permits multiple attempts for one `(document_id, mnemosyne_rev, operation_kind)`. Therefore enqueue uniqueness is the logical source identity `(document_id, mnemosyne_rev)` (or an explicitly versioned equivalent), never an attempt number, and it MUST NOT prevent later attempt records. At slot allocation, `operation_attempt.retry_cycle_no` MUST be copied from the same locked outbox row that supplies `attempt_no`; the pair is immutable attempt lineage, not caller input. The `(row_id, retry_cycle_no, attempt_no)` unique constraint is the durable attempt slot: allocating a slot and inserting its operation record MUST be one transaction. A duplicate slot is rejected by the database, emits `duplicate_attempt_slot` with the existing slot/operation ID and actor/run ID, and MUST NOT send remotely or advance row state; retries must reread the existing record and reconcile it. The source L1 write, outbox row creation/upsert, and initial transition audit are one atomic transaction. Each operation request record, its operation ID, the current `snapshot_id`/`run_id` and fences, and the `in_flight` transition are one durable transaction before any remote send. An outcome and the corresponding local transition/audit are committed atomically with CAS on `state_version` and all applicable fences. Foreign keys and the snapshot membership primary key MUST be enforced; deleting an outbox row requires the archive protocol in §1.5. Every transition obtains and persists the next `transition_seq` in the same transaction as its state change and audit; allocation is a durable CAS/row lock, so sequences are monotonic and never process-local. A failed transaction consumes no externally visible sequence; a committed transition has exactly one sequence and audit record.

### 1.1 State machine

Each outbox row has a monotonically increasing `state_version`, an immutable transition/audit record, and (while active) an ownership lease:

```text
pending --worker_claim(CAS)--> in_flight --worker_success(CAS + fence)--> promoted
pending --worker_retry(CAS)--> pending
in_flight --worker_retry(CAS)--> pending
in_flight --remote_outcome_uncertain(CAS)--> uncertain
in_flight --worker_terminal_failure(CAS)--> quarantined
uncertain --status_success(CAS + fence)--> promoted
uncertain --status_retryable_failure(CAS + fence)--> pending
uncertain --status_permanent_failure(CAS + fence)--> quarantined
uncertain --bounded_unknown(CAS)--> uncertain
uncertain --authorized_unresolved_quarantine(CAS + state_version + fence)--> quarantined
quarantined --automatic_reconciliation_retry(CAS)--> pending
quarantined --authorized_operator_replay(CAS)--> pending
promoted ----------------------------------------------> promoted
```

`promoted` is terminal. `uncertain` is a durable non-terminal state for a possibly accepted remote operation whose outcome has not been established. It remains excluded from ordinary draining and blocks newer revisions until status reconciliation resolves it or an explicitly authorized provider/operator resolution establishes a safe next state. `quarantined` is terminal for the current delivery-attempt cycle, not an immutable row. On every transition into `quarantined`, the outbox `terminal_outcome` MUST be set to exactly one of `confirmed_permanent`, `automatic_retry_quarantine`, or `unresolved_outcome_quarantine`; non-quarantined rows use `none`. It remains excluded from ordinary draining until either `automatic_reconciliation_retry` or `authorized_operator_replay` explicitly moves it to `pending`; neither transition erases prior quarantine evidence. Automatic retry is the Issue #4 30-minute sweep and is performed by the reconciliation service without human authorization. Operator replay is a separate, authenticated, explicitly confirmed action and is never implied by a reconciliation run.

Every transition uses compare-and-set on the expected `state_version` and, for an owned row, the current fencing token. A failed CAS means another worker owns the newer state; the loser MUST abandon the local commit and emit a conflict event. Each transition records its transition name, actor (`worker`, `reconciliation_service`, or principal), run/action ID, expected and resulting state versions, and reason.

### 1.2 Lease, fencing, remote operation identity, and retain mode

An `in_flight` claim MUST persist `owner_id`, `lease_id`, `lease_expires_at`, and a monotonically increasing `fence` before the remote call. The worker renews the lease before expiry. On restart, recovery reclaims only rows whose lease is expired, using a CAS that increments `fence`; it MUST NOT reset every `in_flight` row to `pending`.

V1 MUST call Hindsight `retain` in **async mode**. The caller MUST generate and persist an `operation_id` before sending, and MUST reuse it for the same logical `(document_id, mnemosyne_rev, operation_kind)` attempt after timeout, crash, or an uncertain response. The Issue #5 source/docs describe caller IDs as the provider's async retry mechanism, but this report does not elevate that documentation to a duplicate-suppression proof: live provider behavior and status lookup are required. Hindsight `replace` is a revision/upsert primitive, not an idempotency guarantee, and `operation_id` is ignored for synchronous retain. Therefore `operation_id` alone MUST NOT be presented as protection for synchronous retain; synchronous retain is prohibited in v1.

An async accepted response is not promotion proof by itself. The selected Hindsight client/provider MUST pass a capability gate before implementation: it must supply the exact status route, request/response schema, authentication/identity fields, polling interval and deadline, and mapping to `success`, `retryable_failure`, `permanent_failure`, `timeout`, and unresolved `unknown`. No status API, route, or schema may be invented here. If the selected client cannot supply and test that contract, the integration is blocked. Until then, a missing, malformed, unavailable, or deadline-exceeded lookup is recorded on the outbox as durable `uncertain`; it is not success and MUST NOT trigger resubmission. The row remains non-terminal, excluded from ordinary drain, visible as degraded/alerted, and subject to a bounded next reconciliation deadline. On a lost/uncertain response, status lookup MUST happen before a new submission, and no new submission is permitted after bounded `unknown` without an explicit operator/provider-resolution path that first establishes the prior outcome. If that path cannot establish the prior outcome, the authorized action MUST use the explicit `authorized_unresolved_quarantine` transition below; it MUST NOT silently drop the row or treat it as promoted.

#### Explicit unresolved-outcome quarantine

When the provider-resolution deadline expires without a provider-confirmed outcome, `bounded_unknown` is not permission to abandon or resubmit the operation. The row remains `uncertain` until an authenticated principal or authorized provider-resolution service invokes `authorized_unresolved_quarantine`. The action MUST carry an authorization decision covering the profile/bank, row, and unresolved-outcome policy; record the actor, authorization scope/policy version, operation ID, provider status evidence, lookup/deadline history, and reason; and perform one CAS transaction requiring the expected `state_version`, current `row_fence`, current per-document `ordering_version`/`ordering_fence`, and an active owner/lease where applicable. A failed CAS changes nothing and emits a conflict audit event.

On a successful CAS, the row changes `uncertain -> quarantined`, `state_version` and the row fence advance, the transition audit and attempt outcome are committed with one `transition_seq`, and the unresolved evidence is retained. The same transaction advances the `document_ordering` ledger with `terminal_outcome=quarantined`, allowing a newer revision to become eligible only after the committed ordering advance; it does not claim that the remote operation failed or was cancelled. The transition opens/updates a durable quarantine warning and unresolved-outcome alert, leaves `operationally_healthy=false`, and reports the row as quarantined rather than drained-away. `queue_drained` may become true because `quarantined` is excluded from that predicate, but health remains degraded/unhealthy and the row remains visible in reconciliation and alert counts. No automatic ordinary drain may submit it.

Later replay is permitted only through `authorized_operator_replay` (or the separately defined automatic reconciliation retry after quarantine), with fresh authorization, target state/version/fence CAS, preserved prior attempt evidence, and a new operation ID. Replay MUST status-look up the old operation whenever the provider can resolve it and MUST NOT infer idempotency from the document ID, fence, or quarantine transition. An unauthorized, unconfirmed, stale, or failed-CAS action leaves the current row state unchanged (`uncertain` before the explicit quarantine, or `quarantined` afterward) and its alert open; it cannot silently advance ordering or create a remote attempt.

### 1.2.1 Attempt identity and per-document ordering

An operation attempt is identified by `(document_id, mnemosyne_rev, operation_kind, operation_id)`. The same `operation_id` MUST be reused only while that logical operation is `in_flight` or its outcome is uncertain (including process crash, timeout, lost response, or bounded `unknown`); it MUST NOT be reused for a different revision or operation kind. After a confirmed terminal retryable failure, a subsequent retry creates a new `operation_id` and retains the prior operation record and outcome. A confirmed permanent failure is terminal for that attempt and creates no automatic remote resubmission. When work is snapshot-owned, `snapshot_id` and `run_id` plus `snapshot_fence`, `row_fence`, and `ordering_fence` are persisted on the attempt before send; non-snapshot work persists `run_id` when applicable and records null `snapshot_id`/`snapshot_fence`.

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
uncertain -> status_timeout|status_unknown -> uncertain (durable non-terminal; alert/reconcile again, no resubmission)
any non-terminal state -> newer_revision_waiting -> newer_revision_eligible only after the older operation is terminal
```

The durable `document_ordering` row is the per-document serialization ledger. For every document, `ordering_version`, `eligible_revision`, `terminal_revision`, and `terminal_outcome` are updated under owner/fence/CAS; the ledger is never inferred from a scan of outbox rows. A coordinator claims it with `UPDATE ... WHERE document_id=? AND (owner_id IS NULL OR lease_expires_at <= :now OR owner_id=:me) AND fence=:expected_fence`, setting `owner_id`, `lease_id`, `lease_expires_at`, and (only on takeover) `fence=fence+1`; the claim and ordering mutation allocate one transition sequence transactionally. Renewal is `UPDATE ... WHERE document_id=? AND owner_id=:me AND lease_id=:lease AND fence=:fence AND lease_expires_at>:now`, extending the expiry without changing the fence. Takeover is permitted only by `... WHERE owner_id IS NOT NULL AND lease_expires_at<=:now AND fence=:old_fence`, and must increment the fence; a zero-row update is lease loss. Every submit/advance/terminal CAS MUST include `document_id`, `owner_id`, `lease_id`, `fence`, `ordering_version`, and `lease_expires_at>:now`. A first revision is eligible when it bootstraps the ledger as described above. For every subsequent revision, it is eligible only when it is the ledger's `eligible_revision` and the immediately prior revision's `terminal_revision` and `terminal_outcome` are recorded. Completion of an older revision and promotion of the next revision to `eligible_revision` MUST be one atomic transaction that increments `ordering_version`, records the terminal outcome, and appends the transition audit with one `transition_seq`. A stale coordinator that loses this CAS MUST abandon submission and emit an ordering conflict. The ledger fence is separate from the row fence, and both must be current for a submission.

The record MUST retain every operation outcome and its evidence. A newer revision MUST wait behind an older `in_flight` or uncertain operation. Once the older revision reaches a confirmed terminal outcome, the ordering lease/CAS rules below determine whether the newer revision advances; local promotion still requires the current row state/version and fence. A missing or unresolved outcome does not satisfy the ordinary prior-terminal requirement.

The post-promotion path is explicit: when an older revision reaches `promoted`, the ordering coordinator atomically records the older revision as terminal, advances the per-document ordering version under CAS, and moves the next queued newer revision from `newer_revision_waiting` to `newer_revision_eligible` (or leaves it eligible if already pending). A worker may submit that newer revision only after observing that committed ordering advance and acquiring its own lease/fence. If the older revision terminates in failure, the same ordering advance occurs according to the explicit policy below; a newer revision MUST NOT be blocked forever merely because the older row is quarantined. A stale coordinator/worker that loses this CAS must not submit the newer revision.

#### Ordering policy by terminal outcome

The ledger distinguishes the initial revision from all newer revisions and distinguishes three failure/quarantine cases. These rules preserve no-silent-drop and health/alert semantics:

| Outcome | Ordering advancement and blocking | Replay/resubmission |
|---|---|---|
| First revision bootstrap | Insert the ledger with `eligible_revision=<first revision>`, `terminal_revision=NULL`, and `terminal_outcome=NULL`; the first revision may submit after its normal row/lease/fence CAS. No prior-terminal check applies. | Normal worker submission allocates the first operation ID. No replay is implied by bootstrap. |
| Confirmed permanent failure | Record ordering summary `document_ordering.terminal_outcome=failed` in the same ordering CAS as the terminal row transition; the outbox row is classified `terminal_outcome=confirmed_permanent`. The failed attempt is not automatically resubmitted, but the ledger advances and a newer revision may become eligible; the quarantined row remains visible and keeps health degraded. | Only an explicitly authorized replay may create a new attempt. If a newer revision has already advanced or promoted, replay of the older revision is blocked as superseded unless a fresh ordering CAS proves it cannot overwrite newer state. |
| Ordinary automatic-retry quarantine | Record ordering summary `document_ordering.terminal_outcome=quarantined` and classify the outbox row as `terminal_outcome=automatic_retry_quarantine`; advance the ledger so a newer revision is not blocked indefinitely. The row remains excluded from ordinary draining, visible in counts/alerts, and operational health remains false. | The 30-minute `automatic_reconciliation_retry` starts a fresh cycle with a new operation ID and preserved evidence. It is not operator replay and cannot bypass ordering/fence CAS; an older row that is superseded by a newer committed revision must be skipped rather than overwrite it. Authorized operator replay is separate and subject to the same supersession check. |
| Unresolved-outcome quarantine | `uncertain` remains blocking until the explicit `authorized_unresolved_quarantine` CAS commits. That transaction records ordering summary `document_ordering.terminal_outcome=quarantined`, classifies the outbox row as `terminal_outcome=unresolved_outcome_quarantine`, and advances the ledger, so a newer revision may become eligible, without claiming that the remote operation failed or was cancelled. Before that CAS, no newer submission is allowed. | No ordinary or automatic resubmission is allowed from `uncertain`. After quarantine, automatic retry or authorized replay must preserve the unresolved evidence, status-look up the old operation when possible, use a new operation ID only under fresh CAS/authorization, and skip if the newer revision has superseded the row. |

In every case, advancement means only that the next revision may become eligible; it does not clear the failed/quarantined row, close its alert, or make the system healthy. The ordering transaction and the terminal transition are atomic, and a failed/stale CAS leaves both the row and ledger unchanged.

If a future implementation elects synchronous retain, it must instead add a durable deduplication ledger keyed by logical operation, persist request/response/uncertain status, perform mandatory post-timeout reconciliation/status lookup, and prove duplicate suppression under crash injection. That is an alternative **implementation gate**, not an optional fallback and not part of the v1 decision.

A fence prevents a stale worker from committing a stale **local** state transition after reclamation. It cannot cancel or roll back a remote request already accepted by Hindsight. Remote ordering/status lookup and per-document coordination above are therefore mandatory; claims that fencing alone prevents duplicate remote side effects are out of contract.

Remote-send authorization is durable, not an in-memory boolean: immediately before send, the same transaction (or an immediately preceding committed transaction) MUST persist the operation attempt, `operation_id`, `snapshot_id`/`run_id`, row/ordering/snapshot fences, owner/lease identity, and the authorization predicate that was evaluated. The send boundary MUST revalidate `status=active`, `lease_expires_at > now`, matching `state_version`, row fence, ordering version/fence, and—when snapshot-owned—snapshot fence and active lease. This is a TOCTOU boundary: a database CAS cannot atomically include an external Hindsight request. If the lease expires after the final local validation but before or during the network send, the request may still be accepted remotely; the worker MUST record the attempt as uncertain on recovery, MUST NOT claim cancellation or duplicate suppression from fencing alone, and MUST status-reconcile the persisted operation ID before any resubmission.

### 1.3 Automatic reconciliation retry versus operator replay

The two quarantine retry paths are defined separately:

| Path | Transition | Authorization/actor | Budget and cadence | Alert/audit semantics |
|---|---|---|---|---|
| Automatic reconciliation | `automatic_reconciliation_retry` | No human approval; `reconciliation_service`; outbox rows with `state=quarantined` and `terminal_outcome=automatic_retry_quarantine` only | Every 30 minutes; starts a fresh worker retry cycle with the Issue #4 five-attempt schedule; one row per sweep at most | Durable system audit event with reconciliation run ID and reason; no per-sweep warning for an unchanged condition; persistent-failure alert after 10 failed sweep cycles (~5 hours) |
| Operator replay | `authorized_operator_replay` | Authenticated principal with replay role and profile/bank authorization; explicit mutation confirmation; outbox rows with `state=quarantined` and `terminal_outcome IN (confirmed_permanent, automatic_retry_quarantine, unresolved_outcome_quarantine)` only | Bounded target snapshot, max 100 rows/invocation, 10 remote operations/minute, 30 seconds/operation, concurrency 1; all are implementation gates | Action/audit event containing principal, authorization decision, reason, policy version, target hash, dry-run/mutation result; existing warning/page remains until clear criteria are met |

Automatic reconciliation MUST NOT be described as operator replay, and operator replay MUST NOT silently bypass the automatic retry budget, remote ordering, or evidence rules. Automatic retry failure returns the row to `quarantined` after the worker cycle; it does not make the row healthy. A successful retry records the prior quarantine and the new promotion separately.

### 1.4 Complete reconciliation and drain semantics

A reconciliation run MUST derive `cadence_slot` deterministically in UTC: `floor(unix_seconds(now_utc) / 1800)` for the 30-minute policy, represented as the integer slot start (or an equivalent canonical UTC timestamp). The same policy version and slot derivation MUST be used by every trigger; local timezone and wall-clock formatting MUST NOT affect it. Missed slots are recorded as `missed`/`not_run` and are not silently backfilled as if they had run. If catch-up is enabled, each missed slot is claimed and processed separately with its own `run_id`, bounded by an explicit catch-up limit. The run then claims one durable `sweep_run` for `(run_kind, cadence_slot)`. Claim uses owner/lease/fence CAS; a live unexpired `running` run is not overlapped, and a second trigger records `sweep_overlap_skipped` bound to the existing `run_id`. An expired `running` run is fenced and marked `aborted` before a replacement claims the same slot; the partial-live uniqueness constraint permits that replacement while preserving the old record. Every snapshot, quota decision, retry transition, audit event, alert, health record, and completion report produced by the sweep MUST carry and validate that `run_id`; no cadence/quota/audit/alert record may be attributed only to a worker or wall-clock timestamp. The run lease is renewed before each batch and before expiry. Completion is one owner/fence CAS to `succeeded`, `failed`, `aborted`, or `unknown`, with the transition watermark and report persisted first.

A reconciliation run MUST inspect every non-archived outbox row and verify state, lease expiry, retry eligibility, transition integrity, and remote-operation status for unresolved async operations. It MUST NOT silently delete or rewrite a row. State transitions, claims, lease expiry/reclamation, CAS conflicts, remote operation IDs (never payloads), automatic retry outcomes, and operator actions are audit events.

The health surface MUST expose at least:

- counts by `pending`, `in_flight`, `uncertain`, `promoted`, and `quarantined`;
- oldest pending age and oldest quarantine age;
- expired-lease and CAS-conflict counts;
- unresolved remote-operation count and status-lookup failures;
- last successful drain time and last reconciliation completion time;
- reconciliation run ID, start/end time, and status (`running`, `succeeded`, `failed`, `unknown`);
- worker liveness: heartbeat time, worker count, and stale-worker count;
- freshness age of the health snapshot;
- `queue_drained = (pending == 0 and in_flight == 0 and uncertain == 0)`;
- `operationally_healthy = queue_drained and quarantined == 0 and uncertain == 0 and unresolved_remote_operations == 0 and reconciliation_status == succeeded and worker_liveness == healthy`.

`queue_drained` deliberately does not hide quarantine or uncertainty. A quarantined row makes `operationally_healthy` false and produces a visible alert even when `queue_drained` is true. An uncertain row also prevents `queue_drained`, remains unresolved until status/provider resolution or safe quarantine, and produces degraded/alert state. Missing or stale inputs produce `unknown`/`degraded`, never a clean result.

### 1.5 Compaction, reconciliation snapshots, and watermarks

Compaction and reconciliation MUST use a common monotonic transition watermark defined as the committed high-water mark: the greatest `transition_seq` whose transition and audit transaction committed before the capture transaction's serialization point. Capture locks the sequence row and all transition writers serialize on that same row; it reads the current committed `next_seq - 1`, creates the run/snapshot registry record, records `(snapshot_id, run_id, transition_watermark, captured_at)`, and registers selected members in one transaction before publishing the snapshot as active. A rolled-back transition never advances the committed high-water mark, and an uncommitted/phantom sequence is never observable or included. No transition committed after that serialization point is a member of the snapshot, and no member may be processed before the active record and watermark commit are visible. Snapshot/watermark membership MUST be durable and visible to compaction: an atomic registry transaction (or equivalent lease/CAS protocol) MUST create the snapshot record, capture the committed watermark, register each selected row identity and expected `state_version`, and mark the run active before compaction can select candidates. The snapshot owner is the reconciliation/replay run identified by `owner_id`; it MUST heartbeat/renew `lease_expires_at` before each bounded processing batch and before expiry, using CAS on `(snapshot_id, owner_id, fence, status=active)`. Renewal does not change the fence; takeover after expiry increments the snapshot fence and invalidates the old owner. The registry MUST expose active snapshot IDs, owners, fences, watermarks, row membership, lease expiry, and completion/abort state. A run MUST durably CAS `active -> completed` (with owner and fence) only after all members are processed and its report is persisted, or CAS `active -> aborted` with a reason on cancellation/failure. Lease expiry alone never authorizes archive or compaction; an expired snapshot must first be safely fenced and marked aborted (or otherwise removed by the registry CAS protocol).

Every snapshot batch start, snapshot-member mutation, outbox mutation, remote send, archive write, and archive/delete CAS MUST validate `(snapshot_id, owner_id, fence, status=active, lease_expires_at > now)` in the same transaction or immediately before the side effect. A failed validation is a fenced abort: the actor MUST stop the batch, perform no remote send/archive/delete, and emit a run-bound fence-loss event. Lease renewal is not authorization after expiry. Before compaction selects or deletes anything, it MUST detect expired active snapshots, atomically increment/fence them and mark them `aborted` (or complete the equivalent registry takeover CAS); only then may it proceed. An expired-but-active snapshot is never treated as safe by timeout alone.

Compaction MUST atomically verify that registry membership is absent, or that the snapshot is durably `aborted`/`completed` under the current fence; an expired-but-active snapshot is a block, not permission. At the start of a reconciliation run, materialize the eligible row identities, current `state_version`, `mnemosyne_rev`, and category. The snapshot is immutable for that run. Rows or transitions created after the watermark are excluded from that run and MUST be eligible for the next run; the report records the watermark and exclusion count.

Compaction MUST acquire the per-row archival lease (or an equivalent CAS) and archive only rows whose last transition is at or below the compaction watermark, whose state is terminal for compaction, and which are not currently selected by a reconciliation/replay snapshot. The archive marker/deletion CAS MUST include the expected `state_version`; any concurrent transition causes compaction to skip the row and emit `compaction_skip_concurrent_update`. Reconciliation MUST treat an archived row as valid only when the archive manifest contains the same row ID, final state, state version, and digest. Compaction MUST never delete a row that is newer than its snapshot watermark or one that lacks a verified archive record.

Before compaction, write an immutable archive record containing the row identity, final state, final `state_version`, all transition IDs, attempt/operation IDs, timestamps, error taxonomy, and a canonical keyed digest of the redacted audit record. The archive record MUST also persist `archive_created_at`, `retention_policy_version`, `approved_retention_policy_version`, and `retention_days`. Issue #7's normative retention policy is **365 days** and MUST be recorded as the approved versioned policy before deletion is eligible. An archive write succeeding is not sufficient for immediate deletion: the archive-before-delete CAS MUST require `archive_verified=true`, the expected row `state_version` and current snapshot/row fence, `retention_elapsed=true` (current time at least `archive_created_at + 365 days` under the approved policy), and `retention_policy_version = approved_retention_policy_version = issue7-365-day` (or the exact approved #7 policy identifier). If any predicate is absent, mismatched, or not yet elapsed, the row remains live and deletion is forbidden. Every deletion/expiry is itself an audit event containing archive ID, retention-policy version, actor, timestamp, watermark, and archive digest. Historical reconciliation verifies archive counts, transition continuity, digests, and retention predicates against the manifest. A row MUST NOT disappear from both live and archive sets.

Issue #7 may choose storage tiers and the exact version identifier, but it MUST retain the normative 365-day minimum and MUST not weaken this concurrency, retention, or evidence-preservation invariant. Deletion is an always-ask/destructive operation at rollout and requires explicit approval and a tested recovery path; archive success alone never authorizes immediate deletion.

## 2. Deterministic L1↔L2 drift checks

Full outbox reconciliation is mandatory. Direct L1↔L2 comparison is sampled in v1 to bound remote load, but the sample, denominator, and SLO calculation are reproducible.

At the start of each run, materialize the population snapshot described in §1.5. For each eligible identity, encode the tuple `(scope, identity, category, state_version, mnemosyne_rev)` with canonical length-prefixed unsigned-32-bit byte lengths followed by UTF-8 bytes, in that field order; the encoding version is `tuple-v1`. Sort by the canonical tuple bytes and record the snapshot ID, transition watermark, population count, category counts, and SHA-256 hash of the ordered tuple list. The sampling seed is the exact UTF-8 byte concatenation of length-prefixed fields `(run_id, policy_version, category, identity)` under `seed-v1`; select by the big-endian first 64 bits of `SHA-256(seed-v1-bytes)`, then canonical tuple bytes as the complete tie-break. No unordered database iteration, platform-dependent serialization, or unseeded random generator is allowed. The exact ordered selected list and selection hash MUST be retained.

Coverage is mechanically defined as `distinct sampled eligible tuples / distinct eligible tuples` for a run/category. The 24-hour rolling coverage denominator is the union of eligible identity tuples across all run snapshots in the window; the numerator is the union of those tuples sampled at least once. An identity that changes `state_version` or revision is a new tuple and does not inherit prior coverage. Empty categories are excluded from category ratios but recorded. Overall coverage is `sum(numerator across categories) / sum(denominator across categories)`, not an average of category percentages.

The drift result MUST contain:

- population, sample, numerator, and denominator counts by category and overall;
- per-run and 24-hour rolling coverage ratios;
- sample-policy version, snapshot ID/watermark, population hash, selected-list hash, and canonical encoding version;
- mismatch count and mismatch category;
- a keyed `metadata_digest` over canonical identity/revision/delete metadata from each side, never over memory content;
- run start/end, remote-call count, status-lookup count, and failure/unknown count.

The default policy is a 30-minute cadence and total target `T=min(100, population_total)`. For each non-empty category `c` with population `N_c`, its floor quota is `q_c=min(N_c, max(10, ceil(0.20*N_c) if N_c<500 else 0))`; categories with `N_c>=500` receive no percentage floor. If `sum(q_c)>T`, allocate `T` seats by descending `q_c` and then canonical category bytes, never exceeding `N_c`. Otherwise, allocate the remaining `R=T-sum(q_c)` seats one at a time by largest remainder of proportional entitlement `R*N_c/sum(N_c)`; ties are broken by canonical category bytes, and a category is capped at `N_c`. If caps leave seats undistributed, repeat the same largest-remainder procedure over uncapped categories; if all categories are capped, the total is the population. This precisely handles low populations, empty categories (quota zero and recorded), caps, remainder distribution, and ties. Select each category's quota from its canonical hash order above. These defaults are **implementation gates**: the disposable live spike may justify a revision, but selected values, remote-load budget, and rationale must be recorded before publish readiness.

The detection SLO is also a gate and MUST state maximum time-to-detect for a seeded mismatch, required 24-hour coverage, and pass/fail calculation. The proposed v1 target is detection of every seeded mismatch in a covered category within 2 hours at the default cadence, with at least 95% rolling coverage within 24 hours. For a seeded mismatch, pass means the first result whose canonical comparison reports that identity/category as mismatched has `detected_at - injected_at <= SLO`; missing, unknown, or late observations fail. A mismatch is an observable error and enters operator recovery; it never emits payload content.

## 3. Alerts, deduplication, and hysteresis

Alert state is durable across process restarts. Each alert has a dedupe key, first-seen time, last-seen time, severity, occurrence count, and clear time. Re-emitting the same condition updates one alert rather than creating an unbounded stream. Alerts have explicit `open`, `clearing`, and `closed` states; closure is an auditable state transition, not deletion. A closed condition may create a new alert only when its dedupe condition is observed again after closure, with a new first-seen time and occurrence count.

Default thresholds are exact and configurable only through a versioned policy:

- **Warning:** one warning for each `quarantined` transition, deduped by `(row_id, state_version)`; automatic reconciliation retries do not create a new warning unless they create a new quarantine state version.
- **Page:** when any row's durable `quarantine_origin_at` is at least 5 hours old, or that row has completed 10 failed automatic reconciliation cycles, or when the oldest pending age increases on 3 consecutive sweeps while non-zero, or when no successful drain has completed for 5 hours. The first two predicates are intentionally equivalent only when every 30-minute slot runs: 10 completed failed cycles × 30 minutes = 5 hours. A missed slot does not increment the cycle count, so the persisted wall-clock origin still pages at five hours; a catch-up run must not manufacture cycles for an unrun slot.
- **Warning clear/closure:** a quarantine warning remains open while its `(row_id, state_version)` condition exists. A transition from that quarantine version to `pending` for either retry path is not success and does not clear the incident: after a successful persisted observation of the CAS transition, the old warning enters `clearing` with `closure_reason=superseded_by_retry` and `successor_cycle_no`; the successor retry cycle is opened/linked under its new quarantine state-version key before the old warning may be closed. `quarantine_origin_at` is set on the first quarantine transition and is preserved through automatic/operator retries and later quarantine versions; it is cleared only when promotion is durably committed. If the retry succeeds, the successor warning enters `clearing` and closes on the next successful persisted reconciliation observation. If it fails and creates a new quarantine state version, the successor warning is opened and the prior warning closes as superseded in the same durable alert transaction. A failed, stale, or unknown observation leaves the old warning open. Thus a retry into a new version never creates an alert gap, and a later quarantine is a new dedupe key linked to the prior warning lineage.
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

This report records requirements, not completed evidence. Publish readiness requires two distinct evidence classes. In addition, Issue #4's publish gate is explicit: the disposable live integration must verify exact metadata wire fidelity for the outbox's metadata through retain/replace/recall before this contract may be declared ready; pinned source/docs support is not sufficient.

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
5. Replay is per-item CAS-eligible, bounded, rate-limited, timeout-bounded, snapshotted, audited, and uses the same durable operation/status-resolution contract; no native replace idempotence is assumed.
6. Deterministic drift checks report reproducible coverage, detection SLO status, and keyed metadata digests without content.
7. Telemetry is allowlisted, bounded, HMAC-keyed, access-controlled, scrubbed, and cannot become a memory-content or credential exfiltration path.
8. `queue_drained` never hides quarantine or uncertainty; stale or incomplete health is degraded/unknown, not clean.

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
