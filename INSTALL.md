# Installing hermes-tiered-memory

This guide walks through building the wheel and installing the plugin so the
real Hermes memory-provider loader discovers and imports it.

## Prerequisites

- Python 3.11–3.13 (the Hermes runtime Python)
- The Hermes agent installed and `HERMES_HOME` known (typically `~/.hermes`)

## 1. Build the wheel

```bash
cd hermes-tiered-memory
pip install build          # one-time, if not already present
python -m build --wheel
```

This produces `dist/hermes_tiered_memory-0.1.0-py3-none-any.whl`.

## 2. Run the install script

```bash
scripts/install_plugin.sh dist/hermes_tiered_memory-0.1.0-py3-none-any.whl ~/.hermes
```

The script:

- `pip install`s the wheel into the current Python environment.
- Creates `~/.hermes/plugins/tiered-memory/__init__.py` as a thin shim that re-exports `register` and `register_memory_provider` from the installed package.
- Writes `~/.hermes/plugins/tiered-memory/plugin.yaml`.

It is **idempotent** — re-running upgrades cleanly (just overwrite).

**Fail-closed:** the script aborts if `HERMES_HOME` is unset, the wheel is missing, or `pip install` fails.

## 3. Activate the provider

In your Hermes profile's `config.yaml`:

```yaml
memory:
  provider: tiered-memory
```

The value must match the plugin directory name (`tiered-memory`).

## 4. Restart Hermes

```bash
hermes restart   # or however you restart your Hermes instance
```

The loader scans `$HERMES_HOME/plugins/` at startup. It will find `tiered-memory`, import the shim, and the shim re-exports the real `register(ctx)` from the pip-installed package.

## How discovery works

The Hermes loader (`plugins/memory/__init__.py` in the hermes-agent repo) does a cheap text scan of each plugin's `__init__.py` for the literal strings `register_memory_provider` or `MemoryProvider`. If neither is present, the directory is invisible. Our shim contains both.

On load, the loader calls `register(ctx)` (or the alias `register_memory_provider(ctx)`), passing a simulated context. The provider constructs and validates itself before registering.

## Verifying the install

```bash
python3 -c "
from plugins.memory import list_memory_provider_names, find_provider_dir
assert 'tiered-memory' in list_memory_provider_names()
d = find_provider_dir('tiered-memory')
assert d and d.is_dir()
print('Discovered:', d)
"
```

## Troubleshooting

- **Plugin not discovered:** confirm `$HERMES_HOME/plugins/tiered-memory/__init__.py` exists and contains the string `register_memory_provider` or `MemoryProvider`.
- **Import fails:** ensure the wheel was installed into the *same* Python environment Hermes runs in (`python3 -c "import hermes_tiered_memory"`).
- **`is_available()` returns False:** this is expected when live adapter dependencies (Mnemosyne, Hindsight client) are not installed. Install them with `pip install '.[mnemosyne,hindsight]'` if you want live L1/L2.
