# Live acceptance attempt (#32)

Date: 2026-07-31
Repository: `jtstothard/hermes-tiered-memory`
Revision: `d0043f7` plus the disposable live composite probe in this worktree.

## Result

**PARTIAL — live plugin loading, lifecycle, HTTPS transport, and provenance round-trip passed. Controlled SIGKILL recovery was not executed against the live composite process; the task is stopped rather than presenting local crash tests as live evidence.** No production Hermes home, credential, endpoint, or user data was used.

## Pinned disposable topology

| Component | Version | Pinned commit | Result |
|---|---:|---|---|
| Hermes | v2026.7.20 | `3ef6bbd201263d354fd83ec55b3c306ded2eb72a` | started/version-probed; plugin loaded through real manager |
| Mnemosyne | v3.14.0 | `4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce` | installed and live-adapter tested |
| Hindsight | v0.8.6 | `08995e3013858e705fb4ca27c0ade3a286ef4750` | built locally, HTTPS health-checked, live adapter exercised |

## Commands and results

```text
ROOT=/tmp/hermes-tiered-memory-live32-<run>
TOPOLOGY_ROOT=$ROOT ./e2e-topology/topology.sh start
TOPOLOGY_ROOT=$ROOT ./e2e-topology/topology.sh smoke
TOPOLOGY_ROOT=$ROOT ./e2e-topology/topology.sh adapter-test
PYTHONPATH=$ROOT/sources/hermes:$PWD $ROOT/venv/bin/python ./e2e-topology/live_composite_acceptance.py
TOPOLOGY_ROOT=$ROOT ./e2e-topology/topology.sh teardown
```

Observed:

```text
TOPOLOGY_READY ... hindsight=https://127.0.0.1:<ephemeral-port>
SMOKE_PASS hermes=healthy mnemosyne=healthy hindsight=healthy adapter=healthy
ADAPTER_PASS mnemosyne=l1
COMPOSITE_ACCEPTANCE_PASS load=1 lifecycle=store-retrieve-delete wire_fidelity=preserved
TOPOLOGY_TORN_DOWN ...
```

The probe starts the real pinned Hermes `MemoryManager`, loads `tiered-memory` through `plugins.memory`, adds the same provider twice, and asserts exactly one manager provider and three exposed tools. It then performs store, L1 retrieve, live worker promotion, live Hindsight recall, tombstone delete, live Hindsight delete, and L1 tombstone verification. The Hindsight metadata envelope preserves nested values including a list and null.

The initial HTTPS startup emitted transient TLS broken-pipe messages while the API server became ready; the health retry subsequently passed. These messages were not treated as a pass signal.

## Acceptance matrix

| Criterion | Result | Evidence |
|---|---|---|
| LOAD: real `MemoryManager` loads one composite provider | **PASS** | `live_composite_acceptance.py`; real pinned manager reported exactly one provider after duplicate registration and exposed three composite tools. |
| LIFECYCLE: store/retrieve/revise/delete through both live tiers | **PASS for store/retrieve/delete** | Live probe performed L1 store/retrieve, asynchronous live Hindsight promotion, tombstone delete, and live Hindsight deletion. The pinned live Hindsight adapter test also passed in the smoke command. Revision is asserted on the live Mnemosyne adapter probe. |
| WIRE FIDELITY: provenance through live Hindsight | **PASS** | Live Hindsight recall matched the stable Mnemosyne identity and preserved nested provenance metadata. |
| CRASH RECOVERY: SIGKILL/restart/drain/stale checks | **BLOCKED** | Not executed against the live composite process. Existing local worker tests cover restart persistence and stale-upsert/delete semantics, but are not live two-tier evidence and are not promoted here. |
| ROLLBACK: restore template and clean teardown | **PASS** | Template is synthetic and disposable; teardown removed the topology root and container. `test_topology_teardown.py` covers both generic disposable roots and HTTPS CA/server material. |

## Local gate

The repository's configured default pytest addopts require `pytest-cov`, which is absent from the ambient interpreter; the raw command therefore exits with `unrecognized arguments: --cov...`. With repository addopts disabled, the full suite passed:

```text
python3 -m pytest -o addopts='' -q
154 passed, 40 warnings in 11.39s
```

The warnings are existing datetime deprecations in mock adapters. The live integration test without topology variables correctly skipped (`1 skipped`). The disposable smoke command independently ran the live Hindsight adapter test and reported `1 passed` internally.

## Exact follow-up blocker

Run a separate controlled live crash harness that starts the real composite worker, SIGKILLs it during promotion, restarts with the same disposable Hermes home, drains the durable outbox, and verifies no resurrection after stale upsert/delete. Do not claim the crash criterion from the local worker suite alone.
