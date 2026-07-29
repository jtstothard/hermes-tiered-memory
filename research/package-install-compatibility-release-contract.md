# Issue #8 research: package install, compatibility, and release artifact contract

Date: 2026-07-29
Repository: `jtstothard/hermes-tiered-memory`

## Scope and evidence policy

This is a HITL decision report, not an implementation or a release record. No production service, estate data, credentials, GitHub release, package publication, or Hermes process was used. The contract below is intentionally conditional: a release is not publish-ready merely because this document exists. A claim is a **PASS** only when the named artifact contains the observed result, command/tool version, source revision, interpreter/platform, and relevant hashes. `TBD`, `not run`, or “works locally” is not a pass.

The contract incorporates the decisions and boundaries from issues #3, #6, #7, and #9, including the pinned Hermes loader behavior inspected in [`research/plugin-lifecycle-one-provider-proof.md`](plugin-lifecycle-one-provider-proof.md).

## Decision

Version 1 targets a **private GitHub release** containing a pinned wheel and sdist. PyPI is deferred. The distribution artifact is not itself a Hermes plugin discovery artifact: the install/bootstrap procedure must explicitly place the plugin directory under the selected profile's `$HERMES_HOME/plugins/<name>/`, then configure `memory.provider` to that name. Updates are staged and restart-based. Unsupported environments, identity mismatches, incomplete verification, and duplicate-provider conditions fail closed before memory operation.

Python 3.11, 3.12, and 3.13 are **conditional support claims**, not current evidence. Each interpreter must pass every mandatory release gate on the declared CI platform(s), with the exact pinned dependency resolution. If any one is not tested and passing, the release must narrow its metadata and release notes to the passing range; it must not claim 3.11–3.13 support.

## 1. Names and unresolved release inputs

The following values are deliberately not invented by this report. They are release blockers until recorded in `release-manifest.json` (or an equivalent signed manifest):

- final Python distribution/project name;
- Hermes plugin directory name (`<name>`), which must be the value configured as `memory.provider`;
- build backend and its exact version;
- lockfile/resolution tool and exact version;
- selected Hindsight client/workspace package and exact version/commit, including its transitive runtime tree;
- supported OS/architecture tags and whether the sdist is expected to build on each;
- private GitHub repository/release policy, artifact signing identity, and verification key distribution;
- migration policy and the first release's data/config schema version.

The pinned upstream compatibility inputs already selected are:

- Hermes Agent `v2026.7.20`, commit `3ef6bbd201263d354fd83ec55b3c306ded2eb72a`;
- Mnemosyne `v3.14.0`, commit `4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce`;
- Hindsight `v0.8.6`, commit `08995e3`; this does **not** identify the Python client package.

No artifact may use `latest`, an unrecorded workspace member, or an unhashable floating dependency. Resolving the Hindsight client and final package names is a prerequisite, not a documentation detail.

## 2. Exact install and Hermes discovery contract

### 2.1 Required artifact layout

The wheel must contain the plugin source under a deterministic, declared package subdirectory and expose the selected distribution metadata. The wheel's `RECORD` and file-list evidence must identify the files that bootstrap copies into the Hermes plugin directory. The sdist must contain the same plugin source and build metadata; it must not contain local databases, logs, `.env` files, credentials, real memory fixtures, or machine-specific paths.

A wheel installed into an arbitrary virtual environment is **not sufficient** for discovery. On the pinned Hermes loader, discovery checks the bundled plugin directory and then `$HERMES_HOME/plugins/<name>/`; it loads that directory and selects it from `memory.provider`. The release procedure therefore has two distinct steps:

1. **Dependency install:** install the exact wheel into the controlled Hermes Python environment using the release lock/export and artifact hashes, with no import-time network access or credentials.
2. **Plugin bootstrap:** copy or extract only the verified plugin directory from that wheel into the target profile's `$HERMES_HOME/plugins/<name>/` using a versioned staging directory. The bootstrap must reject path traversal, unexpected files, and an existing directory whose manifest/version does not match. It must never copy a wheel blindly over an active directory.

