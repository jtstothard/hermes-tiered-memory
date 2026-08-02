# Hermes Tiered Memory — Composite Provider

A fail-closed Hermes `MemoryProvider` integration using disposable local adapters. It does not claim live Mnemosyne or Hindsight compatibility.

## Scope and Status

This package contains a usable pinned-Hermes `MemoryProvider` implementation backed by disposable SQLite L1 and mock L2 adapters, deterministic unit/contract tests, and developer documentation. It does not provide live adapter integration.

**What is included:**
- Composite-provider prototype with deterministic mock-adapter tests
- Durable outbox tombstones and ordered delete processing
- Protected endpoint configuration validation and fail-closed startup checks
- Fail-closed registration when the pinned Hermes API boundary is unavailable
- No bundled dependencies or Hermes core changes

**What is explicitly NOT included:**
- Live Mnemosyne/Hindsight adapter routes or compatibility evidence
- Outbox crash replay: abandoned `in_flight` rows are not reclaimed (design in research/outbox-crash-recovery-contract.md)
- Live integration tests with pinned Hermes/Mnemosyne/Hindsight services
- Packaging and signing
- Security evidence (TLS trust, OpenAPI route verification)
- Staged rollout/rollback rehearsal

## Architecture

```
Hermes
  └── CompositeProvider (loadable pinned-Hermes provider boundary)
        ├── SQLite L1 adapter (synchronous reads/writes; disposable)
        ├── Mock L2 adapter (reads + retain/batch-retain; disposable)
        └── Promotion behavior (prototype only; no live-service durability claim)
```

The composite owns the sole Hermes-facing memory tool namespace (`tiered_memory_search`, `tiered_memory_store`, and `tiered_memory_delete`). Mnemosyne and Hindsight adapters expose no duplicate Hermes tools.

## Current and future integration

The checked-in provider implements the pinned Hermes `MemoryProvider` ABC and is loadable through the plugin loader. Its current runtime path uses disposable SQLite L1 and mock L2 adapters. It fails closed: if adapter initialization fails, the retained provider instance disables its schemas so Hermes cannot expose its tools.

The following describes the future live-service integration shape only.

### Prerequisites

- Python 3.11, 3.12, or 3.13
- Hermes Agent v2026.7.20 (commit `3ef6bbd`)
- Mnemosyne v3.14.0 (commit `4e5ed14`)
- Hindsight v0.8.6 (commit `08995e3`)
- Protected Hindsight endpoint configuration (see Configuration)

### Future install

```bash
pip install hermes-tiered-memory[mnemosyne,hindsight]
```

For development:

```bash
pip install hermes-tiered-memory[dev]
```

## Future configuration

The composite requires a protected Hindsight endpoint configuration file with integrity authentication.

### Protected Endpoint Source

Create a protected configuration file (JCS JSON, RFC 8785):

```json
{
  "schema_version": 1,
  "key_id": "admin-key-1",
  "config_id": "prod-hindsight-v1",
  "sequence": 1,
  "issued_at": "2026-07-30T00:00:00Z",
  "not_before": "2026-07-30T00:00:00Z",
  "expires_at": "2027-07-30T00:00:00Z",
  "hindsight": {
    "scheme": "https",
    "host": "hindsight.example.com",
    "port": 443,
    "base_path": "/api/v1",
    "tls": {
      "verify": true,
      "ca_file": null
    },
    "proxy": {
      "enabled": false
    }
  }
}
```

### Integrity Authentication

Compute HMAC-SHA-256 as `HMAC(secret, DOMAIN_SEPARATOR + exact_JCS_bytes)` (or Ed25519 over the same domain-separated bytes), with domain separator `hermes-tiered-memory/config/v1\0`. The file bytes must be exactly canonical JCS JSON; whitespace or alternate JSON spellings are rejected.

Store the signature in a separate file (or secret-store object).

### Pinned Verification Keys

The loader authenticates a protected config against a set of **pinned verification keys**. An unknown `key_id`, a missing signature, a wrong algorithm, a stale config, or a rollback attempt all fail closed: the composite provider aborts startup and no adapter or network side effect occurs.

Keys are provisioned through the `tiered.verification_keys` config block. This is the only production key source — environment variables are intentionally not consulted, and the in-process `register_key()` helper is test-only. Each entry pins one key:

```yaml
memory:
  tiered:
    verification_keys:
      - key_id: "admin-ed25519-1"
        algorithm: "ed25519"            # or "hmac-sha-256"
        public_key_b64: "<base64-encoded 32-byte Ed25519 public key>"
        # For HMAC use secret_b64 instead of public_key_b64.
```

Validation is strict and atomic: a non-empty `key_id`, a supported `algorithm`, correctly-lengthed base64 material, and no duplicate `key_id` (within the list or against an already-pinned key). If any entry is malformed, **nothing** is registered and startup fails closed.

### Complete Ed25519 Provisioning Example

1. **Generate a keypair** (do this once, offline; keep the private key secret):

```bash
python scripts/generate_protected_config.py genkey --key-id admin-ed25519-1
# writes admin-ed25519-1.private.pem and prints the public_key_b64
```

2. **Mint the canonical config and detached signature**:

```bash
python scripts/generate_protected_config.py sign \
  --private-key admin-ed25519-1.private.pem \
  --key-id admin-ed25519-1 \
  --config-id prod-hindsight-v1 \
  --host hindsight.example.com \
  --base-path /api/v1 \
  --not-before 2026-08-01T00:00:00Z \
  --expires-at 2027-08-01T00:00:00Z \
  --out protected-hindsight.json --sig protected-hindsight.sig
```

3. **Place the public key in the Hermes config** (profile-owned, mode 0600):

