# Issue #2 research: live adapter contract and isolated integration boundary

Date: 2026-07-29
Repository under review: `jtstothard/hermes-tiered-memory` at `fe3caeb`.

## Scope and evidence policy

This is a source-only design report. I inspected the repository and pinned upstream source trees locally, but did not configure Hermes, contact a Hindsight deployment, read estate data, change production, commit, or open an issue. Claims marked **UNVERIFIED** require execution in the disposable environment described below.

Primary sources used:

- Hermes Agent `v2026.7.20`, commit [`3ef6bbd`](https://github.com/NousResearch/hermes-agent/tree/3ef6bbd201263d354fd83ec55b3c306ded2eb72a). (Earlier draft of this report incorrectly cited `v0.18.0`/`7c1a029` — that is an unrelated older tag; corrected on router review 2026-07-29.)
- Mnemosyne `v3.14.0`, peeled commit [`4e5ed14`](https://github.com/mnemosyne-oss/mnemosyne/tree/4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce) from tag `v3.14.0`; repository metadata names the upstream project [`AxDSan/mnemosyne`](https://github.com/AxDSan/mnemosyne).
- Hindsight `v0.8.6`, commit [`08995e3`](https://github.com/vectorize-io/hindsight/tree/08995e3013858e705fb4ca27c0ade3a286ef4750).
- Local repo source and prior prototype are evidence of intended behavior only; they are not evidence of live compatibility (see [`README.md`](../README.md), [`ADR.md`](../prototype-acceptance-handoff/ADR.md)).

## Recommendation up front

Implement issue #2 as a single Hermes memory-provider plugin that owns both adapters and exposes one provider/tool namespace. Keep Mnemosyne local and synchronous; enqueue Hindsight promotion behind a durable local outbox. Use Hindsight `document_id` as the revision/idempotency primitive, not an invented fact-level update API. Do not claim the contract is complete until a matrix of pinned-source tests passes in a temporary Hermes home against a disposable Hindsight server/database. The live integration is currently **unverified**.

## Pinned versions

Recommended initial test pins:

| Component | Pin | Evidence and rationale |
|---|---|---|
| Hermes Agent | `v2026.7.20` / `3ef6bbd201263d354fd83ec55b3c306ded2eb72a` | Latest release tag as of 2026-07-29; verified via GitHub Tags API. The loader, `MemoryProvider` ABC, and one-provider boundary cited below were read directly from this commit. Exact commit avoids loader drift. |
| Mnemosyne | `v3.14.0` / `4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce` | Tag exists in the upstream repository and source exposes the Hermes integration and bank-aware `Mnemosyne` construction. The tag is pinned to peeled commit `4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce`, verified locally with `git rev-parse v3.14.0^{commit}`. |
| Hindsight | `v0.8.6` / `08995e3013858e705fb4ca27c0ade3a286ef4750` | Exact release tag/commit. Hindsight 0.8.x source documents `document_id`, replace/append, metadata, async retain, and document deletion. |
| Python | Hermes `>=3.11,<3.14`; Mnemosyne `>=3.10` | Hermes's upper bound is explicit in its project metadata. Use Python 3.11 or 3.12 in CI first; **UNVERIFIED** whether all three pinned dependency sets resolve together on 3.13. |

Do not use floating `main`, `latest`, or unbounded package ranges in the integration job. Record tag, peeled commit, Python version, lockfile hash, and container image digest as CI artifacts.

## Hermes lifecycle, loader hooks, and the one-provider boundary

Hermes's memory loader is directory-based. [`plugins/memory/__init__.py`](https://github.com/NousResearch/hermes-agent/blob/7c1a029553d87c43ecff8a3821336bc95872213b/plugins/memory/__init__.py) scans bundled `plugins/memory/<name>/` and user `$HERMES_HOME/plugins/<name>/`; a provider must have `__init__.py`, and user plugins are heuristically recognized by `register_memory_provider` or `MemoryProvider` text. Bundled providers win name collisions.

The load sequence is:

1. Config reads `memory.provider`.
2. `agent/agent_init.py` calls `load_memory_provider(name)`.
3. The loader imports the provider and first tries `register(ctx)`; the fake context captures `ctx.register_memory_provider(provider)`. Otherwise it instantiates a `MemoryProvider` subclass.
4. `MemoryManager.add_provider()` accepts the loaded provider; `initialize_all()` is called with session, platform, Hermes home, context, user/chat/thread identity, and active profile identity.
5. `inject_memory_provider_tools(agent)` injects the provider's tools.

The source explicitly says: “Only ONE provider can be active at a time,” selected by `memory.provider`. This is the critical boundary: Mnemosyne and Hindsight must not each register as Hermes providers. The composite must be the only registered `MemoryProvider`; lower-level adapters must not register tools or hooks. A second provider is not a supported composition mechanism.

The Mnemosyne Hermes adapter demonstrates both relevant registration paths: `register_memory_provider(ctx)` and plugin `register(ctx)` in [`integrations/hermes/src/mnemosyne_hermes/__init__.py`](https://github.com/mnemosyne-oss/mnemosyne/blob/v3.14.0/integrations/hermes/src/mnemosyne_hermes/__init__.py). **UNVERIFIED:** whether the final composite should use only the memory-provider path or also Hermes's general plugin hook path; use only the former for the first boundary to avoid duplicate tools/hooks.

## Mnemosyne adapter contract and isolation

The public core API has:

- `Mnemosyne(..., bank=..., session_id=..., channel_id=...)` construction in [`mnemosyne/core/memory.py`](https://github.com/mnemosyne-oss/mnemosyne/blob/v3.14.0/mnemosyne/core/memory.py).
- `remember(content, source=..., ...)` and `remember_batch(items, ...)` in [`mnemosyne/core/beam.py`](https://github.com/mnemosyne-oss/mnemosyne/blob/v3.14.0/mnemosyne/core/beam.py).
- `recall(query, top_k=..., ...)` and `recall_enhanced(...)` in the same Beam source.
- `forget(memory_id)` and `sleep(...)` in [`mnemosyne/core/memory.py`](https://github.com/mnemosyne-oss/mnemosyne/blob/v3.14.0/mnemosyne/core/memory.py).

The Hermes provider initializes the bank-aware object when profile isolation is enabled, resolving a profile bank before constructing `Mnemosyne`; otherwise it falls back to a profile-home data path. The source also derives a stable session scope from `gateway_session_key` or Hermes `session_id`. These are strong source-level signals for isolation, but not proof of our composite's mapping.

Proposed adapter interface (our contract, not an upstream API):

```text
L1.remember(record) -> source_id / receipt
L1.remember_batch(records) -> per-item receipts
L1.recall(query, scope, limit) -> records
L1.forget(source_id) -> bool
L1.sleep/durable_boundary() -> result
L1.close() -> result
```

The adapter must bind every operation to `(Hermes profile, Mnemosyne bank, session/channel scope)` and return a normalized record retaining the Mnemosyne source ID, revision, content, timestamps, and provenance. Use separate temporary DBs for separate profiles in tests. Never point tests at a default user database.

**UNVERIFIED:** exact transaction/locking behavior under concurrent `remember_batch`, exact deletion durability after restart, and whether every metadata field survives `recall`. Test these against the pinned SQLite implementation.

## Hindsight HTTP/API contract

Hindsight's pinned docs and generated client are the authority, not assumptions from the fake prototype.

### Retain and batch

The retain docs at [`hindsight-docs/docs/developer/api/retain.mdx`](https://github.com/vectorize-io/hindsight/blob/08995e3013858e705fb4ca27c0ade3a286ef4750/hindsight-docs/docs/developer/api/retain.mdx) specify item content plus `timestamp`, `context`, `metadata`, `document_id`, `update_mode`, entities, and tags. A caller-supplied `document_id` makes retain idempotent: default `replace` deletes the prior document and associated memories before processing the new content; `append` concatenates and reprocesses. Metadata is stored on memory units and returned with recall according to the docs.

The generated Python client and OpenAPI definition are in [`hindsight-clients/python/hindsight_client_api/api/memory_api.py`](https://github.com/vectorize-io/hindsight/blob/08995e3013858e705fb4ca27c0ade3a286ef4750/hindsight-clients/python/hindsight_client_api/api/memory_api.py) and [`hindsight-clients/go/api/openapi.yaml`](https://github.com/vectorize-io/hindsight/blob/08995e3013858e705fb4a27c0ade3a286ef4750/hindsight-clients/go/api/openapi.yaml). The source has synchronous and async retain paths and tests for async batch retain (`tests/test_async_batch_retain.py`). Exact route spelling and response schema must be generated from this pinned OpenAPI at test setup, not hand-copied.

### Revision, delete, and metadata semantics

Use one stable document ID per `(profile, bank, mnemosyne_id)` in the Hindsight bank. A changed Mnemosyne record is a Hindsight document replacement, not a fact-level revision. A Mnemosyne delete maps to the Hindsight document-delete endpoint, whose documentation is [`hindsight-docs/docs/developer/api/documents.mdx`](https://github.com/vectorize-io/hindsight/blob/08995e3013858e705fb4ca27c0ade3a286ef4750/hindsight-docs/docs/developer/api/documents.mdx); the docs describe deletion by bank and document ID. Hindsight source also explicitly distinguishes `MemoryEngine.delete_document` from incidental retain behavior in migration/test comments.

Carry Mnemosyne-only data in Hindsight `metadata`, for example:

```json
{
  "origin": "mnemosyne",
  "profile": "test-profile-a",
  "bank": "test-profile-a",
  "mnemosyne_id": "m-001",
  "mnemosyne_revision": 2
}
```

Do not treat Hindsight-generated memory IDs as the origin identity. Do not use `reflect` as a promotion dependency. **UNVERIFIED:** exact metadata round-trip through retain, recall, document list/get, replacement, and delete at this release; test it explicitly because schema/API drift is the primary risk.

### Failure semantics to model

The adapter must distinguish: transport timeout/connection refusal; HTTP 401/403; 404 unknown bank/document; 409 conflict; 413 oversized payload; 422 validation; 429 rate limit; 5xx server failure; malformed success payload; async operation accepted but later failed; and per-item batch partial failure. The Hindsight source contains tests for transient extraction failure, cancellation, async operation IDs, and same-document concurrency, but **UNVERIFIED** is the exact wire-level status/body contract for every case.

Policy: Mnemosyne success must not be rolled back because Hindsight is unavailable. Persist an outbox item with idempotency key, retry only transient failures, quarantine permanent failures, and expose reconciliation. A batch response must be treated as per-item until the pinned client/schema proves atomicity.

## Disposable isolated environment

Use a temporary directory and no inherited Hermes home or secrets:

```bash
set -eu
ROOT=$(mktemp -d)
export HERMES_HOME="$ROOT/hermes"
export XDG_CONFIG_HOME="$ROOT/config"
export MNEMOSYNE_DATA_DIR="$ROOT/mnemosyne"
mkdir -p "$HERMES_HOME" "$XDG_CONFIG_HOME" "$MNEMOSYNE_DATA_DIR"
# Start only a pinned Hindsight container/server with a fresh database/volume.
# Generate a random test bank: it must never equal an estate bank.
trap 'rm -rf "$ROOT"' EXIT
```

Preferred CI topology: one job-local Hindsight container, fresh Postgres/pgvector volume, random bank name, no host-mounted production paths, no production `.env`, no network route to the estate. If a Hindsight image digest cannot be derived from the v0.8.6 source release, mark the container test **UNVERIFIED** rather than substituting `latest`.

## Synthetic fixtures and acceptance matrix

Fixtures should be deterministic and contain no personal or estate data:

- `m-001`: “Jay uses a vegan recipe notebook”; metadata includes profile `alpha`, revision 1.
- `m-002`: same semantic topic but profile `beta`; proves no cross-profile read.
- `m-003`: revised content for `m-001`, revision 2; same document ID.
- `m-004`: deletion target.
- 20-item batch with two deliberately invalid items; proves partial handling.
- oversized synthetic content; proves 413/validation/retry behavior without real data.
- repeated identical retain; proves idempotency.

Required cases:

1. Hermes discovers exactly one composite provider and exactly one memory tool namespace.
2. L1 hit avoids Hindsight; L1 miss queries Hindsight and backfills L1.
3. L1 write survives Hindsight timeout/refusal.
4. Same source ID/revision replay is a no-op or safe replacement.
5. Revision 2 replaces only the owning Hindsight document.
6. Delete removes/tombstones only the owning document and remains correct after restart.
7. Alpha cannot recall beta; bank names and metadata agree.
8. Hindsight batch success, partial failure, timeout, 429, 5xx, malformed body.
9. Restart with pending outbox drains idempotently.
10. No credentials, estate endpoints, or production paths are read.

## Local and CI evidence commands

Source/pin verification:

```bash
git -C /tmp/hermes-agent rev-parse HEAD
git -C /tmp/mnemosyne describe --tags --exact-match HEAD || true
git -C /tmp/hindsight rev-parse HEAD
python3 -m compileall -q /tmp/hermes-agent /tmp/mnemosyne /tmp/hindsight
```

Repository prototype baseline (fake-only, not live evidence):

```bash
cd /repo/prototype-acceptance-handoff
python3 -m unittest discover -s tests -v
python3 run_evidence.py
python3 verify_evidence.py
```

Once the real adapter exists, the CI gate should be equivalent to:

```bash
python3 -m pytest -q tests/unit tests/integration
python3 -m pytest -q tests/integration/test_hermes_loader.py
python3 -m pytest -q tests/integration/test_mnemosyne_sqlite.py
python3 -m pytest -q tests/integration/test_hindsight_http.py
python3 -m pytest -q tests/integration/test_failure_isolation.py
```

Publish artifacts: exact SHAs/tags, `pip freeze` or lockfile, Hindsight OpenAPI hash, temporary Hermes-home manifest, sanitized request/response status matrix, and test evidence. Do not include payload contents beyond synthetic fixtures.

## Unresolved questions

- What is the peeled commit for Mnemosyne `v3.14.0`, and does its Hermes integration API remain compatible with Hermes `v0.18.0` without a shim?
- Does Hermes's current `MemoryManager` invoke any provider lifecycle method on shutdown, or must the composite register an explicit close hook?
- Which exact Hermes memory methods/tools must the composite implement to satisfy `MemoryProvider` and avoid duplicate legacy Mnemosyne hooks?
- Is Hindsight batch retain atomic, partially successful, or merely asynchronously accepted at the wire level?
- What exact HTTP status/body is returned for delete of an unknown document, duplicate document IDs, malformed metadata, rate limiting, and async operation failure?
- Does Hindsight v0.8.6 return the custom metadata unchanged after replace, recall, list/get document, and delete/re-read?
- Does Hindsight support a caller-controlled fact revision, or is `document_id` replacement the only supported update primitive?
- What are safe timeout, retry, batch-size, and outbox limits under the target deployment?
- Does Mnemosyne bank deletion/DB cleanup have any cross-profile path behavior that could invalidate isolation?
- Can the disposable Hindsight server run with a deterministic local model/embedding configuration without external credentials? **UNVERIFIED.**

## Concise recommendation for issue #2

Proceed with a live-contract spike, not production integration: pin Hermes `v2026.7.20` at `3ef6bbd`, Mnemosyne `v3.14.0` with its peeled SHA recorded, and Hindsight `v0.8.6` at `08995e3`; build one composite Hermes provider; map Mnemosyne records to Hindsight documents using stable `(profile, bank, mnemosyne_id)` metadata and `document_id`; and prove lifecycle, metadata, revision/delete, partial failure, retry, restart, and cross-profile isolation only inside a fresh temporary environment. Until that matrix passes, retain the repository's current “fake prototype only” status and do not claim live adapter compatibility.