The bootstrap records: profile/HERMES_HOME identity (not secrets), distribution version, plugin name, source commit, wheel filename and SHA-256, sdist/build identity if applicable, file-list hash, timestamp, and the staged directory path. It must be rerunnable and must not contact a network. The operator then sets, in the selected profile configuration:

```yaml
memory:
  provider: <name>
```

The value must exactly equal the bootstrapped directory name. Credentials and endpoint settings remain in the supported profile secret mechanism/environment; they are not arguments to the installer or embedded in the release artifact.

### 2.2 Discovery and startup acceptance

The release harness must run the **real pinned Hermes loader**, not an imitation: use the pinned Hermes checkout/installed host and a disposable `$HERMES_HOME` containing only the staged plugin and synthetic configuration. Record the loader source revision and the exact invocation in evidence. The harness must prove, with observable output or a machine-readable assertion:

- the loader resolves `<name>` from `$HERMES_HOME/plugins/<name>/`;
- `register(ctx)` registers one composite provider;
- the composite source is an actual `MemoryProvider` implementation satisfying the pinned ABC;
- only the composite exposes Hermes memory tools; Mnemosyne and Hindsight adapters do not register providers or tools independently;
- initialization, health check, one synthetic operation, shutdown, and a fresh-process reload all succeed;
- no network request occurs during install/import/discovery (network calls are allowed only during explicitly tested adapter operations).

The source-level discovery heuristic documented in issue #3 is not enough. A source grep or successful Python import without the real loader is a **FAIL** for this gate.

### 2.3 Duplicate-provider behavior and Hermes limitation

The pinned Hermes `MemoryManager.add_provider()` rejects a second external provider by logging a warning and dropping it; it does not raise. Therefore the core loader cannot be described as fail-closed for duplicates. This release contract requires a fail-closed **bootstrap/launch wrapper** (or a validated equivalent integration guard) that, before starting Hermes, proves that exactly one external provider is configured and discoverable and that no competing external provider is being loaded. It must treat the pinned loader's duplicate-provider warning as an error, terminate before memory operation, and return a non-zero status with a redacted remediation message.

The guard must be tested with: one composite provider (pass), composite plus a second external plugin (fail before operation), missing plugin (fail), and a builtin-only configuration if unsupported by this product (fail with explicit remediation). If the wrapper cannot observe and enforce this boundary on the pinned host, the release is blocked; the report must not claim that Hermes itself fails closed. The composite must never call `register_memory_provider` for either adapter.

## 3. Compatibility and support gates

The release matrix is the Cartesian product of each claimed Python version, each declared platform tag, wheel installation, and sdist build/install. For every cell, run the real pinned-loader suite described above plus adapter contract, outbox/restart, health/CLI, package-content, and duplicate-provider tests.

Python 3.11–3.13 may appear in release metadata only if all three pass the complete matrix with the same recorded upstream pins and a resolved lock/export. Python 3.13 is currently **unverified**. A failure or missing cell narrows support to the passing versions and is recorded as a release decision; it is not waived as an upstream or local-environment issue.

A cell fails closed on:

- unsupported Python/platform or interpreter ABI;
- mismatch between recorded and installed upstream/package versions or hashes;
- missing/invalid configuration, endpoint, TLS, or authentication;
- missing plugin directory, metadata, entry-point/loader contract, or file-list mismatch;
- duplicate external provider;
- unavailable required outbox/storage path or unsafe permissions;
- artifact/signature/attestation verification failure.

Error evidence must identify the category and remediation while redacting credentials, memory content, and secret-bearing URLs.

## 4. Artifact integrity and private-release authenticity

SHA-256 checksums detect accidental corruption; they do not authenticate who created an asset. A signed Git tag authenticates the tag object, not necessarily the attached wheel or sdist. Consequently, every release must provide both:

1. a protected signed tag whose verified object is the exact source revision used to build; and
2. an asset-level signed manifest/attestation covering each wheel and sdist filename, size, SHA-256, source revision, build environment identity, lockfile hash, and build command/tool versions.

