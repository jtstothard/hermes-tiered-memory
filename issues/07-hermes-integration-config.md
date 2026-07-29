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
- Keep one **protected endpoint document/secret-store object** authoritative for the complete Hindsight destination tuple (`https`, canonical IDNA hostname, `443`, concrete absolute `base_path`), TLS trust policy, and proxy-disabled policy. The source is administrator-controlled and authenticated/integrity-protected (documented secret-store guarantee or canonical document plus a detached MAC/signature and pinned verification key).
- The only endpoint precedence is: **authenticated protected source → no fallback**. Ordinary Hermes config, environment variables, provider arguments, memory metadata, bank/profile values, request parameters, redirects, and runtime overrides must not supply, merge, replace, or augment any protected tuple/TLS/proxy field. Such attempts, including partial overrides, are startup/reload errors before DNS or network activity. The Hindsight credential is a separate secret and never belongs in the endpoint URL, process arguments, logs, or proxy settings.
- The protected source must be a regular non-symlink file or approved secret-store object, owned by the profile/service identity (or explicitly documented administrator), with no group/world write and verified permissions. Reject absent, unreadable, wrong-owner, writable, symlinked, malformed, duplicate-keyed, unauthorized, or integrity-invalid sources. Reload is only an authenticated administrator restart/reload of that source; there is no request-scoped or ordinary-runtime reload path, and a failed reload leaves the previous accepted configuration unusable until revalidated.
- Route and authority policy may be enabled only with retained evidence for one immutable upstream OpenAPI artifact: full source commit (not a tag/short SHA), retrieval URL, media type, retrieval timestamp, SHA-256, and the exact generated/inspected operation entries. The evidence must state the concrete deployed `base_path` and the validated bank-ID encoding (one path segment, canonical percent encoding, and rejection cases). No route, method, path, authority, or bank encoding may be invented from prose or a floating/live API response; missing evidence is release-blocking and the live integration remains UNVERIFIED.
- Configure bank mapping, timeouts, batch size, TTL/size bounds, retry policy, and reconciliation interval through non-security plugin settings—not hardcoded estate values—and validate them before side effects.
- Keep Hindsight credentials in the profile secret mechanism; keep only non-security settings in ordinary Hermes config.
- Make install/update verification fail closed if the composite plugin is missing, rather than silently falling back to a degraded provider.
- Expose health/status tools or metrics for outbox depth, oldest pending age, quarantined items, and L1/L2 drift.

#### Release evidence required by this contract

The release bundle must retain the protected-source identity/schema, ownership and permission result, integrity/authentication result, reload decision, the pinned OpenAPI artifact record above, and the IPv4/IPv6 IANA registry records required by issue #9. Each record includes URL, media type, UTC retrieval timestamp, SHA-256, interpretation used by policy, and the update/re-review procedure. TLS trust evidence (chain, hostname/SNI, trust-store provenance, and custom-CA fingerprint where applicable) is **UNVERIFIED** until an actual retained test/deployment artifact records it; this issue contains no live or scanner evidence.

### Why this boundary

A plugin-only composite is the smallest portable option that satisfies Hermes' one-provider constraint and keeps tool ownership coherent. A Hermes core change is unnecessary and creates upstream coupling. A sidecar is a future hardening option if queue throughput, isolation, or independent restart requirements justify its operational cost; it is not required to prove the design.

**Limitation:** this contract still needs real Hermes/Mnemosyne/Hindsight adapter tests and install/update survivability tests before publication.
