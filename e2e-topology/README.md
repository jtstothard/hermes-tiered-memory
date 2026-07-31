# Disposable pinned E2E topology

This directory is a deliberately isolated harness for live integration work. It never reads the ambient Hermes home, profile configuration, `.env` files, credentials, or production paths.

## Requirements

- Python 3.11–3.13, `python3 -m venv`, and Docker
- Local pinned source checkouts (defaults are `/hermes`, `/tmp/mnemosyne-v314`, and `/hindsight`)
- Network access only when creating the disposable virtualenv or building Hindsight

## Commands

```bash
# Create a fresh temp root, verify source pins, install the two Python packages,
# build/start a Hindsight API container, and run health checks.
./e2e-topology/topology.sh start

# Query all three live instances and run deterministic retain/recall checks.
./e2e-topology/topology.sh smoke

# Exercise the repository's Mnemosyne L1 adapter against the pinned live
# instance, including profile/bank isolation and tombstone/revision behavior.
./e2e-topology/topology.sh adapter-test

# Stop containers/processes and remove the entire temp root.
./e2e-topology/topology.sh teardown

# Full lifecycle smoke test: start/query/teardown twice, assert fresh CA material,
# and assert no root leak.
./e2e-topology/topology.sh test
```

`start` writes its state only below `${TOPOLOGY_ROOT:-/tmp/hermes-tiered-memory-e2e}` and refuses to use an existing directory unless it was created by this harness. It creates a template Hermes home, runs the real pinned Hermes CLI against it, creates a disposable Mnemosyne SQLite database, and starts a disposable Hindsight API container with embedded state. Every start also generates a new private test CA and loopback server certificate below the root; the server serves HTTPS and the adapter test trusts only that run's CA. `adapter-test` uses only synthetic adapter fixtures in separate disposable Mnemosyne profile banks, then `test` runs it in both lifecycle passes. `trap` cleanup is installed for failures; run `teardown` explicitly after a successful run.

Pins are recorded in `pins.json` and checked against the source checkout's exact commit and tag. Hindsight is built from the pinned checkout with local models included (so the `local` embeddings provider works without an external API); its only host mounts are the read-only, topology-generated server certificate and private key (never the CA private key), and its LLM URL points at a non-routable loopback port. The test CA and all certificate material are removed with the root during teardown. The smoke test uses only synthetic records (`e2e-alpha-001`, `e2e-beta-001`).

The harness proves the three pinned runtimes can be started, health-checked, queried, and removed in isolation. Its adapter test is limited to the Mnemosyne L1 boundary; it does not exercise Hindsight promotion or Hermes plugin registration.