The project must choose and document a concrete trusted signing mechanism and verification key/fingerprint before the first release. Verification must be performed from a clean machine/profile using a pinned verifier or documented trusted GitHub/OIDC attestation service, with the trust root obtained out-of-band or from protected repository configuration. A checksum downloaded from the same untrusted release channel is not a trust root. Verification must fail on an unsigned, untrusted, missing, or hash-mismatched asset. Do not claim SLSA level, “signed release,” or trusted provenance until the selected mechanism's actual verification output is retained.

Private GitHub access must be explicit: release download/build inputs use a short-lived, least-privilege credential supplied by the protected CI identity or an operator's preconfigured GitHub authentication, never a URL-embedded token, package metadata, shell history, or committed config. CI must fail if the private repository/release is inaccessible rather than silently resolving from public indexes. Dependency acquisition must use the hash-locked export and configured private source credentials only where required; no credential is permitted at import or runtime discovery. Retain redacted authentication method, repository/release identifier, and download verification result, not tokens.

## 5. Build, sdist, and reproducibility contract

The build starts from a clean, protected-tag checkout in an isolated environment. The manifest records build backend/tool versions, Python version, OS/architecture, lockfile hash, source commit, environment variables that affect the build, and exact artifacts produced.

For the sdist gate, verify that:

- the sdist can be built from the clean checkout with the selected backend;
- a fresh isolated environment can build/install it on every declared platform/interpreter cell, using only declared build requirements and approved package sources;
- the resulting installed plugin passes the same real-loader suite as the wheel;
- build requirements are pinned or hash-locked separately from runtime requirements and are included in the evidence/SBOM boundary;
- the sdist contains no generated local state or secrets.

Build the same source twice in clean environments and compare normalized archive/file contents and hashes. If byte-for-byte reproducibility is not achieved, identify the nondeterministic fields, normalize them where possible, and record the residual difference and approval. “Build succeeded” alone is not reproducibility evidence.

## 6. Staged update, health gate, and rollback

Updates are not in-place mutations of a running provider. For an installed version `V` and candidate `V+1`:

1. **Inventory:** record active version, plugin directory/file-list hash, config schema/data version, outbox depth/oldest item, and required artifact hashes. Stop if the active state is not verified.
2. **Prepare:** verify the signed manifest/attestation and hashes offline; install dependencies into an inactive controlled environment; bootstrap the candidate into a new versioned plugin directory; run file-list, import, configuration, duplicate-provider, and synthetic-loader checks against that directory.
3. **Stage:** preserve the active directory and a restorable copy/manifest of configuration and migration-relevant state. Do not run migrations yet. Switch the profile's plugin pointer/selected directory atomically only after preparation passes; never expose a partially copied directory.
4. **Restart:** stop Hermes cleanly, start it with the candidate, and run the health gate before allowing traffic/memory operations. The gate must verify process readiness, exactly one provider, expected tool names, adapter connectivity/authentication without exposing secrets, writable outbox, zero unexpected startup errors, and one synthetic read/write or explicitly documented no-op health transaction.
5. **Commit or recover:** mark the candidate active only after the health result is durably recorded. On timeout, crash, provider-count mismatch, tool mismatch, failed auth/config, outbox/storage failure, or any unexpected startup error, stop the candidate and atomically restore the prior version/configuration, restart, and rerun the same health gate. If the prior version cannot start, stop and require human intervention; do not fall back to a degraded provider or erase state.

Migration limits must be explicit before implementation. V1 should declare either “no automatic schema/data migrations” or enumerate reversible, versioned migrations and their backup/rollback procedure. An update that needs an unreviewed irreversible migration is blocked. Rollback evidence must include candidate failure injection, restoration of the previous plugin and config/data state, restart output, health result, and outbox integrity check.

The **first release exception** is explicit: there is no previous release artifact to restore. Therefore the first release cannot satisfy the normal rollback rehearsal or claim rollback support. Its gate is limited to verified uninstall/disable recovery to the prior non-plugin Hermes configuration, with a human-approved backup of configuration/state. The full previous-artifact rollback invariant begins with the second release and every later release must name its verified rollback target.

## 7. Falsifiable health and CLI acceptance

Health/CLI evidence is acceptable only when each check has a command or harness action, expected exit/status, bounded timeout, and captured sanitized output. At minimum the release harness must assert:

