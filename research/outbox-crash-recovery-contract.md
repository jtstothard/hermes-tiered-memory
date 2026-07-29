# Issue #4 research: durable outbox crash recovery and replay contract

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`

## Scope and evidence policy

This is a grilling-resolution design record produced through a HITL `wayfinder:grilling` session with Jay. Every decision below was explicitly confirmed by Jay. No code was written, no Hermes process was started, no estate data was touched. This defines the outbox contract; implementation is a later ticket.

This ticket depends on:
- Issue #2 (live adapter contract) — defines the Hindsight `document_id` replace/append primitive and failure semantics.
- Issue #3 (plugin lifecycle) — confirmed teardown is bounded at 5s (`_SYNC_DRAIN_TIMEOUT_S`) and durability must live in the outbox, not in `close()`.

## Decision (the full contract)

### 1. Commit boundary — atomic L1+outbox write

The outbox enqueue is tied to the L1 (Mnemosyne) write in a single SQLite transaction. A crash between commit and enqueue cannot drop a promotion. The coupling means a full disk or corrupt outbox table blocks L1 writes too — this is **accepted**: never losing a promotion is the higher guarantee.

### 2. Storage — separate WAL-mode SQLite file, attached for the atomic transaction

A dedicated outbox SQLite file lives in the same bank directory as the Mnemosyne L1 database. It is `ATTACH`-ed to the L1 connection so both writes commit in one transaction. We own the outbox schema (idempotency key, retry counters, status, error log); Mnemosyne owns its tables. WAL journal mode (`PRAGMA journal_mode=WAL`) for crash recovery and concurrent read by the drain worker. Same bank directory = same backup boundary as L1.

Crash recovery: WAL-mode SQLite recovers automatically on open; the drain-on-restart replay (§6) handles any rows left `pending` by a torn write.

### 3. Outbox table schema (design, not SQL)

```
outbox
  id              INTEGER PRIMARY KEY AUTOINCREMENT
  document_id     TEXT    NOT NULL          -- Hindsight document_id
  mnemosyne_id    TEXT    NOT NULL          -- L1 source id
  mnemosyne_rev   INTEGER NOT NULL          -- L1 revision (idempotency key)
  content         TEXT    NOT NULL          -- snapshot at enqueue time
  metadata        TEXT    NOT NULL          -- JSON: profile, bank, origin, provenance
  status          TEXT    NOT NULL DEFAULT 'pending'
                  -- pending | in_flight | promoted | quarantined
  attempt_no      INTEGER NOT NULL DEFAULT 0 -- 1..5 in the current worker cycle
  retry_cycle_no  INTEGER NOT NULL DEFAULT 0 -- increments for each quarantine retry path
  next_retry_at   INTEGER                   -- epoch seconds; NULL when promoted/quarantined
  state_version   INTEGER NOT NULL DEFAULT 0
  owner_id        TEXT
  lease_id        TEXT
  lease_expires_at INTEGER
  fence           INTEGER NOT NULL DEFAULT 0
  last_error      TEXT
  last_http_status INTEGER
  created_at      INTEGER NOT NULL
  updated_at      INTEGER NOT NULL

