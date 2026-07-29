# Hermes Tiered Memory

Experimental prototype and design handoff for a tiered Hermes Agent memory architecture:

- **Mnemosyne L1** — per-agent local SQLite hot cache.
- **Hindsight L2** — durable PostgreSQL/pgvector memory and consolidation target.
- **Composite provider** — one Hermes-facing provider and tool namespace.

## Status

This repository captures a **throwaway, fake/stdlib prototype** and the decisions needed for a future harden + publish effort. It is **not production-ready** and must not be deployed as-is.

The acceptance handoff passed 11/11 local contract cases with an independent evidence validator. That result proves only the modeled state machine, not live Hermes, Mnemosyne, Hindsight, network, durability, security, or deployment compatibility.

## Contents

- `map.md` — completed wayfinder map and decision index.
- `issues/` — resolved decision tickets.
- `prototype-composite-provider/` — initial provider-boundary prototype.
- `prototype-acceptance-handoff/` — end-to-end local acceptance harness and ADR-shaped handoff.

## Reproduce the acceptance evidence

```bash
cd prototype-acceptance-handoff
python3 -m unittest discover -s tests -v
python3 run_evidence.py
python3 verify_evidence.py
python3 -m compileall -q acceptance_harness.py tests run_evidence.py verify_evidence.py
```

## Explicit non-goals

- No production Hermes or Hindsight configuration.
- No credentials, private endpoints, or estate-specific secrets.
- No claim of live API compatibility.
- No package publication or deployment.

See `prototype-acceptance-handoff/ADR.md` for known gaps and successor hardening prerequisites.
