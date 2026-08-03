# Migration to v1.0.0

This release replaces the pre-v1 prototype documentation and configuration
shape with the public v1 composite provider.

## Runtime and install

Use Python `>=3.11,<3.14`, `hermes-agent>=0.7.0`, and install the optional
`mnemosyne` and `hindsight` extras for live operation:

```bash
python3 -m pip install 'hermes-tiered-memory[mnemosyne,hindsight]'
```

Install the package into the same environment as Hermes and use
`scripts/install_plugin.sh` to create the `tiered-memory` plugin shim. Then set
`memory.provider: tiered-memory` and restart Hermes. Do not configure separate
Hermes providers for the two adapters.

## Configuration changes

Add `memory.tiered.hindsight.auth_mode` with exactly `api_key` or `none`:

- `api_key` requires the active profile's non-empty Hermes secret
  `profiles.<active-profile>.secrets.hindsight.api_key`.
- `none` requires that the active profile has no Hindsight API key.

Add pinned verification keys and a signed protected endpoint document. The
recommended path is:

```bash
hermes-tiered-memory provision --help
hermes-tiered-memory doctor --help
```

Review the generated YAML patch before applying it. Never copy a credential into
the patch, protected JSON, signature, or source repository. The commands are
console-script commands; `hermes tiered-memory` is not provided.

## Data and rollout

Installing v1.0.0 does not migrate or rewrite existing local data. Back up
profile state, stop Hermes while switching provider configuration, restart it,
and run `doctor` before enabling writes. Confirm the active profile and bank
scope: the provider intentionally does not use ambient Mnemosyne defaults.

The provider presents one namespace—`tiered_memory_search`,
`tiered_memory_store`, and `tiered_memory_delete`—and keeps adapter details
internal. Existing callers should use those public names rather than relying on
adapter-specific tools.

For authentication, protected files, signatures, and rollback protection, see
[Security](security.md). For CI/release compatibility, see
[CI, release, and compatibility](ci-release.md).
