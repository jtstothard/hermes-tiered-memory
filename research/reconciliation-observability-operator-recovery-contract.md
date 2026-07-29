# Issue #6 research: reconciliation, observability, and operator recovery contract

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`

## Scope and evidence policy

This is a grilling-resolution design record produced through a HITL `wayfinder:grilling` session with Jay. The decisions below were explicitly selected by Jay. No production service, estate data, or credentials were used. This defines the operator contract; implementation and live measurements remain later work.

This ticket depends on:
- Issue #4 — durable outbox crash recovery and replay contract.
- Issue #5 — Hindsight revision, deletion, metadata, and batch semantics.
- Issue #7 — outbox compaction and performance policy.

## Decision

The v1 operator contract uses structured, content-free telemetry; complete outbox reconciliation; sampled cross-tier drift checks; bounded, dry-run-first replay; and two-level alert escalation.

## 1. Reconciliation rules

### Outbox state reconciliation — complete

The provider must reconcile all outbox rows by state:

- `pending`: eligible or waiting for its retry time.
- `in_flight`: reset to `pending` after an unclean process exit before replay.
- `promoted`: terminal success.
- `quarantined`: terminal failure requiring visibility and possible operator replay.

The health summary must expose at least:

- pending count;
- in-flight count;
- quarantined count;
- oldest pending age;
- oldest quarantine age;
- last successful drain time;
- last reconciliation time;
- `drained = (pending == 0 and in_flight == 0)`.

A reconciliation run must not silently delete or rewrite a row. State transitions and operator actions are recorded in structured logs.

### L1↔L2 drift checks — sampled

Full outbox-state reconciliation is mandatory. Direct L1↔L2 comparison is sampled in v1 rather than performed across the entire memory corpus on every sweep, avoiding unbounded latency and remote load.

A sample should cover recent writes, revisions, deletes, and previously quarantined/replayed documents. The sample must verify identity metadata and revision/document identity, but must not emit memory content. A detected mismatch is an observable error and an input to the operator recovery path.

The sampling rate and population size are implementation parameters to be measured in the disposable live spike; changing them is not a silent behavior change and must be reflected in the operational evidence.

## 2. Alert and lag policy

- **Immediate warning:** every newly quarantined row generates a warning-level signal.
- **Page-level escalation:** page when a quarantine persists across the configured threshold of approximately 5 hours (the existing 10-sweep threshold at 30-minute cadence), or when pending/quarantined counts or oldest-row age are worsening.
- **No quiet failure:** a provider with persistent quarantine, stale drain activity, or unbounded pending age cannot report a clean operational baseline.

The exact threshold remains configurable, but the default and the reason for any override must be visible in the evidence artifact.

## 3. Observability surface

The minimum publish-readiness surface is:

1. **Structured logs** for state transitions, retry classification, quarantine, reconciliation, replay, drift mismatch, startup recovery, and shutdown.
2. **Health/CLI summary** exposing the counts, ages, timestamps, drained state, and last error class/status needed for an operator to decide what to do.
3. **Metrics backend integration** such as Prometheus is deferred until after v1. The v1 contract must keep event names and fields stable enough that a later metrics exporter can derive them without changing recovery semantics.

Logs and summaries must be profile/bank scoped where needed for isolation, while avoiding raw payloads.

## 4. Operator replay contract

Manual replay is supported, but constrained:

- target a specific document or a bounded batch;
- provide a dry-run mode first;
- report eligible, skipped, promoted, quarantined, and still-failing counts;
- preserve idempotency through `(document_id, mnemosyne_rev)`;
- never clear quarantine or delete evidence merely because replay was requested;
- record the operator action, target scope, result, and failure class without logging content or secrets.

Unrestricted full-queue replay is not the v1 default. If a future implementation adds it, it requires an explicit operator control and separate evidence for load and rate-limit behavior.

## 5. Telemetry safety

Observability may contain identifiers, status, timing, counts, HTTP status/error class, retry number, and bounded diagnostic fields needed for recovery. It must never contain:

- memory content or prompt text;
- API keys, bearer tokens, credentials, or connection strings;
- full metadata payloads if they can contain sensitive values.

Document and bank identifiers should be treated as sensitive operational identifiers and protected according to the deployment environment. Content-free telemetry is the default even in isolated debug runs.

## 6. Required evidence before publish readiness

The implementation evidence must include local tests and CI evidence for:

- crash recovery resetting `in_flight` rows;
- complete state reconciliation and correct `drained` calculation;
- warning generation for a new quarantine;
- page escalation at the persistence/growth threshold;
- bounded replay and dry-run behavior;
- idempotent replay of the same `(document_id, mnemosyne_rev)`;
- sampled drift detection across revision, delete, and quarantine/replay fixtures;
- assertions that content and secrets do not appear in logs or CLI output;
- deterministic health/CLI output suitable for later metrics export.

Live disposable-environment evidence must measure drain latency, remote-call load from drift sampling, and the practical threshold configuration. No live production data is required or permitted.

## Safety invariants

1. Reconciliation never silently loses or deletes evidence.
2. Quarantine is visible immediately and escalates when persistent or worsening.
3. Replay is bounded, idempotent, and dry-run-first.
4. Telemetry cannot become a memory-content or credential exfiltration path.
5. A clean `drained` signal does not hide quarantined rows; quarantine remains separately visible.

## Newly visible successor questions

1. **Telemetry schema implementation** — exact event names, field schema, and redaction tests.
2. **Drift sampling policy** — sample size, cadence, and load budget from the disposable live spike.
3. **CLI authorization boundary** — how the eventual deployment authenticates replay operators; deferred because this map does not deploy to the estate.
4. **Metrics exporter** — Prometheus/OpenTelemetry integration after the v1 structured event contract is stable.

## Concise recommendation for issue #6

Use full outbox reconciliation and sampled L1↔L2 drift checks. Emit immediate quarantine warnings and page after approximately five hours or worsening failure/lag. Expose structured logs plus a health/CLI summary in v1. Constrain manual replay to a document or bounded batch with dry-run first. Record IDs, status, and timing only; never log memory content or secrets.
