# Hermes Tiered Memory — Composite Provider Prototype

A fail-closed composite-provider prototype for a future Hermes integration. It is not a production Hermes memory provider and does not claim live Mnemosyne or Hindsight compatibility.

## Scope and Status

This package contains the current composite-provider prototype, deterministic unit/contract tests, and developer documentation. It does not yet provide a usable pinned-Hermes `MemoryProvider` implementation or live adapter integration.

**What is included:**
- Composite-provider prototype with deterministic mock-adapter tests
- Durable outbox tombstones and ordered delete processing
- Protected endpoint configuration validation and fail-closed startup checks
- Fail-closed registration when the pinned Hermes API boundary is unavailable
- No bundled dependencies or Hermes core changes

**What is explicitly NOT included:**
- A usable Hermes `MemoryProvider` implementation or live registration
- Live Mnemosyne/Hindsight adapter routes or compatibility evidence
- Outbox crash replay: abandoned `in_flight` rows are not reclaimed (design in research/outbox-crash-recovery-contract.md)
- Live integration tests with pinned Hermes/Mnemosyne/Hindsight services
- Packaging and signing
- Security evidence (TLS trust, OpenAPI route verification)
- Staged rollout/rollback rehearsal

## Architecture

```
Hermes
  └── CompositeProvider (future Hermes-facing boundary; not currently loadable)
        ├── Mnemosyne L1 adapter (synchronous reads/writes)
        ├── Hindsight L2 adapter (reads + retain/batch-retain)
        └── Promotion behavior (prototype only; no durability claim)
```

The composite is designed to own the sole Hermes-facing memory tool namespace (`memory_search`, `memory_store`) once live integration is implemented. Mnemosyne and Hindsight adapters expose no duplicate Hermes tools.

## Future integration shape (not currently executable)

The following describes the intended future integration shape only. The current
package deliberately fails closed because `CompositeProvider` does not yet
implement the pinned Hermes `MemoryProvider` ABC.

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

Compute HMAC-SHA-256 as `HMAC(secret, DOMAIN_SEPARATOR + exact_JCS_bytes)` (or Ed25519 over the same domain-separated bytes), with domain separator `hermes-tiered-memory/config/v1\\0`. The file bytes must be exactly canonical JCS JSON; whitespace or alternate JSON spellings are rejected.

Store the signature in a separate file (or secret-store object).

### Hermes Configuration

If and when the pinned Hermes provider boundary is implemented and verified,
the intended configuration shape would be:

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

The package fails closed rather than registering the current prototype as a
Hermes provider. Protected configuration is required at startup and must be
valid, signed, and fresh. The prototype's adapter/outbox behavior is not a
claim of live Hermes or service compatibility.

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