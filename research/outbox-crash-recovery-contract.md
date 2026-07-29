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
  attempts        INTEGER NOT NULL DEFAULT 0
  next_retry_at   INTEGER                   -- epoch seconds; NULL when promoted/quarantined
  last_error      TEXT
  last_http_status INTEGER
  created_at      INTEGER NOT NULL
  updated_at      INTEGER NOT NULL

UNIQUE(document_id, mnemosyne_rev)          -- idempotency constraint
INDEX(status, next_retry_at)                -- drain worker query
```

### 4. Transient vs permanent failure classification

| HTTP / error | Class | Action |
|---|---|---|
| Network timeout, connection refused, DNS | transient | retry |
| 5xx (server error) | transient | retry |
| 429 (rate limit) | transient | retry (respect Retry-After if present) |
| 400, 401, 403, 404, 413, 422 | permanent | quarantine immediately |
| Malformed success payload | permanent | quarantine |
| Async-accepted-then-failed | transient | retry (re-enqueue) |

### 5. Retry budget — 5 retries, exponential backoff (~20 min total)

Backoff schedule: **10s → 30s → 90s → 5m → 15m**. After 5 transient failures, the row is reclassified `quarantined` with the final error recorded. Enough to ride out a Hindsight restart or transient network blip; bounded enough that a genuinely permanent failure is quarantined within ~20 minutes.

### 6. Drain trigger — background thread + drain-on-restart replay

- **`initialize()`** starts a dedicated background drain thread (daemon) and runs an immediate drain-on-restart replay. For `in_flight` rows, the Issue #6 reconciliation/lease contract supersedes the former blanket-reset wording: recover only an expired lease by CAS, incrementing the fencing token; do not reset an unexpired lease or resubmit an uncertain remote operation.
- **`close()`** stops the thread within the 5s teardown cap. The thread is daemon, so anything still running past the window dies with the interpreter.
- The drain thread loops: selects rows `WHERE status='pending' AND (next_retry_at IS NULL OR next_retry_at <= now) ORDER BY id LIMIT batch`, marks them `in_flight`, attempts promotion, updates to `promoted` or back to `pending` (with incremented `attempts` and `next_retry_at`) or `quarantined` (attempts exhausted or permanent failure). Sleeps when no work. Lease expiry, fencing, and uncertain remote-operation recovery are governed by Issue #6; this section does not authorize a blind `in_flight` reset.

### 7. Idempotency key — `(document_id, mnemosyne_revision)`

- Same `(document_id, mnemosyne_rev)` already `promoted` → drain skips it (completed duplicate).
- `mnemosyne_rev` incremented → genuine update, re-promote via Hindsight `replace` (deletes prior document, processes new).
- Enforced by the `UNIQUE(document_id, mnemosyne_rev)` constraint: a duplicate enqueue within the same transaction is an upsert/no-op, not an error.

### 8. Permanent failure — quarantine, halt only that document, leave L1 intact

A `quarantined` row records `last_error`, `last_http_status`, and `attempts`. The drain continues processing other documents — a permanent failure on one record never blocks the queue. The L1 (Mnemosyne) entry that produced the quarantined promotion **remains valid** — the memory is stored at L1; only L2 promotion is blocked pending reconciliation.

### 9. Reconciliation — periodic sweep (30 min) + CLI command

- **Periodic sweep:** the drain thread re-attempts `quarantined` rows on a 30-minute cadence (longer than the retry backoff) so transient-then-fixed failures self-heal. A row that fails the sweep resets to `pending` with a fresh attempt counter and re-enters the normal retry cycle; if it fails all 5 again, it returns to `quarantined`.
- **CLI command** (`hermes <provider> reconcile` or equivalent): manual, logged retry with visible output, for cases where an operator fixed something specific and wants immediate confirmation. Reports promoted/quarantined/still-failing counts.

The periodic sweep means a genuinely-permanent-permanent row is retried every 30 min indefinitely — this is **accepted** as the cost of self-healing. The sweep is quiet (no alert) unless a row crosses a high attempt-count threshold (configurable; default 10 sweep cycles = ~5 hours), at which point it surfaces as a persistent-failure alert.

### 10. Checkpoint / drained semantics — all rows terminal

**Drained = every row is terminal (`promoted` OR `quarantined`).** A `quarantined` row has reached a final state and is not pending/retrying. The health-check signal is: `pending` count == 0 AND `in_flight` count == 0.

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

Atomic L1+outbox commit via ATTACH; separate WAL file per bank dir; 5-retry exponential backoff over ~20 min; background drain thread + drain-on-restart; idempotency by `(document_id, mnemosyne_rev)`; quarantine on permanent failure (halt only that document, L1 intact); reconciliation via 30-min periodic sweep + manual CLI; drained = all rows terminal. Every decision confirmed with Jay during the grilling session.
