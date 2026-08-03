# Developer Documentation

This document provides developers with guidance for working on the Hermes Tiered Memory composite provider.

## Project Structure

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

## Key Design Decisions

### 1. One-Provider Boundary

The composite presents as a single pinned-Hermes `MemoryProvider` and registers through the plugin loader. Its current integration uses disposable SQLite L1 and mock L2 adapters; Mnemosyne and Hindsight adapters remain internal implementation details and do not register their own Hermes tools.

### 2. Cache-First, Write-Through Policy

- **Reads**: Query L1 first (cache-first). On miss, query L2 and backfill L1.
- **Writes**: Commit to L1 synchronously. Enqueue L2 promotion asynchronously.

### 3. Durable Outbox

Promotion to L2 goes through a durable SQLite outbox:
- Each item is tracked with state, version, and fences for CAS protection
- Supports bounded retry with exponential backoff
- Quarantines permanent failures
- Survives process restarts

### 4. Identity and Isolation

- **Stable identity**: `(profile, bank, mnemosyne_id)` → stable document ID
- **Profile isolation**: Separate outbox per profile
- **Bank isolation**: Profile identity used as bank ID for Hindsight

### 5. Metadata Provenance

All promoted memories include a provenance envelope:
```python
{
    "origin": "mnemosyne",
    "profile": "<profile>",
    "bank": "<bank>",
    "mnemosyne_id": "<id>",
    "mnemosyne_revision": <revision>,
    ...
}
```

## Testing

### Install test dependencies

The test suite requires `pytest-cov` (declared in the `[test]` extra):

```bash
python -m pip install -e '.[test]'
```

### Run the full suite (canonical)

```bash
python -m pytest -q
```

### Run a subset

```bash
python -m pytest tests/unit -v          # unit tests only
python -m pytest tests/contract -v       # contract tests only
python -m pytest tests/unit/test_outbox_store.py::TestOutboxStoreBasics::test_initialization_creates_schema -v
```

CI (`.github/workflows/test.yml`) runs Ruff, mypy, Black's formatting check, and `python -m pytest -q` across Python 3.11–3.13. The loader-discovery job separately proves installation through the pinned real Hermes loader and rejects secrets or local databases in the wheel. Pull requests and pushes also run a full-history Gitleaks scan.

The release workflow (`.github/workflows/release.yml`) publishes `v*` tags to PyPI through trusted publishing (OIDC; no PyPI token) and attaches distributions plus `SHA256SUMS` to the GitHub Release. Live end-to-end topology tests remain operator-only.

## Code Quality

### Lint

```bash
ruff check hermes_tiered_memory tests/
```

### Type Check

```bash
mypy hermes_tiered_memory
```

### Format

```bash
black --check hermes_tiered_memory tests
```

## Adding a New Feature

1. **Implement the feature** in the appropriate module
2. **Add unit tests** in `tests/unit/`
3. **Add contract tests** if the feature relates to a contract
4. **Update documentation** if it affects public APIs
5. **Run full test suite** and ensure all tests pass

## Debugging

### Enable Debug Logging

```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

### Inspect Outbox State

```python
stats = provider.outbox_store.get_stats()
print(stats)

# Get pending items
items = provider.outbox_store.claim_batch(batch_size=10, ...)
```

### Check Health Status

```python
health = provider.health()
print(health)
```

## Common Patterns

### Adding a New Outbox State

1. Add state to `OutboxState` enum in `outbox/store.py`
2. Update state transition logic in `outbox/worker.py`
3. Add tests for state transitions

### Adding a New Metadata Field

1. Add field to `_build_promotion_metadata()` in `providers/composite.py`
2. Add test for metadata preservation in `tests/contract/test_contracts.py`

### Handling Errors

Use the error classification in `outbox/worker.py`:
```python
error_class = self._classify_error(error)
is_transient = error_class in ("transient", "rate_limit")
```

## Known Limitations

1. **No live integration tests** - Uses mock adapters only
2. **No real Mnemosyne/Hindsight adapters** - Interface definitions only
3. **No security evidence** - TLS trust and OpenAPI verification unverified
4. **No deployment artifacts** - Packaging and signing are follow-up work

## Safety Guarantees

The following guarantees apply **only** to the concrete SQLite L1 adapter with the attached-outbox path (`SQLiteL1Adapter.remember_with_outbox`). Injected or mock adapters retain the historical non-atomic two-step behavior.

1. **Never lose a promotion (SQLite path only)** - Atomic L1+outbox commit
2. **Never block the queue** - Quarantine halts only failing documents
3. **Never silently drop (SQLite path only)** - Every unresolved row durably represented
4. **Never trust teardown for durability** - Outbox written at write time
5. **Never retry permanent errors indefinitely** - Quarantine on permanent failure

The non-SQLite path (mock/injected adapters) writes L1 first, then enqueues outbox separately; an enqueue failure after a successful L1 write is swallowed, so promotion is not guaranteed.

## Follow-Up Work

The following are explicitly out of scope for this task:

- **Live integration tests** with pinned Hermes/Mnemosyne/Hindsight services
- **Real Mnemosyne adapter** implementing the L1 interface
- **Real Hindsight adapter** implementing the L2 interface
- **Packaging and signing** for distribution
- **Security evidence** (TLS trust, OpenAPI route verification)
- **Staged rollout/rollback rehearsal**

These should be tracked as follow-up tasks.