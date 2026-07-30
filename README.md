# Hermes Tiered Memory — Production Composite Provider

A production composite Hermes memory provider that combines Mnemosyne L1 (local SQLite hot cache) with Hindsight L2 (durable PostgreSQL/pgvector memory).

## Scope and Status

This implementation provides the production composite Hermes memory provider, deterministic unit/contract tests, and developer documentation. It implements the contracts defined in `issues/` and `research/`.

**What is included:**
- One Hermes-facing provider/tool namespace
- Mnemosyne L1 synchronous writes and cache-first reads
- Hindsight L2 retain/retain_batch_async promotion (no reflect dependency)
- Durable local outbox with operation_id/idempotency, per-item status/checkpoint, bounded retry, quarantine/replay hooks, crash-safe recovery
- L2 miss fallback and backfill
- Profile/bank isolation
- Metadata provenance envelope
- Tombstone/delete semantics
- Configurable adapters and retention
- No bundled dependencies or Hermes core changes

**What is explicitly NOT included (follow-up gaps):**
- Live integration tests with pinned Hermes/Mnemosyne/Hindsight services
- Packaging and signing
- Security evidence (TLS trust, OpenAPI route verification)
- Staged rollout/rollback rehearsal

## Architecture

```
Hermes
  └── CompositeProvider (one configured MemoryProvider)
        ├── Mnemosyne L1 adapter (synchronous reads/writes)
        ├── Hindsight L2 adapter (reads + retain/batch-retain)
        └── Durable outbox + promotion worker
```

The composite owns the sole Hermes-facing memory tool namespace (`memory_search`, `memory_store`). Mnemosyne and Hindsight adapters expose no duplicate Hermes tools.

## Installation

### Prerequisites

- Python 3.11, 3.12, or 3.13
- Hermes Agent v2026.7.20 (commit `3ef6bbd`)
- Mnemosyne v3.14.0 (commit `4e5ed14`)
- Hindsight v0.8.6 (commit `08995e3`)
- Protected Hindsight endpoint configuration (see Configuration)

### Install

```bash
pip install hermes-tiered-memory[mnemosyne,hindsight]
```

For development:

```bash
pip install hermes-tiered-memory[dev]
```

## Configuration

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

Compute the HMAC-SHA-256 or Ed25519 signature over the exact JCS bytes with domain separator `hermes-tiered-memory/config/v1\0`.

Store the signature in a separate file (or secret-store object).

### Hermes Configuration

Configure Hermes with the composite provider:

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

```bash
# Unit tests (no external services)
python -m pytest tests/unit -v

# Contract tests (deterministic, no live services)
python -m pytest tests/contract -v

# Full test suite
python -m pytest tests/ -v --cov=hermes_tiered_memory --cov-report=html
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
│   ├── __init__.py
│   └── protected.py      # Protected config validation
├── adapters/
│   ├── __init__.py
│   ├── l1.py            # Mnemosyne L1 adapter
│   └── l2.py            # Hindsight L2 adapter
├── outbox/
│   ├── __init__.py
│   ├── store.py         # Durable outbox storage
│   └── worker.py        # Promotion worker
└── providers/
    └── composite.py     # Main Hermes-facing provider

tests/
├── unit/                # Unit tests
└── contract/            # Contract tests (deterministic)
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

## Safety Guarantees

1. **Never lose a promotion** — Atomic L1+outbox commit
2. **Never block the queue** — Quarantine halts only failing documents
3. **Never silently drop** — Every unresolved row durably represented
4. **Never trust teardown for durability** — Outbox written at write time
5. **Never retry permanent errors indefinitely** — Quarantine on permanent failure

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