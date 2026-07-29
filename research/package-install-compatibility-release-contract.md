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
- Hindsight `v0.8.6`, commit `08995e3013858e705fb4ca27c0ade3a286ef4750`; this source pin does **not** identify the Python client package.

The Hindsight source pin and the client dependency are separate manifest records. The client record MUST name the exact distribution/project name, client version, resolved source (index URL or VCS URL), commit or artifact SHA-256 where applicable, and every runtime dependency/hash. The source repository's generated client package must not be inferred from the repository name. No artifact may use `latest`, an unrecorded workspace member, or an unhashable floating dependency. Resolving the Hindsight client and final package names is a prerequisite, not a documentation detail.

## 2. Exact install and Hermes discovery contract

### 2.1 Required artifact layout

The wheel must contain the plugin source under a deterministic, declared package subdirectory and expose the selected distribution metadata. The wheel's `RECORD` and file-list evidence must identify the files that bootstrap copies into the Hermes plugin directory. The sdist must contain the same plugin source and build metadata; it must not contain local databases, logs, `.env` files, credentials, real memory fixtures, or machine-specific paths.

A wheel installed into an arbitrary virtual environment is **not sufficient** for discovery. On the pinned Hermes loader, discovery checks the bundled plugin directory and then `$HERMES_HOME/plugins/<name>/`; it loads that directory and selects it from `memory.provider`. The release procedure therefore has two distinct steps:

1. **Dependency install:** install the exact wheel into the controlled Hermes Python environment using the release lock/export and artifact hashes, with no import-time network access or credentials.
2. **Plugin bootstrap:** the implementation MUST expose one non-network bootstrap operation whose input is `(wheel path, target HERMES_HOME/profile, expected sidecar path, trust root, expected manifest)`. It MUST map exactly the manifest's `archive_prefix` (one declared wheel subdirectory, including its trailing `/`) to the plugin payload root; the mapping MUST NOT be inferred from an arbitrary top-level wheel directory. It MUST extract to a fresh staging directory under `plugins/.versions/<name>/<version>/`, reject absolute paths, `..` components, symlink/hardlink entries, duplicate normalized paths, and any archive member outside the staging root, then allow only the manifest's file allowlist plus the manifest's declared prefix. It MUST verify wheel `RECORD`, per-file SHA-256, distribution name/version, plugin name, source revision, and expected file-list hash before activation. Existing active content MUST remain untouched until verification passes; a version mismatch or unexpected file is a hard failure.

   The loader-compatible activation layout is deliberately **not** `plugins/<name>/current` and does not put versions below the active plugin directory. The durable layout is immutable `$HERMES_HOME/plugins/.versions/<name>/<version>/` payload directories, plus `$HERMES_HOME/plugins/<name>` itself as a symlink whose target is exactly `.versions/<name>/<version>` (relative to `plugins/`; the resolved destination is `plugins/.versions/<name>/<version>`). `<name>` and `<version>` MUST each be a safe single path component matching `[A-Za-z0-9][A-Za-z0-9._-]*`, and MUST NOT be `.` or `..`; reject separators, backslashes, NULs, and any other spelling before constructing a path. Bootstrap creates and verifies the version directory, fsyncs it and its parents, creates a temporary sibling symlink with that exact relative target, and atomically renames that symlink to `<name>` (the first install creates the symlink; later installs atomically replace the symlink). The real loader therefore resolves the exact path it already knows, `$HERMES_HOME/plugins/<name>/`, using ordinary filesystem symlink semantics; it does not need to recurse or understand a selector. The activation assertion MUST be executable and MUST canonicalize both sides, rather than compare prose or an uncanonicalized absolute path:

   ```sh
   plugin_name="$PLUGIN_NAME"
   plugin_version="$PLUGIN_VERSION"
   case "$plugin_name" in (''|.|..|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*) exit 1;; esac
   case "$plugin_version" in (''|.|..|[!A-Za-z0-9]*|*[!A-Za-z0-9._-]*) exit 1;; esac
   active="$HERMES_HOME/plugins/$plugin_name"
   expected_rel=".versions/$plugin_name/$plugin_version"
   expected_abs="$(realpath -e -- "$HERMES_HOME/plugins/$expected_rel")"
   test "$(readlink -- "$active")" = "$expected_rel"
   test "$(readlink -f -- "$active")" = "$expected_abs"
   ```

   The real-loader harness MUST run these assertions with variables populated from the verified manifest, then import and call `register(ctx)` through `$active`. If the host loader cannot follow this symlink, this layout is rejected and release is blocked until a separately reviewed loader integration change is implemented and proven; nested `current` is never an alternative.

   The bootstrap manifest is an external sidecar at the release-bundle root, `bootstrap-manifest.json`; it is not discovered from archive contents and is not trusted merely because it is present in the wheel. The signed asset manifest/attestation covers its bytes and the wheel/sdist hashes. The verifier receives the expected sidecar path and trust root out of band, verifies the sidecar signature and wheel hash before reading file claims, and only then copies a sanitized installation record into the selected version directory. A manifest found inside the archive, or a sidecar not covered by the trusted release attestation, is untrusted input and MUST be rejected.