```yaml
memory:
  provider: tiered
  tiered:
    verification_keys:
      - key_id: "admin-ed25519-1"
        algorithm: "ed25519"
        public_key_b64: "<printed by genkey>"
    protected_config:
      path: "/path/to/protected-hindsight.json"
      signature:
        path: "/path/to/protected-hindsight.sig"
```

4. The signed config now loads end-to-end with no test-only key registration.

### Hermes Configuration

The pinned Hermes `MemoryManager.initialize_all()` forwards runtime scope but
does not forward the loaded configuration mapping to `MemoryProvider.initialize()`.
The supported provider-config seam is therefore the effective, profile-scoped
`hermes_cli.config.load_config_readonly()` loader, which Hermes memory plugins
use for their own `memory.<provider>` settings. The composite reads
`memory.tiered` through that loader during `initialize()`, promotes it to its
internal `tiered` runtime block, and retains the full effective config only to
resolve the active profile's Hindsight credential. It does not use an
environment-variable bridge, constructor injection, or a bypass of the
`MemoryManager` lifecycle.

Configure it as:

```yaml
memory:
  provider: tiered
  tiered:
    # Non-security settings (validated before side effects)
    bank_mapping: {}
    timeouts:
      l1_read: 5.0
      l1_write: 10.0
      l2_read: 30.0
      l2_write: 60.0
    batch_size: 10
    l1_ttl_seconds: 86400
    l1_max_bytes: 104857600  # 100MB
    retry:
      max_attempts: 5
      backoff_seconds: [10, 30, 90, 300]
    reconciliation:
      interval_seconds: 1800  # 30 minutes

    # Pinned verification keys (required in production; see "Pinned
    # Verification Keys" above). Without these the loader has no authorized
    # key_id and every signed config fails closed.
    verification_keys:
      - key_id: "admin-key-1"
        algorithm: "ed25519"
        public_key_b64: "<base64-encoded 32-byte Ed25519 public key>"

    # Protected configuration source
    protected_config:
      path: "/path/to/protected/hindsight-config.json"
      signature:
        path: "/path/to/protected/hindsight-config.sig"
        key_id: "admin-key-1"
```

### Hindsight Credentials

Store Hindsight credentials in the profile secret mechanism:

```yaml
profiles:
  default:
    secrets:
      hindsight:
        api_key: "your-hindsight-api-key"
```

## Testing

### Run Tests

Create an isolated environment and install the declared test extra:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[test]'
```

The reproducible baseline command is the same locally and in CI:

```bash
python -m pytest -q
```

This collects `tests/unit` and `tests/contract` via `pyproject.toml`; it must fail if collection or any test fails. For focused runs:

```bash
python -m pytest tests/unit -v
python -m pytest tests/contract -v
```

### Lint and Type Check

```bash
ruff check hermes_tiered_memory tests/
mypy hermes_tiered_memory
```

## Development

### Project Structure

```
hermes_tiered_memory/
├── __init__.py           # Provider registration
├── config/
│   └── __init__.py       # Protected config validation
├── adapters/
│   ├── l1.py            # L1 adapter interface
│   ├── l2.py            # L2 adapter interface
│   ├── mock_l1.py       # Mock L1 for testing
│   ├── mock_l2.py       # Mock L2 for testing
│   └── sqlite_l1.py     # Concrete SQLite L1 adapter
├── outbox/
│   ├── store.py          # Durable outbox storage
│   └── worker.py         # Promotion worker
└── providers/
    └── composite.py     # Main composite provider

scripts/
└── generate_protected_config.py  # Provisioning helper (genkey + sign)

tests/
├── unit/                # Unit tests
│   ├── test_composite_provider.py
│   ├── test_config.py
│   ├── test_outbox_store.py
│   ├── test_provider_lifecycle.py
│   ├── test_sqlite_l1.py
│   └── test_worker.py
└── contract/            # Contract tests
    └── test_contracts.py
```

### Contracts

This implementation follows the contracts defined in:

- `issues/01-composite-provider-boundary.md` — One-provider boundary
- `issues/02-read-write-routing-policy.md` — Read/write semantics
- `issues/03-promotion-policy.md` — Promotion eligibility
- `issues/04-identity-dedup-conflicts.md` — Stable identity
- `issues/05-metadata-roundtrip.md` — Metadata mapping
- `issues/06-failure-recovery-observability.md` — Failure recovery
- `issues/07-hermes-integration-config.md` — Configuration contract
- `research/outbox-crash-recovery-contract.md` — Outbox durability
- `research/hindsight-native-semantics-proof.md` — Hindsight API
- `research/live-adapter-contract.md` — Adapter interface

## Safety Boundary

The package is a loadable pinned-Hermes provider using disposable local adapters. It
fails closed after startup validation or adapter initialization fails: the retained
provider is disabled and removed from Hermes' provider and tool-routing state, so
Hermes cannot expose or dispatch its tools. Protected configuration, when supplied,
must be valid, signed, and fresh. The prototype's adapter/outbox behavior is not a
claim of live Hermes service compatibility.

## Limitations

- **No live integration claims** — Compatibility requires real pinned-service tests
- **No security evidence** — TLS trust and OpenAPI route verification remain unverified
- **No deployment artifacts** — Packaging and signing are follow-up work

## Release Evidence

The release bundle must retain:
- Protected source identity/schema
- Ownership and permission result
- Integrity/authentication result
- Key-rotation/freshness decision
- Pinned OpenAPI artifact record
- IANA IPv4/IPv6 special registry records
- Exact checked-in contract manifest (full commit SHA + SHA-256 digests)

## License

MIT

## Author

Jay Stothard <jtstothard@gmail.com>