# Issue #9 research: pre-publish security and license review boundary

Date: 2026-07-29  
Repository: `jtstothard/hermes-tiered-memory` at `5a3b010` (after fast-forwarding `main`).

## Scope and evidence policy

This is a source-only decision artifact for the harden + publish successor. I inspected the repository, prototype source/tests/configuration, and the pinned upstream source metadata. I did not start Hermes, connect to Hindsight, use credentials, read production data, change estate configuration, push to GitHub, create a commit, or close an issue.

The repository currently contains a fake/stdlib prototype and an acceptance harness, not an installable provider. The report therefore distinguishes:

- **Required now:** the practical, bounded review required before calling the successor publish-ready (source repository plus an installable package candidate).
- **Full-audit trigger:** objective evidence that the package has crossed into production, public distribution, multi-tenant use, or sensitive-data processing. The full audit is deliberately deferred until one of those conditions occurs.

Primary source pins and inspected local evidence:

- Hermes Agent `v2026.7.20`, commit [`3ef6bbd`](https://github.com/NousResearch/hermes-agent/tree/3ef6bbd201263d354fd83ec55b3c306ded2eb72a); its upstream `LICENSE` is MIT and its `pyproject.toml` declares exact-pinned direct dependencies.
- Mnemosyne `v3.14.0`, peeled commit [`4e5ed14`](https://github.com/mnemosyne-oss/mnemosyne/tree/4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce); its upstream `LICENSE` and `pyproject.toml` identify MIT licensing and `PyYAML>=6.0`.
- Hindsight `v0.8.6`, commit [`08995e3`](https://github.com/vectorize-io/hindsight/tree/08995e3013858e705fb4ca27c0ade3a286ef4750); its upstream `LICENSE` is MIT. Its repository is a workspace, so the exact client package and its transitive runtime tree must be resolved by the successor rather than inferred from the root file.
- Local architecture and known gaps: [`map.md`](../map.md), [`issues/07-hermes-integration-config.md`](../issues/07-hermes-integration-config.md), [`prototype-acceptance-handoff/ADR.md`](../prototype-acceptance-handoff/ADR.md), and the prior adapter/outbox reports in this directory.

## Decision

**Require a reproducible pre-publish gate covering code, dependency resolution, licenses, secrets, basic threat cases, package contents, and source/release integrity. Do not perform or claim a formal penetration/privacy/compliance audit yet. Escalate to a full audit as soon as there is evidence of production promotion, PyPI/public package release, multi-tenant or cross-customer use, or processing of real user PII/confidential memory data.**

The practical gate is a release blocker: no unresolved high/critical SAST finding, known exploitable dependency, incompatible license, credential, package-content leak, cross-profile read, unsafe outbox exposure, or unexplained supply-chain discrepancy may remain. Medium findings require a documented disposition; false positives must have an owner and rationale.

The prototype's current 7-test pass is not security evidence. It proves only the fake/stdlib boundary described by its README and `evidence.json`; it does not prove live HTTP behavior, authentication, package installation, SQLite crash safety, authorization, or license completeness.

## 1. Actual dependency and artifact inventory

### What exists in this checkout

A batched import inspection of every tracked Python file found only Python standard-library imports in the two prototypes and their tests:

| Area | Imports actually present | Security/license implication |
|---|---|---|
| Composite prototype | `queue`, `threading`, `dataclasses`, `typing`, `json`, `pathlib`, `subprocess`, `sys`, `unittest` | No third-party runtime dependency in the current prototype; Python and its standard-library license still belong in the environment inventory. |
| Acceptance harness | `hashlib`, `json`, `dataclasses`, `pathlib`, `typing`, `unittest`, `tempfile`, `subprocess`, `sys` | Same: fake/file-backed evidence only, not the production dependency tree. |
| Packaging metadata | No `pyproject.toml`, `setup.py`, `requirements*.txt`, `uv.lock`, `LICENSE`, or `NOTICE` is tracked in this repository | There is no package to publish yet and no authoritative dependency/license manifest. Creating these is a harden + publish prerequisite. |
| Local data/secrets | `.gitignore` excludes `__pycache__`, bytecode, `.venv`, `.env`/`.env.*` except `.env.example`, SQLite/DB files, and logs | Good first boundary, but it does not cover build output, credentials in arbitrary files/history, core dumps, or real memory fixtures. |

### Intended successor runtime tree

The architecture says the composite calls both APIs and bundles neither. The practical inventory to resolve once implementation starts is:

1. **Hermes Agent** `v2026.7.20` / `3ef6bbd`: plugin loader and `MemoryProvider` contract. The pinned upstream `pyproject.toml` declares Python `>=3.11,<3.14` and exact-pinned direct dependencies; the composite must test against the supported Python range rather than silently inheriting a different Hermes environment.
2. **Mnemosyne** `v3.14.0` / `4e5ed14`: runtime package declares `PyYAML>=6.0`; SQLite is Python's standard-library `sqlite3`/the platform SQLite implementation unless the final adapter adds another package. Verify the actual installed tree, because an upstream range is not a release lock.
3. **Hindsight** `v0.8.6` / `08995e3`: use the selected generated Python client/API package, or an explicitly reviewed HTTP client. The root is a multi-package uv workspace; the successor must record which workspace package is used and its resolved dependencies. Do not assume that the full Hindsight server stack is a client dependency.
4. **Composite-owned code:** durable SQLite outbox, HTTP/TLS adapter, retry/backoff, metadata and profile/bank mapping, package entry point, config validation, and tests. Any choice of `httpx`, `requests`, `hindsight-client`, serialization, or crypto package becomes a direct dependency and must be listed and pinned; none is present in the current checkout.
5. **Build/test tools:** `build`/wheel backend, test runner, Ruff, Bandit, Semgrep, `pip-audit`, SBOM generator, license inventory, Gitleaks/TruffleHog, and artifact verifier. These are CI/tooling dependencies, not automatically package runtime dependencies; pin their versions separately and record `tool --version` output.

**Dependency decision:** use a lockfile with hashes for the supported Python/platform matrix, exact pins for direct dependencies, and a generated requirements/export used by the audit. `pip-audit` is appropriate because its official project describes scanning Python environments and requirements files against the Python Packaging Advisory Database and OSV/PyPI sources ([project README](https://github.com/pypa/pip-audit)). `safety` may be a second advisory source if its policy and license are acceptable, but it is not a substitute for the lockfile or review. A clean audit means “no known vulnerability under the selected advisory sources,” not “proved secure.”

## 2. Required practical checks now

### A. Code review and SAST boundary

Run these against the production candidate, tests, packaging files, and release scripts; exclude only immutable third-party fixtures with an explicit reason:

| Check | Required now | Boundary and evidence |
|---|---:|---|
| Syntax/type/test baseline | Yes | `python -m compileall`, unit tests, adapter contract tests, isolated integration tests against disposable services, and package build/install/import test. Preserve command, interpreter, pins, and results. |
| Ruff | Yes | Pin a CI version and run lint/import/complexity rules selected in `pyproject.toml`; Ruff is an official Python linter/formatter with a documented rule catalogue ([Ruff docs](https://docs.astral.sh/ruff/), [rules](https://docs.astral.sh/ruff/rules/)). It is quality/static analysis, not a complete security scanner. |
| Bandit | Yes | Pin a CI version and run on provider, adapters, outbox, config, and release code. Bandit describes itself as finding common security issues in Python ([official README](https://github.com/PyCQA/bandit)); review every finding, especially subprocess, deserialization, temp-file, TLS, and weak-crypto rules. |
| Semgrep | Yes, focused | Run a pinned local/CI ruleset for Python and the provider's trust boundaries: HTTP requests, URL construction, auth headers, subprocess, unsafe YAML/pickle/eval, path traversal, and logging. Semgrep documents itself as static analysis for bugs, security guardrails, and coding standards ([official README](https://github.com/semgrep/semgrep)). A focused ruleset is practical now; organization-wide custom rules and taint-model completeness belong to the later audit. |
| Manual review | Yes | Read every path that accepts profile/bank/document IDs, constructs URLs or filesystem paths, loads config, logs errors, writes the outbox, serializes metadata, and handles retries. Verify fail-closed auth/config and no secret values in exception/trace logs. |
| Fuzz/property checks | Narrow now | Exercise malformed config, metadata, HTTP status/body, oversized content, duplicate/replayed outbox rows, path separators, and cross-profile identifiers. Full parser fuzzing, race analysis, and code-level formal methods are deferred. |

The practical SAST boundary is the plugin and its packaging/CI code, not a full security review of Hermes, Mnemosyne, Hindsight, Python, SQLite, PostgreSQL, pgvector, or their infrastructure. Those upstreams are dependency inputs: pin and scan them, then escalate if the composite changes their security boundary or embeds their code.

**Version provenance note:** this checkout has no CI/tool manifest and the scanners were not run as part of this source-only ticket. Therefore this report does not invent scanner version numbers. Before the gate is accepted, the successor must select and pin exact versions for each tool, record `tool --version`, and retain the tool configuration and report alongside the release evidence. The only exact versions established here are the architecture's upstream pins and the Python bounds above.

### B. Dependency, SBOM, and update checks

Required release evidence:

1. A clean disposable environment created from the lockfile; no inherited Hermes home, `.env`, credentials, or production paths.
2. `pip-audit` against the lock/export and installed wheel; capture advisory database date/source, tool version, interpreter, and findings ([pip-audit README](https://github.com/pypa/pip-audit)). Run a second advisory check only if the project can use its current terms/service safely.
3. SBOM in CycloneDX JSON (or an equivalent approved format) for the wheel/container/test environment, including package versions, hashes, licenses, and dependency relationships. The official CycloneDX Python library is the relevant generator reference ([cyclonedx-python-lib](https://github.com/CycloneDX/cyclonedx-python-lib)); record generator version and command.
4. Hash-checked installation (`--require-hashes` or the lock tool's equivalent) and a dependency diff against the previous release. A new transitive dependency, yanked release, source build, unsigned/unexpected artifact, or resolver change is a release review event.
5. Verify the pinned upstream commits and package metadata for Hermes, Mnemosyne, and Hindsight; do not use `main`, `latest`, or an unreviewed server image.

SQLite itself is a platform component, not a PyPI package in this prototype. The successor must record Python version, SQLite library version, journaling mode, and OS image in integration evidence; database encryption/backup/permissions are outside this pre-publish source-only review unless the package ships or configures them.

### C. License and notice review

The inspected upstream source licenses are all MIT: Hermes Agent's pinned [`LICENSE`](https://raw.githubusercontent.com/NousResearch/hermes-agent/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/LICENSE), Mnemosyne's pinned [`LICENSE`](https://raw.githubusercontent.com/mnemosyne-oss/mnemosyne/4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce/LICENSE), and Hindsight's pinned [`LICENSE`](https://raw.githubusercontent.com/vectorize-io/hindsight/08995e3013858e705fb4ca27c0ade3a286ef4750/LICENSE). Calling their APIs and bundling neither does not eliminate the need to inventory the runtime client and transitive dependencies.

Required now:

- Publish the composite under **MIT**, unless the maintainer obtains a different explicit legal decision. MIT is consistent with the inspected upstreams and permits a small plugin to be reused with Hermes; include the copyright/author and MIT notice in the source repository and package metadata.
- Run a pinned license inventory tool (for example [`pip-licenses`](https://github.com/rauenzi/pip-licenses)) over the lockfile/installed environment, and manually verify each package's declared license against its license file/metadata. Record tool version, package version, SPDX-normalized expression, and source URL.
- Include required MIT notices for distributed code. Do not copy upstream source into the package unless necessary; API use is preferable because the architecture bundles neither upstream.
- Review optional/dev/build dependencies separately from runtime dependencies. A GPL/AGPL/non-commercial/custom license in any distributed runtime or bundled artifact is a blocker until legal compatibility is decided; a tool-only dependency is not automatically a runtime licensing issue but still belongs in the SBOM/release record.
- Inspect Hindsight's selected client package and all transitive licenses, not merely the Hindsight repository root. The root MIT finding is not proof that every workspace/package dependency is MIT-compatible.

This is a compatibility inventory, not legal advice. A formal legal license opinion is deferred unless a non-permissive, ambiguous, bundled, or changed license is discovered.

### D. Secret and sensitive-data handling

The plugin is expected to handle a Hindsight API key/token, endpoint/TLS settings, profile/bank identifiers, and memory content in the L1 database/outbox. Mnemosyne configuration may include local paths and bank selection; bank identifiers are routing/isolation data, not credentials, but may still be sensitive. The outbox necessarily stores content and metadata snapshots until promotion or quarantine.

Required now:

- Credentials enter only through the supported Hermes/profile secret mechanism or environment; never source, package metadata, test fixtures, command-line arguments, URLs, or committed config. `.env` is ignored today, but the release checklist must also scan wheel/sdist/build directories and generated evidence.
- Provide a sanitized `.env.example` or config template containing placeholders only; never real endpoint, bank, profile, memory, or token values. Make missing credentials fail closed before a network call.
- Run pinned Gitleaks ([official project](https://github.com/gitleaks/gitleaks)) over the current tree and relevant Git history, and optionally TruffleHog ([official project](https://github.com/trufflesecurity/trufflehog)) as a second detector. Record findings only as type/path/commit with values redacted. A clean working tree is not proof that an old commit is clean.
- Scan package contents (`tar -tzf`, `unzip -l`) and Git-tracked files for `.env`, databases, logs, tokens, private keys, authorization headers, internal endpoints, and real memory fixtures. Verify `.gitignore` actually excludes local state, but do not rely on ignore rules as a scanner.
- Redact secrets and memory payloads from error logs, evidence JSON, test output, HTTP traces, and quarantine records. Use synthetic fixtures only; the map explicitly prohibits live production data.
- Apply the release-estate marker scan as an additional publication check: no personal paths, estate hostnames/IPs, profile names, internal service URLs, or trace identifiers. Authorship may remain; estate topology and real data may not.

A discovered real credential is an immediate stop-and-escalate event: do not publish, revoke/rotate through the authorized owner, determine exposure history, and only then reassess the release. This report does not perform credential rotation.

### E. Basic threat-model checks now

| Threat | Practical pre-publish test/control | Full-audit boundary |
|---|---|---|
| L2 exfiltration or unintended network destination | Allowlist/validate configured HTTPS endpoint; reject missing/invalid TLS/auth config; unit-test that only intended Hindsight calls occur; no hidden telemetry or fallback URL. Use a disposable endpoint only. | Penetration test, egress policy, TLS/certificate deployment review, server-side access review, and incident response. |
| Prompt injection in memory content | Treat recalled/retained text as untrusted data; do not execute instructions from memory; delimit provenance; test malicious synthetic content cannot alter routing/tool selection or authorize an action. | Red-team agent/tool chains, model-specific indirect prompt-injection assessment, policy/compliance review. |
| Unauthorized cross-profile reads | Bind every L1/outbox/L2 operation to profile + bank + metadata/tag scope; test alpha cannot recall beta, including L1 miss, backfill, replay, and restart. Fail closed on absent/mismatched identity. | Multi-tenant authorization review, server-side tenant isolation, concurrent/race testing, external penetration test. |
| Outbox data leakage on crash | Restrictive file permissions; no payloads in ordinary logs; inspect WAL/outbox/quarantine after synthetic crash/restart; define retention/compaction and deletion behavior. | At-rest encryption/key management, backup/restore access review, forensic recovery, privacy/retention audit. |
| Replay/revision confusion | Test stable `(profile, bank, mnemosyne_id)` mapping, document replacement, delete/tombstone, async operation IDs, retry/quarantine, and malformed responses against pinned/disposable services. | Distributed concurrency/fault-injection campaign and formal data-loss/privacy review. |
| SSRF/path traversal/config injection | Validate endpoint scheme/host policy and profile/bank/path components; reject traversal/control characters; test YAML/JSON/config parsing with hostile synthetic inputs. | Full network/application penetration test and deployment-specific egress controls. |
| Supply-chain compromise | Hash-pinned lockfile, clean build, dependency audit, SBOM, provenance/signature verification, and artifact diff. | Organization-wide supplier risk, signing-key custody, independent build, incident/revocation exercise. |

These controls are proportional to a source/package publication review. They do not establish that a live Hindsight deployment is secure or that the agent is safe against all prompt-injection classes.

## 3. Package and release integrity

A publish candidate must be built from a clean, pinned checkout in an isolated environment. Required evidence:

1. Source commit, tag, Python version, build backend/tool versions, lockfile hash, and dependency hashes.
2. sdist and wheel hashes; inspect their file lists and install into a fresh environment using hashes.
3. Rebuild from the same source in a second clean environment and compare normalized contents/hashes, or document unavoidable nondeterminism. Reproducibility is practical now; a fully independent build service is deferred.
4. Verify the package imports, registers exactly one Hermes provider, has no install-time network call, and fails closed for missing credentials/config.
5. Generate an SBOM and license report for exactly what is distributed. Test that no `.env`, SQLite/WAL, log, fixture with real content, or local path is present.
6. If/when PyPI is selected, use PyPI Trusted Publishing/OIDC rather than a long-lived upload token where supported ([PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/)); protect the release workflow, pin actions, require review, and publish only from a protected tag. Attach checksums and provenance/attestation. SLSA's level definitions ([SLSA v1.0](https://slsa.dev/spec/v1.0/levels)) are the reference for describing provenance maturity; do not claim a SLSA level without satisfying its requirements.

The current task does **not** publish to PyPI or GitHub. Public package release itself is one of the explicit full-audit triggers below, even if these basic integrity checks have already passed.

## 4. Precise full-audit trigger

Escalate from this practical review to a formal security/privacy/license/supply-chain audit on the **first verified occurrence** of any condition below, and do not treat a clean pre-publish gate as a waiver:

1. **Production promotion:** the real composite is enabled in any production Hermes profile, points at a live Hindsight service, or writes/reads non-test memory data.
2. **Public package release:** a wheel/sdist is uploaded to PyPI or another public package index, or a public release artifact is distributed for installation outside the private repository.
3. **Multi-tenant/cross-customer use:** more than one independent user, customer, organization, or trust domain can share the service, bank namespace, endpoint, or package deployment.
4. **Sensitive-data processing:** the provider handles real user PII, confidential business data, regulated data, secrets, or memory data subject to contractual retention/deletion obligations—even if the deployment is private.
5. **Security-boundary expansion:** the plugin gains privileged filesystem/network access, server-side administrative APIs, arbitrary tool execution, a new process/sidecar, custom crypto, remote code/plugin loading, or a change that can bypass profile/bank authorization.
6. **Material supply-chain/license event:** a new non-permissive/ambiguous license, a critical/high exploitable dependency finding, a compromised/revoked artifact, a package-signing/provenance failure, copied upstream code, or a dependency/source change outside the pinned review.
7. **Incident evidence:** a credential leak, cross-profile read, unexpected egress, memory disclosure, tampered artifact, or unexplained outbox data-loss/corruption event.

At the trigger, the formal audit should include independent code review/penetration testing, threat modeling and abuse cases, tenant authorization and race testing, secret/PII lifecycle and backup review, dependency/SBOM/provenance verification, license counsel review, live deployment configuration review, and a documented remediation/retest cycle. The exact compliance framework depends on the actual data and customers; it cannot be chosen honestly before those facts exist.

## 5. Release evidence bundle / acceptance checklist

The successor should not mark this ticket complete until the following sanitized artifacts exist locally or in CI:

- `git status`, source commit/tag, supported Python matrix, and pinned Hermes/Mnemosyne/Hindsight SHAs.
- Test/compile output, live-adapter tests against disposable services, and cross-profile/restart/outbox threat-case results.
- Ruff, Bandit, Semgrep, Gitleaks (and any TruffleHog) versioned reports with dispositions.
- Lockfile/export with hashes, `pip-audit` report, dependency diff, CycloneDX SBOM, and SQLite/Python runtime versions.
- `pip-licenses`/license inventory, upstream MIT license copies/URLs, composite LICENSE/NOTICE, and any legal exceptions.
- sdist/wheel file lists and SHA-256 hashes; clean install/import test; reproducibility comparison; provenance/attestation plan.
- Secret/history/package-content scan result and an explicit statement that only synthetic fixtures were used.
- A signed decision record naming any residual medium findings and why they do not cross the full-audit trigger.

## Newly visible successor questions

1. Which Hindsight Python client/workspace package is the final runtime dependency, and what exact resolved license/advisory tree does it produce?
2. Will the composite depend directly on Hermes Agent, Mnemosyne, and Hindsight packages, or load against a host-installed Hermes and use only client libraries? The answer determines the package's runtime constraints and license obligations.
3. What package name, maintainer copyright, MIT `LICENSE`, `NOTICE`, and SPDX metadata will the installable artifact use?
4. Which lockfile/build system will provide cross-platform hashes, and which exact versions of Ruff, Bandit, Semgrep, `pip-audit`, CycloneDX, `pip-licenses`, and secret scanners will CI pin?
5. What is the supported endpoint allowlist/TLS policy, and how are Hindsight credentials injected without appearing in process arguments or logs?
6. What outbox/WAL file permissions, retention, compaction, and operator redaction policy apply before at-rest encryption becomes a full-audit concern?
7. Which publication target is intended (private artifact, GitHub release only, or PyPI), and what protected workflow/provenance level is required before that target is enabled?
8. What exact data classification and tenant model will exist at promotion time? This answer selects the formal privacy/compliance audit scope.

## Concise recommendation for issue #9

Proceed with harden + publish only after a bounded, reproducible gate: implement package metadata and a lockfile; run version-pinned Ruff, Bandit, focused Semgrep, tests, `pip-audit`, CycloneDX SBOM, license inventory, Gitleaks/history scan, artifact-content scan, and the synthetic threat matrix; verify all upstream pins and MIT notices; and produce clean wheel/sdist/reproducibility evidence. The current fake/stdlib prototype has no third-party runtime dependency and no package license manifest, so it cannot yet satisfy that gate. Defer a formal audit, but make it mandatory on the first production enablement, public package release, multi-tenant use, real PII/confidential-memory processing, security-boundary expansion, material supply-chain/license finding, or incident.