The minimum bootstrap manifest schema is:

```json
{
  "schema": 1,
  "distribution": {"name": "<exact-name>", "version": "<exact-version>"},
  "plugin_directory": "<loader-directory-name>",
  "archive_prefix": "<single-wheel-directory/>",
  "wheel": {"filename": "<filename>", "sha256": "<64-lowercase-hex>"},
  "source": {"revision": "<full-commit-sha>"},
  "files": [{"path": "<relative-posix-path-under-prefix>", "size": 123, "sha256": "<64-lowercase-hex>"}],
  "file_list_sha256": "<64-lowercase-hex>",
  "loader": {"project": "Hermes Agent", "revision": "<full-commit-sha>"}
}
```

The manifest location, trust boundary, and archive mapping are normative: `bootstrap-manifest.json` is the signed external sidecar; `archive_prefix` is exactly one non-empty POSIX directory prefix ending in `/`; each archive member under that prefix maps to `path` after removing the prefix; no member outside it is payload. Archive names MUST be canonical UTF-8 POSIX paths: no NUL, backslash, empty component, `.` or `..` component, leading `/`, drive/UNC prefix, percent-encoded separator, or alternate Unicode normalization; the archive byte/name spelling MUST equal the validator's canonical spelling. Reject noncanonical input rather than normalizing it into acceptance. `files` MUST be non-empty, paths MUST be sorted lexicographically and unique, and the list MUST include a non-empty `__init__.py` and the module that defines `register`. The validator MUST prove that the mapped payload contains importable package files and a loadable `register(ctx)` before activation. `RECORD` is authoritative for wheel membership, but payload and wheel metadata are separate sets: every mapped payload file MUST have one matching `RECORD` row with the same normalized archive path, size, and hash (decoded from the RECORD hash format), and every such row MUST be listed in `files`; the only permitted wheel `.dist-info` members are `METADATA`, `WHEEL`, and `RECORD`, validated against the distribution metadata. No `.dist-info` member is copied into the plugin payload, and `entry_points.txt`, `direct_url.json`, `licenses/`, arbitrary data, signatures, and every other metadata member are rejected unless a separately named non-payload release artifact explicitly permits them. The external sidecar and trust-root/attestation files are never wheel members and are never copied as payload. The canonical file-list serialization is UTF-8 RFC 8785/JCS JSON of the `files` array (objects with exactly `path`, `size`, `sha256`, sorted by `path`, no trailing newline); `file_list_sha256` is SHA-256 of those exact bytes. This makes the hash independent of whitespace and key order while remaining executable by a standard JCS implementation.

The schema validator MUST reject missing/unknown required fields, a missing or untrusted sidecar/trust root, empty `files`, an empty or non-prefix `archive_prefix`, every noncanonical archive/path spelling, paths that escape the prefix, non-relative paths, duplicate normalized paths, invalid sizes/hashes, missing `__init__.py`/`register`, a RECORD mismatch or omitted mapped RECORD row, forbidden `.dist-info` members, invalid canonical file-list hash, and a `plugin_directory` that is empty, contains separators, or differs from `memory.provider`. The bootstrap verifier MUST return non-zero and produce a sanitized structured failure; no release may claim this gate until the operation, validator, verifier, and negative tests exist. Required negative tests are: `../` traversal, absolute member, backslash/`.`/empty/non-normalized member, symlink/hardlink, duplicate normalized path, archive member outside prefix, undeclared file, forbidden wheel metadata member, empty file list, missing `__init__.py`, missing/unloadable `register`, modified file, RECORD missing/mismatch, wrong canonical file-list hash, wrong wheel hash, wrong distribution/version, wrong archive prefix, wrong plugin directory, unsigned/untrusted sidecar or trust root, malformed manifest, and interrupted staging. **No bootstrap implementation exists in this repository; this is a hard release blocker.**

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

