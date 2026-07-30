Type: grilling
Status: resolved
Claimed by: default router session
Blocked by: 08, 09

## Question

What isolated rollout stages, stop conditions, rollback steps, recovery checks, and go/no-go evidence are required before publish readiness can be declared?

## Parent map

https://github.com/jtstothard/hermes-tiered-memory/issues/1

## Completion test

Record one clear decision, the evidence needed to support it, and any newly visible successor questions. Keep the result within one fresh agent session.

## Answer

**Verdict: staged rollout/rollback rehearsal contract defined; gated behind implementation.**

All research/contract child issues (#2–#9) have passed fresh independent adversarial review with zero REQUEST-CHANGES. The contracts are internally consistent and every remaining publish blocker is a real implementation gate. This issue defines the rollout/rollback rehearsal acceptance contract that gates the final publish decision.

### Rollout scope

**Disposable isolated services only.** The rehearsal uses throwaway Hermes + seeded Mnemosyne + seeded Hindsight instances on disposable infrastructure, fully torn down after the rehearsal. No estate deployment, no production Hermes profile, no live production data, no production credentials. This matches the map's isolation boundary and the #9 security review boundary.

### Mandatory acceptance criteria (all four must pass)

The rehearsal must demonstrate, in order, against disposable isolated services:

1. **Plugin load and one-provider enforcement.** The composite provider loads through Hermes' provider interface, registers exactly one memory provider, exposes one tool namespace (`memory_search`, `memory_store`), rejects duplicate tool registration, and requires no Hermes core changes. A second configured provider must fail closed.

2. **End-to-end memory round-trip.** A `memory_store` writes synchronously to L1 (Mnemosyne); the outbox queues an asynchronous L2 retain; `memory_search` queries L1 first, falls through to L2 (Hindsight) on a controlled miss, and backfills L1. The promoted document is retrievable from L2 after the outbox drains.

3. **Outbox crash-recovery durability.** Kill the process mid-promotion (after L1 write, before L2 retain completes). Restart. Confirm: no data loss, the outbox row recovers via lease/fence/CAS, the promotion completes or quarantines according to #4/#6, and `queue_drained` is truthful.

4. **Clean rollback.** Uninstall/disable the plugin. Confirm Hermes returns to pre-plugin state: no orphan tools, no broken configuration, no residual provider registration, no orphaned outbox/WAL blocking startup. First-release uninstall/disable recovery evidence is required per #8; artifact rollback is not required for release 1.

### Stop conditions

Abort the rehearsal immediately and do not publish if any of the following occur:

- Any acceptance criterion fails and cannot be resolved within the disposable environment.
- Data loss is observed (L1 write succeeds but the document is not recoverable through L1 or L2 after recovery).
- The plugin corrupts Hermes state, leaves orphan tools after uninstall, or blocks startup.
- A security finding from #9 (secret leakage, unauthorized network activity, credential exposure) is observed.
- The plugin modifies Hermes core behavior outside the declared provider interface.

### Go/no-go rule

**Publish requires Jay's explicit go/no-go after reviewing the rehearsal evidence.** All four acceptance criteria passing is necessary but not sufficient. The rehearsal evidence (commands, outputs, logs, timestamps, environment identity) must be retained and reviewed before the publish decision. No auto-publish.

### Evidence required

For each acceptance criterion, retain:

- Exact commands executed and their exit codes.
- Standard output and standard error (content-free / redacted per #9 telemetry policy).
- Service versions (Hermes, Mnemosyne, Hindsight) and disposable environment identity.
- Timestamps (RFC 3339 UTC) for each stage.
- Confirmation that the disposable environment was torn down after the rehearsal.

### Successor questions (visible after grilling)

- Who builds the disposable isolated environment and what infrastructure does it use? (Implementation/coder lane.)
- What exact Hermes, Mnemosyne, and Hindsight versions are pinned for the rehearsal? (Must match #2 adapter contract: Hermes v2026.7.20, Mnemosyne v3.14.0, Hindsight v0.8.6, unless a later contract update changes them.)
- Does the rehearsal run on the existing host, a VM, or a container? (Implementation decision, constrained by the disposable-isolation boundary.)
- Is a CI-gated version of this rehearsal required for ongoing releases, or is it a one-time publish gate? (Defer to post-publish.)
