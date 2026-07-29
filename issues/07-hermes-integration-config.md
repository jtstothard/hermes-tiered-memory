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
- **Bind the checked-in contract immutably at packaging/review time.** The release bundle must record the full commit SHA of the exact checked-in revision reviewed and the SHA-256 digest of this file's exact bytes (and, where the bundle relies on them, the digests of the other contract files). Compute the digest after checkout from the reviewed commit and before packaging; retain the command/tool version and manifest beside the bundle. A report edit, working-tree state, short SHA, branch name, or mutable URL is not a substitute. If the file or commit changes, recompute the manifest and rerun the review; never copy a prior digest or claim that a later report revision was present at the earlier commit.
- The only endpoint precedence is: **authenticated protected source → no fallback**. Ordinary Hermes config, environment variables, provider arguments, memory metadata, bank/profile values, request parameters, redirects, and runtime overrides must not supply, merge, replace, or augment any protected tuple/TLS/proxy field. Such attempts, including partial overrides, are startup/reload errors before DNS or network activity. The Hindsight credential is a separate secret and never belongs in the endpoint URL, process arguments, logs, or proxy settings.
- The protected source must be a regular non-symlink file or approved secret-store object, owned by the profile/service identity (or explicitly documented administrator), with no group/world write and verified permissions. Reject absent, unreadable, wrong-owner, writable, symlinked, malformed, duplicate-keyed, unauthorized, or integrity-invalid sources. Version 1 uses UTF-8 JSON serialized with RFC 8785 JCS (duplicate names, insignificant whitespace, and alternate encodings rejected). The authenticated payload contains `schema_version: 1`, `key_id`, `config_id`, integer `sequence`, RFC 3339 UTC `issued_at`/`not_before`/`expires_at`, and the complete endpoint/TLS/proxy policy. Authenticate the exact canonical payload bytes with domain separator `hermes-tiered-memory/config/v1\0`, using only `HMAC-SHA-256` or `Ed25519`; `key_id` selects a pinned authorized key. Unknown/revoked keys and other algorithms fail closed. Rotation pre-publishes a new key, permits old/new only during a documented overlap, then revokes the old key. Require a bounded clock-skew allowance of at most five minutes, `not_before <= now <= expires_at`, and strictly increasing `sequence` per `config_id`; equal sequence is accepted only for the identical digest, while lower sequence, same-sequence changed payload, expiry, or revoked-key reuse is rejected. Persist the accepted sequence and digest before activation to prevent rollback after restart.
- Reload is an atomic state machine: `UNINITIALIZED -> VALIDATING -> ACTIVE` for a valid document; while `ACTIVE`, validation occurs against the new bytes with the last-known-good immutable snapshot still serving, and only after validation plus durable freshness recording succeeds does one atomic generation/pointer swap activate it. Invalid, stale, replayed, unauthorized, or unverifiable reloads are discarded and leave the old generation `ACTIVE`; if no snapshot has ever succeeded, enter `QUIESCED/FAIL_CLOSED`. Validation/reload failure performs no DNS, socket, proxy, TLS, or HTTP activity, and in-flight operations retain their captured generation. Recovery is an explicit authenticated administrator reload/restart with a newer valid sequence; automatic retries may re-read local bytes but may not use the network or bypass freshness. There is no request-scoped or ordinary-runtime override path, and a failed reload must not make a previously active configuration unusable.
- Route and authority policy may be enabled only with retained evidence for one immutable upstream OpenAPI artifact: full source commit (not a tag/short SHA), retrieval URL, media type, retrieval timestamp, SHA-256, and the exact generated/inspected operation entries. The evidence must state the concrete deployed `base_path` and the validated bank-ID encoding (one path segment, canonical percent encoding, and rejection cases). No route, method, path, authority, or bank encoding may be invented from prose or a floating/live API response; missing evidence is release-blocking and the live integration remains UNVERIFIED.
- Outbox compaction and deletion are governed by #6's archive-before-delete contract, not by ad-hoc SQL or age alone: use the committed transition watermark, immutable deterministic candidate ordering, active snapshot exclusion, archival lease/CAS, expected `state_version`/revision fence, verified immutable archive, deletion audit, retention, and authorized manual-clear semantics. The default archive/audit retention period is 365 days, justified by a full annual operational/audit lookback; deletion is blocked until the retention policy is approved, and any override requires documented operator approval, rationale, scope, and policy version. A fence conflict skips the row; lease expiry never authorizes deletion; a row must not disappear from both live and archive sets. Destructive deletion is always-ask at rollout and requires explicit approval and a tested recovery path. #7 is the policy owner for the growth trigger and retention default; #6 remains authoritative for the deletion mechanism and evidence-preserving archive-before-delete transition.
- Configure bank mapping, timeouts, batch size, TTL/size bounds, retry policy, and reconciliation interval through non-security plugin settings—not hardcoded estate values—and validate them before side effects.
- Keep Hindsight credentials in the profile secret mechanism; keep only non-security settings in ordinary Hermes config.
- Make install/update verification fail closed if the composite plugin is missing, rather than silently falling back to a degraded provider.
- Expose health/status tools or metrics for outbox depth, oldest pending age, quarantined items, and L1/L2 drift.

#### Release evidence required by this contract

The release bundle must retain the protected-source identity/schema, ownership and permission result, integrity/authentication result, key-rotation/freshness decision, the pinned OpenAPI artifact record above, the IPv4/IPv6 IANA registry records required by issue #9, and the exact checked-in contract manifest (full commit SHA plus SHA-256 for this file and every other contract file used). The IANA sources are the authoritative XML documents `https://www.iana.org/assignments/iana-ipv4-special-registry/iana-ipv4-special-registry.xml` and `https://www.iana.org/assignments/iana-ipv6-special-registry/iana-ipv6-special-registry.xml`; fetch as `application/xml`, parse with a namespace-aware XML parser, and retain the exact bytes, UTC retrieval timestamp, and SHA-256. Each record includes URL, media type, parser/interpretation used by policy, and the update/re-review procedure; missing retained bytes, timestamp, or SHA-256 for either registry is release-blocking. TLS trust evidence (chain, hostname/SNI, trust-store provenance, and custom-CA fingerprint where applicable) is **UNVERIFIED** until an actual retained test/deployment artifact records it; this issue contains no live or scanner evidence.

### Why this boundary

A plugin-only composite is the smallest portable option that satisfies Hermes' one-provider constraint and keeps tool ownership coherent. A Hermes core change is unnecessary and creates upstream coupling. A sidecar is a future hardening option if queue throughput, isolation, or independent restart requirements justify its operational cost; it is not required to prove the design.

**Limitation:** this contract still needs real Hermes/Mnemosyne/Hindsight adapter tests and install/update survivability tests before publication.