### 2.3 Duplicate-provider launch boundary and fail-closed guard

The launch boundary is a **separate preflight process wrapper around the real Hermes process**: the wrapper owns the process boundary, starts no Hermes memory operation until preflight succeeds, and must terminate the child on any post-start invariant violation. It is not a change to `MemoryManager.add_provider()`. The pinned Hermes method rejects a second external provider by logging a warning and dropping it; it does not raise, so a loader warning alone is insufficient.

The wrapper's concrete preflight algorithm is:

1. Enumerate the selected profile's configured `memory.provider`, every candidate directory under `$HERMES_HOME/plugins/`, and the pinned loader's discovered external-provider registrations using the real loader in a disposable synthetic configuration.
2. Classify each discovered external provider by canonical plugin directory and registered provider identity; reject missing directories, unreadable/invalid manifests, and any provider not named by the selected manifest.
3. Require exactly one external provider, and require it to be the manifest's composite. A second external directory/provider, a provider-name mismatch, or a duplicate registration is a failure before the synthetic memory operation.
4. Run the real loader's startup/discovery assertion and treat the pinned duplicate warning/drop signal as failure if it occurs. Exit non-zero with a redacted reason and do not invoke any memory operation.
5. Only after all assertions pass, `exec` the selected Hermes launch command, preserving its exit status. The operator/CI invocation MUST be the exact wrapper invocation recorded in the release manifest under `commands.launch_guard`; it must be the same invocation in CI and deployment. There is currently no implemented wrapper or launch command in this repository, so `commands.launch_guard` cannot honestly be populated and this gate is **BLOCKED**, not a passing command claim.

The guard's required acceptance cases are: one composite (zero exit, one provider), composite plus a second external plugin (non-zero before operation), missing plugin (non-zero), malformed manifest (non-zero), duplicate registration/warning (non-zero), and builtin-only configuration if unsupported by this product (non-zero with remediation). Each case must record provider enumeration, whether the memory-operation sentinel was reached, exit status, and sanitized stderr/stdout. The composite must never call `register_memory_provider` for either adapter. If the wrapper cannot observe and enforce this boundary on the pinned host, the release is blocked; the report must not claim that Hermes itself fails closed.

## 3. Compatibility and support gates

The release matrix is an explicit, ordered list of applicable tuples `(python, platform, artifact_variant)`, not an implicit Cartesian product. The release manifest/catalog MUST enumerate each tuple exactly once, in declared order, with `artifact_variant` equal to `wheel-install` or `sdist-build-install`; a tuple is included only when that artifact variant is applicable to that Python/platform cell (for example, an sdist build may be unsupported on a platform even when a wheel is published). The catalog's machine-readable `matrix.cells` object MUST either contain the tuple array or contain a precise manifest pointer, canonical encoding (`UTF-8 RFC 8785/JCS JSON` of the ordered array), hash algorithm (`SHA-256`), and digest of that exact array. A missing manifest pointer target, tuple array, or digest is `BLOCKED`—it is not an empty matrix and cannot support evidence. The catalog records the ordered tuple list or its manifest-derived digest, and evidence may reference no tuple outside it. For every listed tuple, run the real pinned-loader suite described above plus adapter contract, outbox/restart, health/CLI, package-content, and duplicate-provider tests.

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

Private GitHub access must be explicit: release download/build inputs use a short-lived, least-privilege credential supplied by the protected CI identity or an operator's preconfigured GitHub authentication, never a URL-embedded token, package metadata, shell history, or committed config. CI must fail if the private repository/release is inaccessible rather than silently resolving from public indexes.

Private dependency index isolation is a separate gate. The lock/export MUST enumerate the only allowed package hosts (the private index host(s) and, only if explicitly approved per dependency, the immutable public host); the manifest MUST record each host and whether it is allowed for direct or transitive dependencies. CI and operator installs MUST use an isolated package configuration supplied through protected CI configuration or the operator's preconfigured secret mechanism, with credentials supplied via the package tool's supported credential store/environment—not command-line URLs, source files, or committed config. The resolver MUST reject redirects or hosts outside the allowlist, and MUST run with public fallback disabled. The negative test MUST make the private index unavailable while a dependency exists only there and assert a non-zero resolution/install result, no request to a public index, and no credential leakage. A second negative test MUST make an unapproved public-only dependency available and assert rejection. No private-index implementation, host allowlist, credential binding, or no-public-fallback test exists in this repository; this is a hard release blocker.

