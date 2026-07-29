# Issue #8 research: package install, compatibility, and release artifact contract

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`

## Scope and evidence policy

This is a HITL `wayfinder:grilling` resolution. The decisions below were explicitly selected by Jay. No production service, estate data, or credentials were used. This defines the v1 packaging contract; implementation and CI evidence remain follow-on work.

This ticket incorporates:
- Issue #3 — the composite must load as exactly one Hermes memory provider.
- Issue #6 — publish evidence must include deterministic health/CLI and content-free operational surfaces.
- Issue #7 — pinned, measured compatibility and performance assumptions.
- Issue #9 — reproducible release, SBOM, lockfile, license, secret, and artifact-integrity requirements.

## Decision

Version 1 will ship as a **private GitHub release containing an installable pinned wheel and source distribution**. PyPI publication is deferred. Installation and upgrades are explicit, pinned, and restart-based; unsupported environments fail closed rather than continuing on a best-effort basis.

## 1. Distribution target

- Canonical source remains the private GitHub repository.
- Each candidate release is a protected, signed tag with a private GitHub release.
- The release contains a wheel and sdist, checksums, SBOM, lockfile/export, compatibility metadata, and installation evidence.
- PyPI and other public package indexes are out of v1 scope. Enabling public publication is a later decision and also crosses the security review trigger defined by issue #9.

## 2. Install and update path

### Install

Install the release wheel into an isolated environment using the published checksum and lockfile. The installation must not require a live Hermes home, production configuration, network access at import time, or credentials.

The package must expose the documented Hermes plugin entry point and register exactly one composite provider. A fresh-environment install/import test is mandatory for every release.

### Update

Update by installing the replacement pinned wheel into the controlled environment, then restarting Hermes. Do not mutate a running provider in place. The release record must identify the old and new versions, upstream pins, migration/configuration notes, and the restart verification result.

Rollback is the inverse: reinstall the previous verified wheel and restart. Rollback rehearsal belongs to issue #10, but the package must preserve version-identifiable artifacts so that rehearsal is deterministic.

## 3. Compatibility matrix

The initial supported Python range is **3.11–3.13**, constrained by the pinned Hermes support boundary. The release matrix must test each supported interpreter against the pinned versions:

- Hermes Agent `v2026.7.20` / commit `3ef6bbd`;
- Mnemosyne `v3.14.0` / commit `4e5ed14`;
- Hindsight `v0.8.6` / commit `08995e3`.

Floating `latest` dependencies are not supported. A dependency or upstream version change requires a new compatibility run and release record.

The matrix must cover:

- clean wheel install;
- sdist build/install;
- import and plugin discovery;
- exactly-one-provider enforcement;
- isolated adapter contract tests;
- outbox/restart tests;
- health/CLI surface;
- uninstall/reinstall and rollback path.

## 4. Fail-closed checks

The package and loader must fail closed before provider operation when any of these conditions is detected:

- unsupported Python version;
- unsupported or mismatched pinned upstream version;
- missing or invalid required configuration;
- invalid endpoint/TLS/authentication settings;
- duplicate composite-provider registration;
- package metadata or entry-point mismatch;
- missing required outbox/storage permissions;
- integrity or checksum mismatch during controlled installation.

Failure must identify the configuration category and remediation without exposing credentials, memory content, or full secret-bearing URLs. Best-effort continuation is not acceptable for a memory provider that could create split-brain or cross-profile behavior.

## 5. Release artifact bundle

Every release must include:

1. Wheel (`.whl`) and source distribution (`.tar.gz`).
2. SHA-256 checksums for each artifact.
3. Dependency lockfile/export with hashes for the supported matrix.
4. CycloneDX SBOM covering distributed runtime dependencies and their versions, hashes, licenses, and relationships.
5. License/notice inventory and the composite's MIT `LICENSE`/SPDX metadata, subject to issue #9's review.
6. Reproducible clean-install evidence for wheel and sdist.
7. Package file-list inspection proving no `.env`, database/WAL, logs, real memory fixtures, local paths, or credentials are included.
8. Test results for the compatibility and isolation matrix.
9. A signed, protected tag and release provenance/checksum record.
10. Versioned release notes naming upstream pins, supported Python versions, configuration changes, restart requirements, and rollback target.

The release itself is not complete if it has only a source archive or an unverified wheel.

## 6. Required CI/local evidence before publish readiness

CI is authoritative and local runs are advisory. The pipeline must build from a clean pinned checkout and verify:

- wheel and sdist build successfully;
- artifact contents are safe;
- checksums and SBOM are generated;
- fresh environments install using hashes;
- Python 3.11, 3.12, and 3.13 pass the compatibility suite;
- provider discovery registers exactly one provider;
- unsupported/missing/duplicate configurations fail closed;
- tests cover restart, outbox, health/CLI, and rollback-compatible version identity;
- security/license/secret scans from issue #9 pass or have explicit dispositions.

## Safety invariants

1. No public package publication in v1.
2. No floating dependency resolution in a release.
3. No install-time network call or credential requirement.
4. No best-effort operation after a compatibility or identity mismatch.
5. Every release has a verified previous artifact for rollback.
6. Package contents are inspectable, reproducible, and free of secrets and real memory data.

## Newly visible successor questions

1. What exact package name and Python build backend will be used?
2. Which Hindsight client/workspace package is the final runtime dependency?
3. Which lockfile/build tool will provide cross-platform hashes?
4. What private GitHub release access policy and signing workflow will protect artifacts?
5. What exact migration/configuration compatibility policy applies between wheel versions?
6. Which release evidence artifact format will issue #10 consume for staged rollout and rollback rehearsal?
7. What decision and evidence are required before moving from private GitHub release to PyPI?

## Concise recommendation for issue #8

Publish a private, signed GitHub release containing a pinned wheel and sdist, checksums, SBOM, lockfile, install evidence, and compatibility results for Python 3.11–3.13 against the pinned Hermes/Mnemosyne/Hindsight versions. Install by pinned wheel replacement and restart. Fail closed on unsupported versions, missing configuration, or duplicate providers. Defer PyPI until a separate publication and full-audit decision.
