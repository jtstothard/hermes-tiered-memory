# Wayfinder Map: Mnemosyne L1 → Hindsight L2 Memory Promotion

`wayfinder:map` · local-markdown tracker

## Destination

A **design spec + working composite-provider prototype** that proves a single Hermes memory plugin can serve Mnemosyne (L1, per-profile SQLite, sub-ms, zero-network) and Hindsight (L2, PostgreSQL+pgvector, durable, cross-profile) simultaneously — with a defined L1→L2 promotion policy — ready to hand off to a **harden + publish successor effort**.

## Notes

**Framing constraints** (settled by research + this session's grilling; every ticket treats these as givens):

- **Single composite plugin** presents as ONE provider to Hermes, bypassing the one-provider guard. No Hermes core change needed.
- **Hindsight remains the source of truth** for cross-profile and long-term memory. L1 is a cache/tier, never authoritative across profiles.
- **Per-profile isolation mirrored into L2**: each profile's Mnemosyne SQLite DB promotes into its own Hindsight bank. No cross-profile bleed by design.
- **Must not break existing Hindsight deployment** (35K+ facts, production since July 2026).
- **Promotion must NOT depend on Hindsight reflect loop** — writes use `retain`/`retain_batch_async`, not reflect.
- **Promotion must NOT depend on local LLM availability** — Mnemosyne's AAAK fallback works without an LLM.
- **License-clean architecture**: the plugin calls both APIs, bundles neither. Hindsight license is a publish-successor audit concern.
- **Configurable & portable**: no hardcoded IPs, bank names, or profile assumptions.

**Skills to consult:** `/grilling` + `/domain-modeling` for policy tickets; `/prototype` for the composite shell.

**Map-level risk:** tickets 02–06 assume the composite masquerade (ticket 01) is viable. If the prototype fails, the map resets.

## Decisions so far

- [01-composite-provider-boundary](issues/01-composite-provider-boundary.md) — A single composite provider boundary is viable in an isolated fake-based prototype: L1-first reads, async L2 retain, one tool namespace, profile/bank isolation, and L2 failure containment all passed; live adapter integration remains unverified.
- [02-read-write-routing-policy](issues/02-read-write-routing-policy.md) — Use cache-first reads, L1 synchronous writes, asynchronous durable L2 promotion, L2→L1 backfill on misses, stale-while-revalidate during outages, tombstones for deletes, and lazy cold start.
- [03-promotion-policy](issues/03-promotion-policy.md) — Promote successful consolidated facts/summaries on consolidation events plus reconciliation sweeps; keep raw working memory L1-only unless explicitly important; initially use no hard confidence threshold.
- [04-identity-dedup-conflicts](issues/04-identity-dedup-conflicts.md) — Use `(profile, bank, mnemosyne_id)` as stable origin identity; replay is a no-op, changed payloads revise with tombstones, and unrelated semantic conflicts are retained rather than overwritten.
- [05-metadata-roundtrip](issues/05-metadata-roundtrip.md) — Use a dedicated Hindsight `metadata` provenance envelope for Mnemosyne-only fields; native fact identity, boundaries, canonical entities, proof counts, and consolidation timestamps are derived/lossy.
- [06-failure-recovery-observability](issues/06-failure-recovery-observability.md) — Use a durable local outbox with bounded retries, reconciliation, quarantine/replay, per-item checkpoints, and alerts only for quarantine, sustained lag, retry spikes, or drift.
- [07-hermes-integration-config](issues/07-hermes-integration-config.md) — Use one composite Hermes plugin with embedded durable outbox/worker; no Hermes core change or initial sidecar, with profile-safe configurable adapters and fail-closed install verification.
- [08-prototype-acceptance-handoff](issues/08-prototype-acceptance-handoff.md) — The complete contract passes 11/11 fake/stdlib acceptance cases and an independent validator; hand off to harden + publish, not production.


## Not yet specified

No additional decisions are required for this map's destination. The following are deliberately deferred to the harden + publish successor: live adapter/lifecycle behavior, production outbox durability, Hindsight-native revision/delete mechanics, TTL/eviction/bootstrap measurements, and operational/install/security evidence.

## Out of scope

- **Full estate deployment across all profiles** — successor (harden + publish).
- **Hindsight reflect loop fix** — tracked separately.
- **Upstreaming composite provider to Hermes core** — successor, post-publish validation.
- **Production implementation of the acceptance harness** — the harness is throwaway evidence only.
