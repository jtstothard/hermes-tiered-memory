# Live acceptance attempt (#32)

Date: 2026-07-31
Repository: `jtstothard/hermes-tiered-memory`
Revision: `ee6f7dc64baff7924de64c0054bdb1b3e55406e0` plus the safe disposable-topology teardown fix in this worktree.

## Result

**STOPPED — the disposable topology is healthy, but it cannot initialize the composite provider against both live tiers without violating the protected-endpoint contract.** No production Hermes home, credential, endpoint, or user data was read or used.

The task explicitly permits stopping with exact blocking evidence. This evidence is intentionally sanitized: it includes no reusable credential values, container IDs, or full local paths.

## Pinned disposable topology evidence

The topology started from source commits pinned in `e2e-topology/pins.json`:

| Component | Version | Pinned commit | Result |
|---|---:|---|---|
| Hermes | v2026.7.20 | `3ef6bbd201263d354fd83ec55b3c306ded2eb72a` | started/version-probed |
| Mnemosyne | v3.14.0 | `4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce` | installed and live-adapter tested |
| Hindsight | v0.8.6 | `08995e3013858e705fb4ca27c0ade3a286ef4750` | built locally and health-checked |

Commands run, with a fresh disposable root:

```bash
TOPOLOGY_ROOT=/tmp/hermes-tiered-memory-live-acceptance-<id> ./e2e-topology/topology.sh start
TOPOLOGY_ROOT=/tmp/hermes-tiered-memory-live-acceptance-<id> ./e2e-topology/topology.sh smoke
TOPOLOGY_ROOT=/tmp/hermes-tiered-memory-live-acceptance-<id> ./e2e-topology/topology.sh adapter-test
HINDSIGHT_E2E_URL=http://127.0.0.1:<ephemeral-port> python -m pytest -q \
  tests/integration/test_hindsight_l2_live.py
```

Observed results:

```text
TOPOLOGY_READY
SMOKE_PASS hermes=healthy mnemosyne=healthy hindsight=healthy adapter=healthy
ADAPTER_PASS mnemosyne=l1
1 passed  # live Hindsight adapter test
```

The Hindsight health response reported `status: healthy` and `database: connected`.

## Acceptance matrix

| Criterion | Result | Exact evidence |
|---|---|---|
| LOAD: real `MemoryManager` loads one composite provider | **BLOCKED** | Pinned `MemoryManager.initialize_all()` calls `provider.initialize(session_id, **kwargs)` but the pinned agent-init call does not pass the loaded config mapping. `CompositeProvider.initialize()` correctly rejects a config-less call with `Tiered provider requires live tiered configuration`; existing pinned-Hermes contract test proves the failed provider is removed before tool exposure. |
| LIFECYCLE: store/retrieve/revise/delete through both live tiers | **BLOCKED** | The composite cannot initialize because its protected L2 configuration requires HTTPS, while the disposable Hindsight API is deliberately exposed only on ephemeral loopback HTTP. Making the composite accept this endpoint would weaken its fail-closed security contract. |
| WIRE FIDELITY: provenance through live Hindsight | **PARTIAL** | The concrete live `HindsightL2Adapter` test passed against the disposable Hindsight server and verifies the nested metadata envelope round-trip. It is not a composite-provider proof because initialization is blocked before L2 wiring. |
| CRASH RECOVERY: SIGKILL/restart/drain/stale checks | **BLOCKED** | No safe composite process can be started. Existing crash/recovery coverage remains local SQLite/fake-L2 only and must not be presented as a live two-tier result. |
| ROLLBACK: restore pre-composite template and clean teardown | **PASS** | The disposable template stayed on `memory.provider: mnemosyne`; the composite was never written into it. `topology.sh teardown` removed the active disposable root and `test ! -e "$ROOT"` passed. A regression test now covers task-specific disposable root names. |

## Security-gate probe

A synthetic protected configuration targeting the topology's loopback HTTP API was checked before any composite attempt. It was rejected with:

```text
PRECHECK_BLOCKED Invalid scheme: http
```

This is expected and correct: `HindsightEndpointConfig` only admits `https`. The topology's HTTP listener is suitable for direct adapter protocol testing but not for the protected composite endpoint contract.

## Local and CI-equivalent gate

The full declared CI-equivalent command was rerun after the teardown regression fix:

```text
141 passed, 52 warnings
```

The warnings are pre-existing `datetime.utcnow()` deprecations and unclosed SQLite `ResourceWarning`s; they do not change the pass exit code. The live adapter integration test independently passed.

The repository CI workflow runs `python -m pytest -q` with the declared `[test]` extra. The same command was run in a fresh disposable virtualenv.

## Required follow-up

Do not relax HTTPS or inject an ambient config to force this task green. A subsequent acceptance task needs:

1. a disposable HTTPS Hindsight endpoint with a test CA accepted by the protected configuration; and
2. a pinned-Hermes configuration-propagation seam (or an approved plugin-owned configuration bridge) that supplies the validated `tiered` mapping to `MemoryProvider.initialize()` without core changes that bypass the provider lifecycle.

Only after those two prerequisites are proven may the composite lifecycle and controlled SIGKILL scenarios be executed.