Dependency acquisition must use the hash-locked export and configured private source credentials only where required; no credential is permitted at import or runtime discovery. Retain redacted authentication method, repository/release identifier, allowed-host policy, and download verification result, not tokens.

Every direct and transitive dependency MUST have one manifest record with: normalized distribution name, exact version, dependency edge (`direct` or the requiring distribution), selected source index URL and hostname, artifact filename/type, artifact SHA-256, and the lock/export line that selected it. A source record is a tuple `(normalized_name, exact_version, approved_host, artifact_sha256)`; the resolver and installer MUST verify all four fields, not merely the package name and version. The approved-host policy MUST assign exactly one source host to each name/version for a release. If the same name/version is offered by another approved host, or the artifact hash differs, resolution/install MUST reject rather than choose by priority, and the package tool MUST run with public fallback and implicit extra indexes disabled. Negative tests MUST offer an identically named/versioned dependency from an alternate approved host and assert non-zero resolution, no install, and an auditable rejection reason; a dependency-confusion test MUST offer an attacker-controlled higher-version or same-version artifact on an otherwise reachable host and assert the same. The evidence bundle MUST bind the installed distribution metadata and `direct_url.json`/index response (where applicable) to the manifest record and artifact hash. **No dependency provenance implementation or confusion test exists in this repository; this is a hard release blocker.**

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
3. **Stage:** preserve the active directory and a restorable backup set containing (a) the active versioned plugin directory plus its manifest/file-list hash, (b) the exact profile configuration bytes and mode/ownership, (c) the migration/data schema version, and (d) the Mnemosyne SQLite database and its SQLite sidecars (`-wal` and `-shm`) while the writer is quiesced. The backup record MUST include file sizes, SHA-256 values, SQLite `user_version`, journal mode, and a consistency marker. Do not run migrations yet.
4. **Activate:** use the loader-compatible layout defined in §2.1: `$HERMES_HOME/plugins/.versions/<name>/<version>/` contains the complete immutable candidate, while `$HERMES_HOME/plugins/<name>` itself is the only selector and is a symlink to that version directory. Prepare the candidate in a sibling staging directory, fsync required files/parent directories, create a temporary sibling symlink, and atomically rename it over the existing `<name>` symlink only after all checks pass. The pinned real-loader test must prove that this exact direct path resolves and imports; no nested `current` path is permitted. A crash before the switch leaves the old target; a crash after the switch leaves either the old or complete new target; a crash during staging leaves an ignored staging directory that startup removes or quarantines without selecting it. Never expose a partially copied directory.
5. **Restart:** stop Hermes cleanly, start it with the candidate, and run the health gate before allowing traffic/memory operations. The gate must verify process readiness, exactly one provider, expected tool names, adapter connectivity/authentication without exposing secrets, writable outbox, zero unexpected startup errors, and one synthetic read/write or explicitly documented no-op health transaction.
6. **Commit or recover:** mark the candidate active only after the health result is durably recorded. On timeout, crash, provider-count mismatch, tool mismatch, failed auth/config, outbox/storage failure, or any unexpected startup error, stop the candidate and atomically restore the prior version selection and exact configuration backup. Restore the migration/data version and the SQLite database with a consistent database/WAL set; never restore the main `.db` without its matching `-wal`/`-shm` state, and verify with SQLite integrity/user-version checks before restart. Rerun the same health gate and outbox integrity check. If the prior version cannot start, stop and require human intervention; do not fall back to a degraded provider or erase state.

The backup/restore rehearsal MUST inject failure before switch, after switch, during candidate startup, and during migration (if migrations exist), then assert plugin identity, config hash, migration version, SQLite/WAL consistency, and outbox rows equal the pre-update inventory. **No versioned-directory activation, atomic selector, consistency backup/restore implementation, or crash-injection evidence exists in this repository; update/rollback remains a hard release blocker.**

Migration limits must be explicit before implementation. V1 should declare either “no automatic schema/data migrations” or enumerate reversible, versioned migrations and their backup/rollback procedure. An update that needs an unreviewed irreversible migration is blocked. Rollback evidence must include candidate failure injection, restoration of the previous plugin and config/data state, restart output, health result, and outbox integrity check.

