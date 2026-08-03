# Hermes Tiered Memory v1.0.0

## What ships

v1.0.0 is the first public release of the Hermes Agent composite memory
provider. It presents one provider and three public tools:

- `tiered_memory_search`
- `tiered_memory_store`
- `tiered_memory_delete`

The implementation routes through profile/bank-scoped Mnemosyne L1 and
Hindsight L2 adapters. L1 writes are committed before asynchronous L2
promotion through the durable SQLite outbox. Protected endpoint configuration,
pinned verification keys, freshness checks, and fail-closed startup behavior
are included.

## Install and configure

Use Python 3.11, 3.12, or 3.13 with `hermes-agent>=0.7.0`:

```bash
python3 -m pip install 'hermes-tiered-memory[mnemosyne,hindsight]'
```

For a wheel-based Hermes plugin installation, run `scripts/install_plugin.sh`
from the source checkout, set `memory.provider: tiered-memory`, and restart
Hermes. Use the public standalone console commands:

```bash
hermes-tiered-memory provision --help
hermes-tiered-memory doctor --help
```

The Hindsight authentication policy is configured as
`memory.tiered.hindsight.auth_mode: api_key` or `none`. API keys remain in the
active profile's Hermes secret configuration; they are never part of the
signed endpoint document or provisioning patch.

## Verification

The release CI matrix covers Python 3.11–3.13 and runs lint, type, formatting,
and the full pytest suite. It also verifies real Hermes loader discovery,
scans wheel contents for secret/database artifacts, and runs a full-history
Gitleaks scan. Tag-triggered release automation builds wheel and sdist,
produces SHA256 checksums, publishes through PyPI trusted publishing, and
attaches assets to the GitHub Release.

## Boundaries

The `research/`, `issues/`, `map.md`, and `DEVELOPER.md` files are internal
non-user design history. Public behavior and migration guidance are maintained
in [README.md](../README.md), [security.md](security.md), and
[ci-release.md](ci-release.md).
