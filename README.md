# Hermes Tiered Memory

`hermes-tiered-memory` is a single Hermes Agent memory provider with a hot L1
(Mnemosyne) and durable L2 (Hindsight). It keeps the Hermes-facing namespace
small and explicit, fails closed when dependencies or configuration are invalid,
and promotes L1 writes through a durable local outbox.

Version: **1.0.1**

## Public tools

The provider exposes exactly these public memory tools:

- `tiered_memory_search` — search with `query` and an optional `limit` (1–100).
- `tiered_memory_store` — store `content` with optional object `metadata`.
- `tiered_memory_delete` — delete by `memory_id`.

The L1 and L2 adapters are internal implementation details. They do not register
additional Hermes tools. This project does not add native `hermes tiered-memory`
commands; its standalone commands are the `hermes-tiered-memory` console script
described below.

## Architecture

```text
Hermes Agent
  └─ one CompositeProvider
       ├─ Mnemosyne L1 (profile/bank-scoped local memory)
       ├─ durable SQLite outbox (L1 write → asynchronous promotion)
       └─ Hindsight L2 (HTTPS, stable document identity, provenance metadata)
```

Reads are L1-first, with L2 recall and L1 backfill on a miss. Writes commit to
L1 and enqueue promotion. Deletes create durable L1 tombstone state and enqueue
L2 deletion. The provider uses one configured profile and bank scope; it does not
use ambient Mnemosyne defaults.

## Installation

Requirements: Python `>=3.11,<3.14` and `hermes-agent>=0.7.0`.

Install the package and the live adapter extras in the same Python environment
used by Hermes:

```bash
python3 -m pip install 'hermes-tiered-memory[mnemosyne,hindsight]'
```

For a source checkout, build and install a wheel, then install the Hermes plugin
shim. `HERMES_HOME` must already exist:

```bash
python3 -m build --wheel
scripts/install_plugin.sh dist/hermes_tiered_memory-1.0.0-py3-none-any.whl "$HERMES_HOME"
```

The script installs the wheel, creates the `tiered-memory` plugin shim, and
writes its `plugin.yaml`. Then set `memory.provider: tiered-memory` in the
selected profile and restart Hermes. See [INSTALL.md](INSTALL.md) for discovery
and troubleshooting details.

## Provisioning and diagnosis

These are the supported standalone commands:

```bash
hermes-tiered-memory provision --help
hermes-tiered-memory doctor --help
```

Provision a signed endpoint configuration and a reviewable Hermes YAML patch:

```bash
hermes-tiered-memory provision \
  --output-dir ./tiered-memory-config \
  --key-id operator-ed25519-1 \
  --config-id hindsight-config-1 \
  --host hindsight.example.invalid \
  --auth-mode api_key
```

`provision` generates an Ed25519 private key, canonical protected config,
detached signature, and patch. It never accepts or reads the Hindsight API key.
Use a real HTTPS hostname and review the generated patch before applying it.
`--auth-mode` is exactly `api_key` or `none`.

Run read-only checks with the generated files and public key:

```bash
hermes-tiered-memory doctor \
  --config ./tiered-memory-config/protected-config.json \
  --signature ./tiered-memory-config/protected-config.sig \
  --verification-key operator-ed25519-1=<base64-public-key>
```

`doctor` checks optional extras, protected-config validity, L1 availability, and
optionally an L2 `/health` endpoint. It does not write the live replay state.

## Configuration and secret boundary

The provider reads `memory.tiered` through Hermes' effective profile
configuration. A minimal shape is:

```yaml
memory:
  provider: tiered-memory
  tiered:
    hindsight:
      auth_mode: api_key # api_key or none
    verification_keys:
      - key_id: operator-ed25519-1
        algorithm: ed25519
        public_key_b64: <base64-public-key>
    protected_config:
      path: ./protected-config.json
      signature:
        path: ./protected-config.sig

profiles:
  <active-profile>:
    secrets:
      hindsight:
        api_key: <profile-secret> # required only for auth_mode: api_key
```

For `api_key`, the non-empty credential must exist in the active profile's
Hermes-owned secrets at `profiles.<active-profile>.secrets.hindsight.api_key`.
For `none`, that secret must be absent. The provider rejects both mismatches at
startup. Credentials are not stored in the protected endpoint file, generated
by `provision`, or sent by `doctor`; the API key is used only by the L2 HTTP
adapter's authorization header.

Read [docs/security.md](docs/security.md) for the protected-config and crypto
boundary, [docs/migration.md](docs/migration.md) for compatibility and upgrade
notes, and [docs/ci-release.md](docs/ci-release.md) for CI and release behavior.

## Quickstart

1. Install `hermes-tiered-memory[mnemosyne,hindsight]` into the Hermes Python environment.
2. Run `hermes-tiered-memory provision ...` and keep the private key mode `0600`.
3. Apply the generated `memory.tiered` patch after review; put any API key in
   the active profile's Hermes secret configuration, never in the patch.
4. Set `memory.provider: tiered-memory` and restart Hermes.
5. Run `hermes-tiered-memory doctor ...` before exercising the three public tools.

## Documentation boundaries

`README.md`, `INSTALL.md`, and `docs/` are user-facing documentation. The
`research/` directory, `map.md`, `issues/`, and `DEVELOPER.md` are internal
design history or maintainer material, not product compatibility promises.
Start with [docs/internal-design-history.md](docs/internal-design-history.md) if
you need that distinction.

## Development

```bash
python3 -m pip install -e '.[test]'
python3 -m pytest -q
```

See [CHANGELOG.md](CHANGELOG.md) and [docs/release-notes-v1.0.0.md](docs/release-notes-v1.0.0.md)
for the v1.0.0 release record.