The **first release exception** is explicit and machine-resolvable in `research/release-assertion-catalog.json` under `release_context`: `release_version` is `1`, `required_from_release` for artifact rollback is `2`, `omission_allowed_before_release` is `true`, and `omission_reason` records that there is no previous artifact to restore. A validator MUST apply these rules: a missing/`null` `release_version` is `BLOCKED`; when a known release version is below `required_from_release`, the rollback gate may be omitted only when `omission_allowed_before_release` is true and the omission reason is retained; at or above `required_from_release`, rollback is required and MUST have one evidence object per listed matrix cell. A `BLOCKED` rollback result always requires a non-empty blocker and can never satisfy a required gate. Release 1 has a separate required `uninstall-disable-recovery` gate; it MUST prove uninstalling or disabling the plugin and restoring the prior non-plugin configuration/state, and it MUST NOT be omitted or treated as satisfied by the permitted rollback omission. Thus the first release cannot satisfy the normal rollback rehearsal or claim previous-artifact rollback support; its recovery proof is limited to verified uninstall/disable recovery with a human-approved backup of configuration/state. The full previous-artifact rollback invariant begins with the second release and every later release must name its verified rollback target.

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

## 8. Machine-readable evidence contract and required bundle

Every gate result MUST be a separate JSON object validated against the release evidence schema before it is considered evidence. The minimum schema is:

```json
{
  "schema": 1,
  "evidence_id": "<yyyy-mm-ddTHHMMSSZ>_<gate>_<matrix-cell>_<short-artifact-sha>",
  "gate": "<stable-gate-name>",
  "matrix_cell": {"id": "<stable-cell-id>", "python": "<exact-version>", "platform": "<OS-arch>", "artifact_sha256": "<64-lowercase-hex>"},
  "status": "PASS|FAIL|BLOCKED",
  "source_revision": "<full-commit-sha>",
  "artifact": {"filename": "<name>", "sha256": "<64-lowercase-hex>"},
  "environment": {"python": "<exact-version>", "platform": "<OS-arch>", "loader_revision": "<full-commit-sha>"},
  "command": {"id": "<stable-command-id>", "run_id": "<unique-run-id>", "argv": ["<actual-argv-elements>"], "cwd": "<recorded-cwd>", "tool_versions": {"<tool>": "<exact-version>"}, "environment_hash": "<64-lowercase-hex>", "config_hash": "<64-lowercase-hex>", "input_hashes": {"wheel": "<64-lowercase-hex>", "sidecar": "<64-lowercase-hex>", "trust_root": "<64-lowercase-hex>", "catalog": "<64-lowercase-hex>"}, "source_revision": "<full-commit-sha>", "resolved_outputs": ["<bundle-relative-path>"], "redaction_map": {"schema": 1, "policy": "<policy-version>", "hash": "<64-lowercase-hex>", "metadata": {"argv": true, "outputs": true, "paths": false}, "entries": [{"field": "<field-name>", "reason": "<secret|credential|payload|path>"}]}},
  "started_at": "<RFC3339>", "finished_at": "<RFC3339>", "exit_code": 0,
  "expected": {"exit_code": 0, "assertions": ["<stable-assertion-id>"]},
  "observed": {"assertions": [{"id": "<stable-assertion-id>", "result": true, "detail": "<sanitized>"}]},
  "outputs": [{"path": "<bundle-relative-path>", "sha256": "<64-lowercase-hex>", "kind": "stdout|stderr|report|trace", "produced_by": "<stable-command-id>", "provenance": {"command_id": "<stable-command-id>", "run_id": "<unique-run-id>", "cwd": "<recorded-cwd>", "environment_hash": "<64-lowercase-hex>", "config_hash": "<64-lowercase-hex>", "input_hashes": {"<name>": "<64-lowercase-hex>"}, "source_revision": "<full-commit-sha>", "resolved_path": "<bundle-relative-path>", "output_sha256": "<64-lowercase-hex>"}, "reuse_of": {"run_id": "<prior-run-id>", "output_sha256": "<64-lowercase-hex>"}}],
  "blocker": "<required non-empty detail when status is BLOCKED>",
  "redaction": {"secrets_removed": true, "payloads_synthetic_or_absent": true}
}
```