UNIQUE(document_id, mnemosyne_rev)          -- idempotency constraint
INDEX(status, next_retry_at)                -- drain worker query
```

The `UNIQUE(document_id, mnemosyne_rev)` constraint deduplicates only the logical enqueue; it does not represent an attempt and MUST NOT block multiple attempts. Issue #6's durable `operation_attempt` table is append-only and contains `attempt_id`, `outbox_id`, `(document_id, mnemosyne_rev, operation_kind)`, opaque provider `operation_id`, `attempt_no`, status, request/outcome timestamps, bounded error classification, and provider status reference. `transition_audit` records every CAS transition. The source L1 write, outbox row creation/upsert, and initial audit are one ATTACH transaction; lease/fence claim, operation-ID persistence, and `in_flight` transition are durable before remote send; outcome and local transition/audit commit atomically with expected `state_version` and fence. This is the complete durable contract; the mutable `attempt_no` is only a materialized current-cycle counter, not the attempt history.

### 4. Transient vs permanent failure classification

| HTTP / error | Class | Action |
|---|---|---|
| Network timeout, connection refused, DNS | transient | retry |
| 5xx (server error) | transient | retry |
| 429 (rate limit) | transient | retry (respect Retry-After if present) |
| 400, 401, 403, 404, 413, 422 | permanent | quarantine immediately |
| Malformed success payload | permanent | quarantine |
| Async-accepted-then-failed | transient | retry (re-enqueue) |

### 5. Retry budget — 5 attempts per worker cycle, exponential backoff

The budget is **five total remote attempts**, numbered `attempt_no=1..5`, not five retries after an initial attempt. `retry_count = max(attempt_no-1, 0)`; after attempt 1, 2, 3, or 4 fails transiently, the next attempt is scheduled after **10s → 30s → 90s → 5m**, respectively. A transient failure on attempt 5 quarantines the row and records the final outcome; no sixth attempt is implied. Permanent failure quarantines immediately. A 429 Retry-After may delay the next scheduled attempt but may not extend the five-attempt budget. The counter resets to zero only when a quarantine retry path explicitly starts a new worker cycle, and `retry_cycle_no` records that boundary. All audit events, metrics, backoff oracles, and Issue #6 reconciliation rules use these same names and values.

### 6. Drain trigger — background thread + drain-on-restart replay

- **`initialize()`** starts a dedicated background drain thread (daemon) and runs an immediate drain-on-restart replay. For `in_flight` rows, the Issue #6 reconciliation/lease contract supersedes the former blanket-reset wording: recover only an expired lease by CAS, incrementing the fencing token; do not reset an unexpired lease or resubmit an uncertain remote operation.
- **`close()`** stops the thread within the 5s teardown cap. The thread is daemon, so anything still running past the window dies with the interpreter.
- The drain thread loops: selects rows `WHERE status='pending' AND (next_retry_at IS NULL OR next_retry_at <= now) ORDER BY id LIMIT batch`, marks them `in_flight`, attempts promotion, and updates to `promoted` or back to `pending` (with incremented `attempt_no` and `next_retry_at`) or `quarantined` (attempts exhausted or permanent failure). Sleeps when no work. Lease expiry, fencing, and uncertain remote-operation recovery are governed by Issue #6; this section does not authorize a blind `in_flight` reset.

### 7. Idempotency key — `(document_id, mnemosyne_revision)`

- Same `(document_id, mnemosyne_rev)` already `promoted` → drain skips it (completed duplicate).
- `mnemosyne_rev` incremented → genuine update, re-promote via Hindsight `replace` (deletes prior document, processes new).
- Enforced by the `UNIQUE(document_id, mnemosyne_rev)` constraint: a duplicate enqueue within the same transaction is an upsert/no-op, not an error.

### 8. Permanent failure — quarantine, halt only that document, leave L1 intact

A `quarantined` row records `last_error`, `last_http_status`, and `attempt_no=5` (or the terminal-failure attempt number), plus its `retry_cycle_no`. The drain continues processing other documents — a permanent failure on one record never blocks the queue. The L1 (Mnemosyne) entry that produced the quarantined promotion **remains valid** — the memory is stored at L1; only L2 promotion is blocked pending reconciliation.

### 9. Reconciliation — periodic sweep (30 min) + CLI command

- **Periodic sweep:** the drain thread/reconciliation service re-attempts `quarantined` rows on a 30-minute cadence (longer than the retry backoff) so transient-then-fixed failures self-heal. It performs the Issue #6 `automatic_reconciliation_retry` CAS transition, increments `retry_cycle_no`, resets `attempt_no` to zero, and re-enters the five-total-attempt worker cycle; if it fails all five again, it returns to `quarantined` with a new state version and linked warning lineage.
- **CLI command** (`hermes <provider> reconcile` or equivalent): this is only the Issue #6 `authorized_operator_replay` interface, never a third retry path. It MUST use authenticated replay authorization, default dry-run, explicit mutation confirmation, immutable target snapshot/hash, per-item state/revision/fence CAS, async operation-ID/status lookup, the same five-total-attempt budget and ordering rules, bounded target/rate/time/concurrency limits, deterministic skip reasons, and durable audit/alert semantics defined by Issue #6 §1.3 and §4. It reports promoted/quarantined/still-failing/skipped/rate-limited counts. An unauthorized or unconfirmed CLI request changes no row state.

The periodic sweep means a genuinely-permanent-permanent row is retried every 30 min indefinitely — this is **accepted** as the cost of self-healing. The sweep is quiet (no alert) unless a row crosses a high **sweep-cycle** threshold (configurable; default 10 sweep cycles = ~5 hours), at which point it surfaces as a persistent-failure alert.

### 10. Checkpoint / queue-drained semantics

**Queue drained = no row is currently `pending` or `in_flight`**, i.e. `pending == 0 AND in_flight == 0`; this does not mean every row is healthy. `quarantined` is terminal only for the current delivery-attempt cycle and remains an active recovery condition under Issue #6. The health surface MUST therefore report queue-drained separately from operational health, and must not describe a quarantined queue as “all rows terminal” without that qualification.

The operator surface reports:
- `drained: true/false` (no pending/in-flight rows)
- `promoted: N`
- `quarantined: N` (visible separately, does not block `drained`)

## Safety invariants

1. **Never lose a promotion** — the atomic commit guarantees the enqueue and L1 write land together or not at all.
2. **Never block the queue** — quarantine halts only the failing document; the drain continues.
3. **Never silently drop** — every row reaches a terminal state (`promoted` or `quarantined`); there is no "lost" state.
4. **Never trust teardown for durability** — the outbox is on disk at write time; `close()` is best-effort within 5s.
5. **Never retry a permanent error indefinitely at short intervals** — permanent failures quarantine immediately; the periodic sweep is the only re-attempt path, on a 30-min cadence.

## Dependency and feeding downstream tickets

- **Issue #5 (Hindsight semantics):** this contract assumes `document_id` replace is idempotent and that metadata round-trips. #5 must verify that against the pinned API.
- **Issue #7 (TTL/eviction/performance):** the outbox table grows with quarantined rows; #7 should define retention/compaction for terminal rows.
- **Issue #6 (reconciliation/observability/operator recovery):** its lease/fence/CAS recovery contract explicitly supersedes §6's former blanket `in_flight` reset-to-`pending` rule. An unexpired lease remains owned; an expired lease is reclaimed only by fenced CAS, and an uncertain remote operation is status-reconciled before resubmission.
- **A future implementation ticket** will build this against a disposable environment per the issue #2 isolation rules.

## Newly visible successor questions

1. **Outbox compaction** — when/how to delete `promoted` rows (they're completed; keeping them is only for audit). Candidate for #7.
2. **Sweep alert threshold** — the default 10-cycle (~5h) persistent-failure alert needs tuning against real failure distributions.
3. **Batch promotion** — whether the drain should coalesce multiple rows for one Hindsight `retain_batch` call, or promote one-at-a-time. One-at-a-time is simpler and isolates partial failures; batch is faster but needs the partial-failure handling from #2's matrix.
4. **Cross-profile outbox isolation** — confirmed per-profile (separate bank dir per profile), but the drain thread per profile multiplies thread count. Worth noting for the performance ticket.

## Concise recommendation for issue #4

Atomic L1+outbox commit via ATTACH; separate WAL file per bank dir; five-total-attempt exponential backoff; append-only operation/transition history with lease/fence/CAS recovery; background drain thread + drain-on-restart; idempotency by `(document_id, mnemosyne_rev)` with multiple durable attempts; quarantine on permanent failure (halt only that document, L1 intact); reconciliation via the Issue #6 automatic 30-minute path; and CLI replay only through Issue #6's authorized operator path. Queue-drained is separate from operationally healthy. Every decision confirmed with Jay during the grilling session.