- install/bootstrap: exit 0, expected versioned files present, no unexpected files, and recorded hashes match;
- discovery: exit 0 and exactly one composite provider plus the expected tool-name set;
- negative discovery: non-zero exit before any synthetic memory operation for missing plugin, bad config, unsupported interpreter, duplicate provider, and tampered artifact;
- runtime health: bounded response within the declared timeout, status `healthy` (or the exact selected status), provider identity/version match, outbox writable, and no credential/content leakage in output;
- restart: fresh process exits 0 and reproduces the same provider/tool identity;
- rollback: injected candidate failure yields non-zero candidate health, restored version equals the recorded previous version, and restored health passes;
- CLI surface: documented commands return the documented exit codes and stable machine-readable fields; unknown/malformed arguments return non-zero without a traceback or secret.

A prose statement, screenshot, log line without an assertion, or test of a fake loader does not satisfy acceptance. Exact command names remain implementation-dependent until the project selects the bootstrap/build tooling; the release manifest must record the actual commands used rather than this report inventing them.

## 8. Required release evidence bundle

The private release is publish-ready only when the bundle contains, for every claimed matrix cell:

1. signed-tag verification output and trusted asset-manifest/attestation verification output;
2. exact package/distribution/plugin names, source revision, upstream pins, Hindsight client identity, build backend/tool versions, interpreter/platform, and lockfile/export hash;
3. wheel and sdist, file lists, SHA-256 values, SBOM, license/notice inventory, and secret/content scan results;
4. clean wheel install and sdist build/install evidence using the declared hash-locked inputs;
5. real pinned-Hermes loader discovery evidence from `$HERMES_HOME/plugins/<name>/`, including the bootstrap record;
6. positive and negative one-provider/duplicate-provider evidence, with the wrapper's non-zero fail-closed result;
7. compatibility, adapter, outbox, restart, health/CLI, and rollback evidence with commands/actions, expected results, observed results, and sanitized output;
8. update manifest naming the previous verified artifact, or an explicit first-release rollback exception;
9. migration/configuration notes, recovery instructions, and residual findings with owner and disposition;
10. private GitHub access/dependency-acquisition provenance sufficient to show which authenticated source was used without retaining secrets.

CI is authoritative for publish readiness; local runs are advisory. Missing evidence is `BLOCKED`, never `PASS`.

## Safety invariants

1. V1 is private GitHub only; PyPI requires a separate decision and security review trigger.
2. No floating dependency resolution or unidentified Hindsight client.
3. Wheel installation and Hermes plugin discovery are separate, verified steps.
4. No install/import/discovery network call or credential requirement.
5. Duplicate external providers fail before memory operation through a tested guard; the pinned Hermes warning/drop limitation is disclosed.
6. No best-effort operation after identity, compatibility, integrity, health, or authentication failure.
7. Updates are staged, atomic, restart-based, and health-gated; first release does not claim previous-artifact rollback.
8. Every release after v1 names and verifies a previous artifact for rollback.
9. Package contents and evidence contain no secrets, real memory data, or estate-identifying paths.

## Successor decisions still required

1. Select and record the exact distribution name, plugin directory name, build backend, lock tool, Hindsight client package, platform matrix, and migration policy.
2. Select the trusted asset-signing/attestation mechanism and out-of-band trust root.
3. Implement and validate the bootstrap/launch duplicate-provider guard against the pinned real loader.
4. Produce Python 3.11, 3.12, and 3.13 matrix evidence, or narrow support honestly.
5. Define the concrete health/CLI harness and machine-readable evidence schema.

## Concise recommendation for issue #8

Proceed only as a gated private-release design. Build and sign a pinned wheel and sdist, verify assets independently of checksums/tag signatures alone, install dependencies and then bootstrap the verified plugin into `$HERMES_HOME/plugins/<name>/`, configure exactly one provider, and exercise the real pinned Hermes loader. Keep Python 3.11–3.13 conditional on complete passing evidence, resolve the Hindsight client and package names before release, fail closed through a tested wrapper where Hermes itself only warns/drops duplicates, and use atomic staged restart/health/rollback procedures. The first release may establish install/disable recovery but cannot honestly claim a previous-artifact rollback.
