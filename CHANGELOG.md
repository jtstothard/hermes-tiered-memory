# Changelog

All notable public changes are recorded here.

## [1.0.1] — 2026-08-03

### Added
- `LICENSE` file (MIT) — was declared in metadata but missing from repo root
- `SECURITY.md` — security policy and vulnerability reporting process
- `CONTRIBUTING.md` — development workflow and contribution guidelines
- `.github/FUNDING.yml` — Ko-fi sponsorship link (`trshpotato`)
- `.github/dependabot.yml` — weekly dependency and GitHub Actions updates
- `.github/PULL_REQUEST_TEMPLATE.md` and issue templates (bug/feature)
- `py.typed` marker file (PEP 561) — ships in the wheel for type-checker support

### Changed
- Version bumped to 1.0.1

## [1.0.0] — 2026-08-03

### Added

- Single composite Hermes memory provider with public tools
  `tiered_memory_search`, `tiered_memory_store`, and `tiered_memory_delete`.
- Mnemosyne L1, Hindsight L2, profile/bank scoping, provenance metadata, and a
  durable promotion outbox.
- Protected endpoint configuration with canonical JSON validation, detached
  Ed25519/HMAC-SHA-256 authentication, pinned verification keys, freshness, and
  rollback checks.
- `hermes-tiered-memory provision` for signed configuration/key material and a
  reviewable Hermes patch.
- `hermes-tiered-memory doctor` for read-only dependency, configuration, L1,
  and optional L2 health checks.
- Python 3.11–3.13 package metadata, optional Mnemosyne/Hindsight extras, CI
  quality gates, wheel discovery checks, Gitleaks scanning, and trusted PyPI
  publishing workflow.

### Compatibility

- Requires Python `>=3.11,<3.14` and `hermes-agent>=0.7.0`.
- The supported plugin installation path is the package plus the generated
  `tiered-memory` Hermes plugin shim. This release does not provide a native
  `hermes tiered-memory` command.

See [v1.0.0 release notes](docs/release-notes-v1.0.0.md) and
[compatibility/migration notes](docs/ci-release.md).