The contract's stable gate vocabulary is `bootstrap`, `loader-discovery`, `duplicate-provider-guard`, `dependency-provenance`, `wheel-install`, `sdist-build-install`, `compatibility`, `adapter-contract`, `outbox-restart`, `health-cli`, `rollback`, `uninstall-disable-recovery`, `artifact-authenticity`, and `package-content`. This exact ordered list is checked in as the machine-readable assertion catalog at `research/release-assertion-catalog.json` (the implementation is absent here, so the catalog itself is a release blocker). The catalog assigns immutable IDs and ordered assertions to each gate, declares `required` versus `optional` gates, and declares the ordered matrix dimensions and cardinality. No evidence may introduce a gate, cell, assertion, or ordering not present in that catalog. Each gate has a checked-in assertion vocabulary; assertion IDs are opaque stable strings from that vocabulary (for example `loader.discovery.direct_plugin_path`), never free-form labels. Every claimed matrix cell MUST have a stable `matrix_cell.id` derived from the manifest's ordered Python/platform/artifact tuple, and `matrix_cell.artifact_sha256` MUST equal `artifact.sha256`; `environment.python` and `environment.platform` MUST equal the same cell.

The checked-in catalog contains these stable assertion IDs in this order: `bootstrap` = `bootstrap.sidecar_trusted`, `bootstrap.wheel_hash_matches`, `bootstrap.payload_allowlist`, `bootstrap.activation_symlink`; `loader-discovery` = `loader.discovery.direct_plugin_path`, `loader.discovery.registers_composite`, `loader.discovery.fresh_process`; `duplicate-provider-guard` = `guard.one_external_provider`, `guard.rejects_duplicate_before_operation`; `dependency-provenance` = `deps.lock_hashes_match`, `deps.allowed_host`, `deps.no_public_fallback`; `wheel-install` = `wheel.install_clean`, `wheel.metadata_boundary`; `sdist-build-install` = `sdist.build_clean`, `sdist.install_loader_pass`; `compatibility` = `compatibility.interpreter_platform`, `compatibility.upstream_pins`; `adapter-contract` = `adapter.contract_pass`; `outbox-restart` = `outbox.restart_recovery`; `health-cli` = `health.status_and_tools`, `cli.exit_contract`; `rollback` = `rollback.restore_invariant`; `uninstall-disable-recovery` = `recovery.uninstall_disable_restores_prior_configuration`; `artifact-authenticity` = `artifact.tag_verified`, `artifact.attestation_verified`; and `package-content` = `package.file_list`, `package.no_secrets`. The catalog marks `bootstrap`, `loader-discovery`, `duplicate-provider-guard`, `dependency-provenance`, `wheel-install`, `sdist-build-install`, `compatibility`, `adapter-contract`, `outbox-restart`, `health-cli`, `uninstall-disable-recovery`, `artifact-authenticity`, and `package-content` as required; `rollback` is required from release 2 onward and optional only for the explicit first-release exception described by the catalog's machine-readable `release_context`. Matrix cardinality is the length of the catalog/manifest's explicit ordered applicable tuple list, not a computed Cartesian product. Every required gate MUST have exactly one evidence object per listed tuple; optional gates MUST be explicitly present as `BLOCKED` or omitted only when `release_context.validator_rule` and the gate's `omission_allowed_before_release`/`omission_reason` permit omission. Although the catalog models release 1's recovery rule, implementation and release evidence remain `BLOCKED`; no prototype result can satisfy these gates. Stable IDs are generated from the catalog's ordered tuple, not timestamps or filesystem order.

The validator MUST enforce required fields, enum values, RFC3339 timestamps, `finished_at >= started_at`, integer exit code, exact artifact/source/loader hashes, unique `evidence_id`, one known gate vocabulary, one known assertion vocabulary for that gate, non-empty `command.id`, `command.argv`, and `command.tool_versions`, and output paths staying inside the bundle. `command.id` MUST equal the stable digest of the canonical recorded/redacted argv, tool-version map, and redaction-map hash defined below, and each output's `produced_by` MUST equal it. `expected.assertions` MUST be non-empty, unique, and ordered according to the catalog. `observed.assertions` MUST contain exactly one object for every expected ID—no missing, duplicate, or extra IDs—and each result MUST be boolean with sanitized detail. `status=PASS` is valid only when `exit_code` equals expected exit code, every observed result is `true`, the matrix/artifact bindings match, and `outputs` is non-empty; every PASS output MUST exist, hash-verify, be produced by the recorded command ID, and have provenance sufficient to reproduce the assertion (stdout/stderr alone is not enough when the gate needs a report or trace). `FAIL` is a tested negative result and still requires the complete assertion set and output provenance. `BLOCKED` requires a non-empty `blocker` detail, cannot be promoted to `PASS`, and is not a substitute for missing observations. Evidence filenames MUST equal `evidence_id.json`; gate names and matrix coordinates MUST use the manifest's stable vocabulary. A CI publish job MUST validate every JSON file, reject missing/duplicate/malformed/stale evidence, reject a PASS with missing command/tool-version/output provenance, reject a result whose observed assertion set is not exactly the expected set, reject an unlisted matrix cell or artifact hash, and exit non-zero before publishing. The repository currently has no release evidence validator or CI publish gate; this is a hard release blocker. Do not claim any passing release evidence from the prototype's fake/stdlib artifacts.



