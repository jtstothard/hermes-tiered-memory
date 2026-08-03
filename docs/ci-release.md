# CI, release, and compatibility

## Supported runtime

v1.0.0 declares and tests Python `>=3.11,<3.14` (3.11, 3.12, and 3.13) and
requires `hermes-agent>=0.7.0`. The live adapter extras are optional package
extras:

- `mnemosyne` installs `mnemosyne-memory>=3.14.0,<4.0.0`.
- `hindsight` installs `hindsight-client>=0.8.6,<1.0.0`.

The base package still contains the provider and its validation/configuration
code, but live L1/L2 operation requires the relevant extras. Install both
extras for the supported live setup.

## Continuous integration

`.github/workflows/test.yml` runs on pushes and pull requests. Its baseline
matrix covers Python 3.11–3.13 and runs:

- Ruff
- mypy
- Black's check mode
- `python -m pytest -q`

The loader-discovery job builds a wheel, installs the plugin shim into a
throwaway Hermes home, checks that the pinned real Hermes loader discovers and
loads `tiered-memory`, and rejects secret/local-database files in the wheel.
The Gitleaks job scans full repository history. Live service topology tests are
not silently represented as ordinary CI tests.

## Release workflow

`.github/workflows/release.yml` runs for tags matching `v*`. It:

1. builds the wheel and source distribution with Python 3.13;
2. writes `dist/SHA256SUMS`;
3. publishes both distributions to PyPI with the PyPA trusted-publishing OIDC
   action and no long-lived PyPI token; and
4. attaches the distributions and checksums to the GitHub Release.

Release publication requires the repository's configured PyPI environment and
GitHub permissions. A tag-triggered workflow is not a substitute for reviewing
configuration, credentials, and generated release assets.

## Migration notes

From pre-v1 prototype documentation/configuration:

- Replace claims that the package is only a mock or disposable prototype with
  the v1 composite-provider behavior documented in the README. The supported
  runtime uses the live Mnemosyne and Hindsight adapters.
- Install the optional `mnemosyne` and `hindsight` extras. They are not
  bundled into the base dependency list.
- Use `memory.provider: tiered-memory` after installing the generated plugin
  shim. Do not configure separate Hermes providers for L1 and L2.
- Add `memory.tiered.hindsight.auth_mode` with exactly `api_key` or `none`.
  `api_key` reads only the active profile's Hermes-owned Hindsight secret;
  `none` rejects a present secret.
- Replace any environment-variable or test-only key registration approach with
  pinned `memory.tiered.verification_keys` and the signed protected config.
- Run `hermes-tiered-memory provision` to create new signed configuration
  material, then `hermes-tiered-memory doctor` to validate it without mutating
  the live replay state.
- The standalone commands are console-script commands. There is no native
  `hermes tiered-memory` command in this release.

Existing local data is not rewritten by package installation. Back up profile
state before changing provider configuration, stop/restart Hermes during the
provider switch, and verify the active profile/bank mapping before enabling
writes. Do not copy secrets into generated patches or protected endpoint JSON.
