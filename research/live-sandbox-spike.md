# Live sandbox verification spike (#24)

Date: 2026-07-31
Repository: `jtstothard/hermes-tiered-memory`
Test branch: `wt/tiered-memory-provider`
Test commit: `15ba821eae1ecc2c5d929e26069fb1e660bcc2fb`

## Result

**STOPPED — live acceptance criteria cannot be demonstrated by this checkout.**

The repository currently contains a fail-closed prototype and deterministic fake/SQLite contract tests, not a runnable Hermes/Mnemosyne/Hindsight integration. The pinned Hermes and Hindsight source commits are available and verified below; Mnemosyne's tag is remotely verifiable, but its package/repository was not installed into the sandbox. No estate data, credentials, production endpoints, or persistent user homes were read or used.

Per issue #24's instruction (“If a contract assumption breaks during live testing, STOP and report — do not paper over it”), this report records the broken integration assumptions rather than substituting mocks for live evidence.

## Disposable environment and safety boundary

Commands used for the repository test worktree:

```bash
ROOT=$(mktemp -d)
export HERMES_HOME="$ROOT/hermes"
export XDG_CONFIG_HOME="$ROOT/config"
export MNEMOSYNE_DATA_DIR="$ROOT/mnemosyne"
mkdir -p "$HERMES_HOME" "$XDG_CONFIG_HOME" "$MNEMOSYNE_DATA_DIR"
trap 'rm -rf "$ROOT"' EXIT
```

The actual test execution used only the checkout and temporary pytest/unittest directories. `docker info` returned server `26.1.5+dfsg1`, but no Hindsight container was started: the repository has no live adapter/client wiring or pinned Hindsight image digest, and starting an unpinned service would violate the acceptance constraints.

## Pinned version verification

Hermes:

```text
$ git -C /hermes show -s --format='%H%n%D%n%s' 3ef6bbd201263d354fd83ec55b3c306ded2eb72a
3ef6bbd201263d354fd83ec55b3c306ded2eb72a
tag: v2026.7.20
chore: release v0.19.0 (2026.7.20) (#68175)
```

Hindsight:

```text
$ git -C /hindsight show -s --format='%H%n%D%n%s' 08995e3013858e705fb4ca27c0ade3a286ef4750
08995e3013858e705fb4ca27c0ade3a286ef4750
tag: v0.8.6
Release v0.8.6
```

Mnemosyne (remote tag verification):

```text
$ git ls-remote https://github.com/AxDSan/mnemosyne.git refs/tags/v3.14.0 refs/tags/v3.14.0^{}
3afdeff0becffd0c83ad5e84fface235baf5359c refs/tags/v3.14.0
4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce refs/tags/v3.14.0^{}
```

The local tiered-memory worktree was reset to the required upstream branch tip:

```text
$ git rev-parse HEAD
15ba821eae1ecc2c5d929e26069fb1e660bcc2fb
```

## Broken assumptions / findings

### F1 — no live dependency set in the sandbox

The pinned packages are not installed in the Python environment:

```text
hermes_agent None
mnemosyne None
hindsight_client None
hindsight None
Python 3.13.5
```

The source checkouts exist for Hermes and Hindsight, but the Mnemosyne checkout/package is absent. Therefore a real `MemoryManager`, Mnemosyne L1, and Hindsight L2 process could not be instantiated without first implementing the missing installation/integration path.

### F2 — the composite is not a pinned Hermes MemoryProvider

Source inspection found:

```text
hermes_tiered_memory/providers/composite.py:18:class CompositeProvider:
hermes_tiered_memory/providers/composite.py:74:    def initialize_all(self) -> None
```

It does not subclass the pinned Hermes `MemoryProvider`, does not implement the ABC lifecycle (`initialize`, `get_tool_schemas`, etc.), and exposes prototype methods (`initialize_all`, `inject_memory_provider_tools`) instead. The package initializer intentionally fails closed when the API is unavailable or the provider is not an implementation of the ABC. This prevents criterion 1 from being claimed; the fake registration tests are not a real `MemoryManager` proof.

### F3 — no live Hindsight server or native adapter is wired

`CompositeProvider._initialize_l1()` and `_initialize_l2()` contain explicit “mock for now” paths. The repository's `mock_l2.py` is the test double used by the worker tests. No Hindsight v0.8.6 HTTP client/server request was made, so exact metadata wire fidelity (criterion 3) is unverified.

### F4 — local test baseline has one unrelated collection/import failure

The documented pytest command's coverage options are not available in the installed environment, so it was run with repository `addopts` overridden:

```bash
pytest -o addopts='-v --tb=short'
```

Result:

```text
122 collected
121 passed, 1 failed
FAILED tests/unit/test_provider_lifecycle.py::test_loader_accepts_same_sequence_identical_payload
ModuleNotFoundError: No module named 'tests.unit'
```

The failure is a test-package import/collection issue, not live integration evidence. The prototype handoff suite independently passed (below).

## Acceptance matrix

| Criterion | Result | Evidence |
|---|---|---|
| 1. LOAD: one composite under real MemoryManager | **NOT DEMONSTRATED** | F2; no pinned Hermes runtime registration occurred. |
| 2. LIFECYCLE: store/retrieve/revise/delete through both tiers | **NOT DEMONSTRATED** | F1/F3; only fake L2 and local SQLite paths are available. |
| 3. WIRE FIDELITY: metadata through Hindsight L2 | **NOT DEMONSTRATED** | No Hindsight server/client request; explicit live gate remains open. |
| 4. CRASH RECOVERY: SIGKILL/restart/outbox drains | **PARTIAL, local-only** | SQLite atomic crash test and fake outbox tests pass; no live L2 promotion was involved. |
| 5. ROLLBACK: pre-composite teardown | **NOT DEMONSTRATED** | No real Hermes process loaded the provider. |

## Local-only evidence (not a substitute for live acceptance)

The repository's prototype acceptance harness passed:

```text
$ python3 -m unittest discover -s tests -v
Ran 11 tests in 0.004s
OK

$ python3 run_evidence.py && python3 verify_evidence.py
"status": "PASS"
validated 11 exact contract cases; fresh_test_exit_code=0
INDEPENDENT_EVIDENCE_PASS
```

The full repository suite result was 121 passed and 1 failed as recorded in F4. These results validate fake/local contracts only; they do not upgrade any criterion above to PASS.

## Required follow-up before rerunning live spike

1. Implement/package a real Hermes `MemoryProvider` subclass and `register(ctx)` path against Hermes `v2026.7.20`.
2. Add a pinned Mnemosyne `v3.14.0` installation/checkout and a concrete L1 adapter.
3. Add a native Hindsight `v0.8.6` client/server test topology with a disposable database and deterministic local model configuration, recording image/source digests.
4. Repair the `tests.unit` import failure (or package the tests) and make the CI command reproducible with `pytest-cov` installed.
5. Rerun all five acceptance cases in a fresh temporary home, retaining sanitized command output and request/response status evidence.

No production change, credential access, estate-data access, or deployment was performed by this spike.
