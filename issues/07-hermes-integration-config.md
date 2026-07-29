Type: task
Status: resolved
Claimed by: default router session
Blocked by: 01

## Question

What is the smallest portable integration boundary for Hermes: standalone composite plugin, sidecar promotion worker, plugin hooks, or a limited Hermes core change — and what configuration/install contract does it require?

**Completion test:** a concrete component diagram and install/config contract identifies process boundaries, credentials/config locations, tool ownership, lifecycle hooks, and why the chosen boundary is safer than the alternatives.

## Answer

**Decision:** implement the first real version as a single composite Hermes memory plugin with an embedded durable outbox/worker. Do not modify Hermes core and do not require a separate sidecar for the initial handoff.

### Boundary

```text
Hermes
  └── one configured MemoryProvider: CompositeProvider
        ├── Mnemosyne adapter (L1, synchronous reads/writes)
        ├── Hindsight adapter (L2 reads and retain/batch-retain)
        └── local durable outbox + promotion worker
```

The composite owns the sole Hermes-facing memory tool namespace. Mnemosyne and Hindsight adapters expose no duplicate Hermes tools. The plugin hooks consolidation events, performs L1 work in the request path, and queues L2 work through the outbox. A periodic worker/reconciliation hook drains the outbox.

### Configuration/install contract

- Configure Hermes with exactly one provider: the composite plugin.
- Use profile-safe paths and runtime home resolution; each profile gets its own L1 database and outbox.
- Configure L2 endpoint, bank mapping, timeouts, batch size, TTL/size bounds, retry policy, and reconciliation interval through plugin config/environment—not hardcoded estate values.
- Keep Hindsight credentials in the profile `.env`/secret mechanism; keep non-secret settings in Hermes config.
- Make install/update verification fail closed if the composite plugin is missing, rather than silently falling back to a degraded provider.
- Expose health/status tools or metrics for outbox depth, oldest pending age, quarantined items, and L1/L2 drift.

### Why this boundary

A plugin-only composite is the smallest portable option that satisfies Hermes' one-provider constraint and keeps tool ownership coherent. A Hermes core change is unnecessary and creates upstream coupling. A sidecar is a future hardening option if queue throughput, isolation, or independent restart requirements justify its operational cost; it is not required to prove the design.

**Limitation:** this contract still needs real Hermes/Mnemosyne/Hindsight adapter tests and install/update survivability tests before publication.
