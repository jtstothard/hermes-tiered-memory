# Installing hermes-tiered-memory

This guide installs the package and makes it discoverable as a Hermes
memory plugin.

## Requirements

- Python `>=3.11,<3.14`
- `hermes-agent>=0.7.0` in the same Python environment
- A Hermes home directory that already exists

For live L1/L2 operation, install both optional adapter extras:

```bash
python3 -m pip install 'hermes-tiered-memory[mnemosyne,hindsight]'
```

## Install from a wheel

From a source checkout, build a wheel and run the installer with the Python
interpreter used by Hermes:

```bash
python3 -m pip install build
python3 -m build --wheel
PYTHON=python3 scripts/install_plugin.sh \
  dist/hermes_tiered_memory-1.0.2-py3-none-any.whl "$HERMES_HOME"
```

The installer pip-installs the wheel, verifies the import, creates
`$HERMES_HOME/plugins/tiered-memory/`, and writes its discovery shim and
`plugin.yaml`. Re-running it upgrades the installed package and rewrites the
shim.

Set this in the selected profile's configuration and restart Hermes:

```yaml
memory:
  provider: tiered-memory
```

This package exposes the three public tools `tiered_memory_search`,
`tiered_memory_store`, and `tiered_memory_delete`. It does not provide a native
`hermes tiered-memory` command.

## Configure and verify

Use the standalone console script to create and validate protected endpoint
configuration:

```bash
hermes-tiered-memory provision --help
hermes-tiered-memory doctor --help
```

Keep generated private keys, signed configuration, and detached signatures
mode `0600`. Put Hindsight credentials only in the active Hermes profile's
secret configuration. See [README.md](README.md) and
[docs/security.md](docs/security.md).

## Troubleshooting

- If the plugin is not discovered, check that
  `$HERMES_HOME/plugins/tiered-memory/__init__.py` exists and that the Hermes
  process uses the same `HERMES_HOME`.
- If import fails, install the wheel into the exact Python environment used by
  Hermes and run `python3 -c 'import hermes_tiered_memory'`.
- If startup fails closed, run `hermes-tiered-memory doctor` with the protected
  config, detached signature, and pinned public key. Check `auth_mode`, the
  active profile secret boundary, file permissions, and optional extras.