Evidence records are single-use immutable observations: the same `evidence_id` or output/provenance record MUST NOT be reused for another gate or matrix cell, and a rerun gets a new run ID and evidence object. The same immutable artifact MAY be exercised in multiple matrix cells (for example one universal wheel on multiple interpreters/platforms) when the catalog explicitly lists that artifact hash in each cell; this is permitted artifact reuse, not evidence reuse. Each evidence object MUST bind `gate`, `matrix_cell.id`, artifact hash, source revision, environment identity, and run ID. The validator MUST reject a record whose bindings differ, duplicate `(gate, matrix_cell.id, artifact.sha256, run_id)`, or duplicate output hash/producer across distinct runs. A duplicate is permitted only when the output's `reuse_of` object satisfies the sole reuse exception defined below. It MUST reject cross-cell evidence reuse while allowing the same artifact hash only with independently executed, independently provenance-bound cell evidence.

Every command record MUST additionally contain `run_id`, `cwd`, `environment_hash`, `config_hash`, `input_hashes` (including wheel, sdist, lock/export, sidecar, trust-root, and catalog hashes as applicable), `source_revision`, `resolved_outputs`, and a `redaction_map` object containing a canonical entry list, metadata (schema, policy/version, and whether argv/output/path redaction was applied), and its SHA-256 hash. The map hash is SHA-256 of the UTF-8 JCS serialization of the map with its `hash` field omitted. `command.argv` is the canonical recorded/redacted argv (placeholders are required for secret-bearing fields); the unrevealed argv is never required for independent validation. `command.id` MUST be `SHA-256(UTF-8 JCS({"argv": command.argv, "tool_versions": command.tool_versions, "redaction_map_sha256": command.redaction_map.hash}))`, represented as lowercase hex. Thus independent validators can recompute the command identity from the record without secrets, while the redaction map binds every placeholder to a field and reason. `cwd` and resolved output paths MUST be bundle-relative or explicitly allowlisted absolute paths. Output provenance MUST be a run-scoped object identifying the command, run, cwd, environment/config/input hashes, source revision, resolved path, and output hash, so another operator can reproduce the assertion without relying on an unbound log line. Output reuse is forbidden unless the output contains a `reuse_of` object: that object is the sole exception, MUST identify the original run/output, and MUST prove an immutable content-addressed output plus an exactly matching complete provenance object. A rerun with a new run ID otherwise requires a newly executed output; there is no implicit shared-input exception. The bootstrap interface and evidence MUST include the sidecar path/hash and trust-root identity/hash, even though those files remain external to the wheel.

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
3. Implement and validate the concrete package bootstrap, versioned activation/restore mechanism, and launch duplicate-provider guard against the pinned real loader; retain the required negative/crash tests.
4. Produce Python 3.11, 3.12, and 3.13 matrix evidence, or narrow support honestly.
5. Implement the evidence-schema validator and CI publish rejection gate, then define the concrete health/CLI harness and record its actual invocations.
6. Select the private dependency index allowlist/credential mechanism and prove public fallback is rejected.

## Concise recommendation for issue #8

Proceed only as a gated private-release design. Build and sign a pinned wheel and sdist, verify assets independently of checksums/tag signatures alone, install dependencies and then bootstrap the verified plugin into `$HERMES_HOME/plugins/<name>/`, configure exactly one provider, and exercise the real pinned Hermes loader. Keep Python 3.11–3.13 conditional on complete passing evidence, resolve the Hindsight client and package names before release, fail closed through a tested wrapper where Hermes itself only warns/drops duplicates, and use atomic staged restart/health/rollback procedures. The first release may establish install/disable recovery but cannot honestly claim a previous-artifact rollback.
