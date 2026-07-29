Type: grilling
Status: resolved
Claimed by: default router session
Blocked by: 03

## Question

How does promotion behave under Hindsight outage, timeout, rate limit, malformed payload, partial batch failure, local queue restart, and permanent rejection — including retry, rollback/compensation, alerting, and drift detection?

**Completion test:** a failure-state model defines durable promotion checkpoints and bounded retries, and a prototype/test demonstrates restart-safe replay plus actionable health metrics/logs without losing L1 data.

## Answer

**Decision:** use a durable local outbox with bounded retry, quarantine, and silent-by-default observability.

- Persist pending L2 work in a durable local outbox before/alongside asynchronous dispatch.
- Retry transient outage, timeout, and rate-limit failures with exponential backoff and bounded attempts.
- Reconciliation sweeps recover missed events and retry eligible pending work after worker or host restart.
- L1 foreground operations never fail solely because L2 is unavailable.
- After bounded retries, quarantine the item with its payload and failure reason; alert and permit manual or reconciliation replay.
- Keep normal retries silent. Alert on any quarantined item, sustained outbox age/lag, rising retry rate, and detected L1/L2 drift.
- Partial batches checkpoint per item; successful items remain complete and failed items remain retryable/quarantinable.

**Acceptance evidence required in the successor implementation:** restart-safe outbox replay, L2 timeout/rate-limit/malformed-payload cases, partial-batch recovery, quarantine/replay, and synthetic drift detection.

**Limitation:** this defines behavior; exact storage schema, metrics backend, thresholds, and Hindsight delete/update mechanics remain implementation details.
