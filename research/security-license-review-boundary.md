# Issue #9 research: pre-publish security and license review boundary

Date: 2026-07-29  
Repository evidence revision: `d762f28353ca6cb5ddd65bdf4a706842131226df` (the immutable commit resolved as `HEAD` when this revision of the report was edited). The earlier draft named `5a3b010`; that is historical context only and is not the evidence revision for this report.

## Scope and evidence policy

This is a source-only decision artifact for the harden + publish successor. I inspected the repository at the evidence revision above, its prototype source/tests/configuration, and the pinned upstream source metadata. I did not start Hermes, connect to Hindsight, use credentials, read production data, change estate configuration, push to GitHub, create a commit, or close an issue. Local observations are therefore claims about that exact checkout, not about a moving branch or a later checkout.

### Publish set and hygiene boundary

The intended publish set is an explicitly generated allowlist: source and tests needed to build the successor, packaging/build metadata, the selected `LICENSE`/`NOTICE` files, sanitized configuration examples, and decision documentation that has passed the release scan. It is **not** “every tracked file.” Research notes, local evidence captures, generated reports, databases/WALs, logs, `.env*` files (other than a placeholder example), build directories, caches, worktrees, and any fixture containing real memory, credentials, personal filesystem paths, estate hostnames/IPs, internal URLs, profile names, or trace IDs are excluded unless a reviewer explicitly records why a non-sensitive synthetic fixture is needed.

Before publication, generate the set from a clean checkout and record the exact file list and archive manifest. Run the estate-marker scan, secret/history scan, and a direct tracked-tree/package-content sweep over that set and over the repository history; require zero blocking hits. Scrubbing is replacement with synthetic values, not relabelling real identifiers. This report records the required scans and boundaries only: no scanner, legal review, or publish-set audit was run for this source-only ticket, so their results must not be inferred from the absence of findings below.

**Known current publish-set findings:** tracked documentation and evidence currently contain estate-specific filesystem paths, including `/home/hermes/.scratch/mnemosyne-hindsight-promotion/` in `issues/01-composite-provider-boundary.md`, `issues/08-prototype-acceptance-handoff.md`, `prototype-acceptance-handoff/ADR.md`, `prototype-acceptance-handoff/README.md`, and `prototype-composite-provider/README.md`, plus `/home/hermes/hermes-tiered-memory/` in `prototype-composite-provider/evidence.json` and `research/live-adapter-contract.md`. These are findings from source inspection, not a completed release scan, and they are not publish-safe merely because the paths are local or the prototypes are fake. Before any distribution, the owner must choose and document one of: (a) scrub them to synthetic/configurable placeholders and assess the resulting history; (b) if the repository history has been distributed, use an explicitly approved history rewrite and coordinate the rewritten refs; or (c) prove that the generated allowlist excludes every affected file/content occurrence **and** prove that the repository history containing them has not been distributed. An allowlist exclusion alone does not clean already-distributed history.

The repository currently contains a fake/stdlib prototype and an acceptance harness, not an installable provider. The report therefore distinguishes:

- **Required now:** the practical, bounded review required before calling the successor publish-ready (source repository plus an installable package candidate).
- **Full-audit trigger:** objective evidence that the package has crossed into production, public distribution, multi-tenant use, or sensitive-data processing. The full audit is deliberately deferred until one of those conditions occurs.

Primary source pins and inspected local evidence:

- Hermes Agent `v2026.7.20`, commit [`3ef6bbd`](https://github.com/NousResearch/hermes-agent/tree/3ef6bbd201263d354fd83ec55b3c306ded2eb72a); its upstream `LICENSE` is MIT and its `pyproject.toml` contains ranged/conditional dependency declarations. The successor must resolve the selected environment to a lockfile with hashes; the source pin alone is not a dependency lock.
- Mnemosyne `v3.14.0`, peeled commit [`4e5ed14`](https://github.com/mnemosyne-oss/mnemosyne/tree/4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce); its upstream `LICENSE` and `pyproject.toml` identify MIT licensing and `PyYAML>=6.0`.
- Hindsight `v0.8.6`, commit [`08995e3`](https://github.com/vectorize-io/hindsight/tree/08995e3013858e705fb4ca27c0ade3a286ef4750); its upstream `LICENSE` is MIT. Its repository is a workspace, so the exact client package and its transitive runtime tree must be resolved by the successor rather than inferred from the root file.
- Local architecture and known gaps: [`map.md`](../map.md), [`issues/07-hermes-integration-config.md`](../issues/07-hermes-integration-config.md), [`prototype-acceptance-handoff/ADR.md`](../prototype-acceptance-handoff/ADR.md), and the prior adapter/outbox reports in this directory.

## Decision

**Require a reproducible pre-publish gate covering code, dependency resolution, licenses, secrets, basic threat cases, package contents, and source/release integrity. Do not perform or claim a formal penetration/privacy/compliance audit yet. Escalate to a full audit as soon as there is evidence of production promotion, PyPI/public package release, multi-tenant or cross-customer use, or processing of real user PII/confidential memory data.**

The practical gate is a release blocker: no unresolved high/critical SAST finding, known exploitable dependency, incompatible license, credential, package-content leak, cross-profile read, unsafe outbox exposure, or unexplained supply-chain discrepancy may remain. Medium findings require a documented disposition; false positives must have an owner and rationale.

The prototype evidence has two distinct counts that must not be conflated: the current composite prototype reports a **7-case** pass, while the acceptance-handoff harness reports **11/11 local contract cases** (as stated in the repository README). Neither is security evidence. Together they prove only the fake/stdlib/modelled boundaries described by their respective evidence files; they do not prove live HTTP behavior, authentication, package installation, SQLite crash safety, authorization, supply-chain integrity, or license completeness.

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

1. **Hermes Agent** `v2026.7.20` / `3ef6bbd`: plugin loader and `MemoryProvider` contract. The pinned upstream `pyproject.toml` declares Python `>=3.11,<3.14` and ranged/conditional dependencies; the composite must resolve those dependencies for each supported Python/platform matrix into its own successor lockfile rather than silently inheriting a different Hermes environment.
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

**Version provenance note:** this checkout has no CI/tool manifest and the scanners were not run as part of this source-only ticket. Therefore this report does not invent scanner version numbers. Before the gate is accepted, the successor must select and pin exact versions for each tool, record `tool --version`, retain the tool configuration and report alongside the release evidence, and bind each result to the immutable source commit, lockfile hash, and artifact digest it examined. The only exact versions established here are the architecture's upstream pins and the Python bounds above.

### B. Dependency, SBOM, and update checks

Required release evidence:

1. A clean disposable environment created from the lockfile; no inherited Hermes home, `.env`, credentials, or production paths.
2. `pip-audit` against the lock/export and installed wheel; capture advisory database date/source, tool version, interpreter, and findings ([pip-audit README](https://github.com/pypa/pip-audit)). Run a second advisory check only if the project can use its current terms/service safely.
3. SBOM in CycloneDX JSON (or an equivalent approved format) for the wheel/container/test environment, including package versions, hashes, licenses, and dependency relationships. The official CycloneDX Python library is the relevant generator reference ([cyclonedx-python-lib](https://github.com/CycloneDX/cyclonedx-python-lib)); record generator version and command.
4. Hash-checked installation (`--require-hashes` or the lock tool's equivalent) and a dependency diff against the previous release. A new transitive dependency, yanked release, source build, unsigned/unexpected artifact, or resolver change is a release review event.
5. Verify the pinned upstream commits and package metadata for Hermes, Mnemosyne, and Hindsight; do not use `main`, `latest`, or an unreviewed server image. Record the full commit SHA, retrieval URL, expected tree/file hashes, and verification time. A short SHA or a link that merely resolves today is a locator, not immutable authenticity evidence.

**Supply-chain trust roots:** the current upstream SHAs and MIT observations are source facts, not proof that the fetched bytes came from an authentic maintainer or were not replaced in transit. The successor release process must define trusted roots: authenticated HTTPS/TLS with normal certificate and hostname verification; a pinned repository/ref or package-index policy; verified signed tags/commits or maintainer-published checksums/attestations where available; hash-locked dependency artifacts; and a protected build/release identity (for PyPI, Trusted Publishing/OIDC rather than a long-lived token). Record which trust check was available and its result. If signature/provenance verification is unavailable or fails, stop the release or explicitly escalate it as a supply-chain exception; do not silently treat a matching package name/version as trusted.

SQLite itself is a platform component, not a PyPI package in this prototype. The successor must record Python version, SQLite library version, journaling mode, and OS image in integration evidence. Package publication does not ship or configure database encryption/backup, but any real-data deployment must define those controls at its deployment boundary and apply the data-classification/full-audit triggers below.

### C. License and notice review

The inspected upstream source licenses are all MIT: Hermes Agent's pinned [`LICENSE`](https://raw.githubusercontent.com/NousResearch/hermes-agent/3ef6bbd201263d354fd83ec55b3c306ded2eb72a/LICENSE), Mnemosyne's pinned [`LICENSE`](https://raw.githubusercontent.com/mnemosyne-oss/mnemosyne/4e5ed14ab2b9befbc4b77f1f5b43d06de8c18bce/LICENSE), and Hindsight's pinned [`LICENSE`](https://raw.githubusercontent.com/vectorize-io/hindsight/08995e3013858e705fb4ca27c0ade3a286ef4750/LICENSE). Calling their APIs and bundling neither does not eliminate the need to inventory the runtime client and transitive dependencies.

Required now:

- **Candidate recommendation only:** MIT is a plausible publication license because the three inspected upstream roots are MIT, but publication under MIT is conditional on the final resolved runtime/build dependency inventory, contributor/author ownership and inbound-license review, and a manual license/notice review of every distributed artifact. Do not publish or describe MIT as decided until those checks pass (or an explicit legal decision selects another license); include the copyright/author and chosen notice only after that decision.
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

#### Fixed Hindsight destination contract (release gate)

The successor must not accept an arbitrary URL. Its configuration contains one required **Hindsight origin allowlist entry**, and configuration loading fails closed if it is absent, malformed, duplicated, or has more than one origin. The entry is an exact tuple:

```text
scheme = https
hostname = <one configured DNS name, canonicalized with IDNA2008 UTS #46 transitional=false, lower-case, trailing dot removed>
port = 443
base_path = <one configured, absolute API prefix; no path traversal or query/fragment>
```

The hostname is a DNS name, never an IP literal, and its canonical ASCII form is the only approved identity. There is no default hostname, wildcard, suffix match, alternate port, HTTP mode, or “localhost” mode. The approved destination is the exact origin `https://<canonical-host>:443` plus the configured API prefix; requests may use only the fixed Hindsight API paths registered by the adapter beneath that prefix. A URL supplied by a memory item, bank/profile value, response, redirect, environment proxy, or request parameter is never allowed to replace any tuple member. If a deployment genuinely needs another Hindsight origin or port, an administrator must issue a new reviewed configuration and rerun this gate; it is not a runtime override.

The endpoint/allowlist configuration is **authenticated and admin-only**: it is loaded only from the profile's protected secret/config mechanism, is not writable by memory content or ordinary provider inputs, and startup must fail closed when its owner/permissions or schema cannot be established. The Hindsight API credential is separate, injected through the supported secret mechanism, and must not appear in the URL, logs, proxy configuration, or process arguments. The release record must name the canonical hostname, port, base path, TLS trust policy, and the exact endpoint identity; this source-only report intentionally contains no real deployment hostname or credential.

#### Address, DNS, connector, proxy, and redirect invariant

For every outbound request, endpoint validation must be enforceable, not a best-effort DNS check. Parse the configured origin strictly, canonicalize IDNA before comparison, reject userinfo/fragments/control characters/ambiguous ports and all IP-literal spellings (decimal, octal, hexadecimal, integer, mixed IPv4, bracket tricks, IPv4-mapped IPv6), and reject any address that is not globally routable. Use a **controlled resolver/connector pattern**: resolve the canonical hostname through an injected resolver; follow and validate the complete bounded CNAME chain and every A and AAAA answer; re-check the resulting address set immediately before connect; connect the socket to one of those validated numeric addresses while passing the original canonical hostname to TLS for SNI and certificate hostname verification; and prevent a second unconstrained resolver lookup in the HTTP client. A resolver result/connection race must fail closed, not fall back to the system resolver. This is the accepted implementation pattern for this gate; an undefined “equivalent” is not accepted.

The deny policy must reject the following IPv4 ranges, including their IPv4-mapped IPv6 forms: `0.0.0.0/8` (current network/unspecified), `10.0.0.0/8` (private), `100.64.0.0/10` (shared/CGNAT), `127.0.0.0/8` (loopback), `169.254.0.0/16` (link-local), `172.16.0.0/12` (private), `192.0.0.0/24` (IETF special use, including NAT64/DNS64 discovery exceptions), `192.0.2.0/24` (documentation), `192.88.99.0/24` (6to4 relay anycast), `192.168.0.0/16` (private), `198.18.0.0/15` (benchmarking), `198.51.100.0/24` (documentation), `203.0.113.0/24` (documentation), `224.0.0.0/4` (multicast), and `240.0.0.0/4` (reserved/future use, including limited broadcast). Apply the current IANA IPv4 Special-Purpose Address Registry as a deny-by-default supplement so newly assigned special-purpose blocks cannot become reachable by policy omission.

The deny policy must reject these IPv6 categories/prefixes: `::/128` (unspecified), `::1/128` (loopback), `::ffff:0:0/96` (IPv4-mapped), `::/96` (IPv4-compatible/deprecated), `64:ff9b::/96` and `64:ff9b:1::/48` (NAT64 translation), `100::/64` (discard-only), `2001:0000::/32` (Teredo), `2001:0002::/48` (benchmarking), `2001:0010::/28` and `2001:0020::/28` (ORCHID/ORCHIDv2), `2001:db8::/32` (documentation), `2002::/16` (6to4 transition), `fc00::/7` (unique-local), `fe80::/10` (link-local), and `ff00::/8` (multicast). Also reject any address classified by the current IANA IPv6 Special-Purpose Address Registry as not globally reachable; do not rely solely on a language runtime's `is_private` flag.

Proxies are disabled by default, including `HTTP_PROXY`, `HTTPS_PROXY`, `ALL_PROXY`, lowercase variants, proxy config files, and automatic proxy discovery. The client must use an explicit no-proxy transport/session, not merely clear one environment variable. No proxy may be configured by ordinary provider input. If an administrator-approved proxy is later supported, it is a separate exact allowlist entry subject to the same controlled resolution/address policy; HTTPS CONNECT must use only the approved proxy authority, the CONNECT target must equal the approved canonical Hindsight authority, and the request-form target/Host/SNI must not be redirected to a user-controlled authority. Redirects are disabled by default. If a reviewed mode enables them, every hop must be HTTPS and an exact match for the configured canonical scheme, hostname, port, and API prefix; cross-origin, alternate-port, userinfo, downgrade, and credential/proxy-policy changes are rejected.

Required tests are deterministic unit/integration tests using an injected resolver and disposable TLS HTTP server: IDNA/lowercase/trailing-dot canonicalization; every alternate IPv4 spelling and IPv4-mapped IPv6; every listed IPv4/IPv6 deny range plus registry-classified special addresses; mixed A/AAAA answers; multi-hop and cyclic CNAME chains; missing/extra/poisoned CNAME answers; resolver/connect rebinding races; proof that the socket uses the validated address while TLS SNI and certificate verification use the canonical hostname; no proxy use despite all environment proxy variables and auto-discovery; explicit proxy rejection and, if implemented, CONNECT/request-target enforcement; exact-origin redirect acceptance and cross-origin/port/scheme/path/credential rejection; and no network call on invalid or unauthenticated config. Retain the resolver inputs, canonical tuple, selected address, and redacted decision outcome as test evidence—never credentials or real memory.

| Threat | Practical pre-publish test/control | Full-audit boundary |
|---|---|---|
| L2 exfiltration or unintended network destination | Enforce the fixed destination contract and controlled resolver/connector invariant above; test all canonicalization, deny-range, CNAME, rebinding, TLS-SNI, proxy, CONNECT, request-target, and exact-origin redirect cases against disposable services. | Penetration test, egress policy, deployment TLS/certificate review, DNS/proxy controls, server-side access review, and incident response. |
| Prompt injection in memory content | Treat recalled/retained text as untrusted data; do not execute instructions from memory; delimit provenance; test malicious synthetic content cannot alter routing/tool selection or authorize an action. | Red-team agent/tool chains, model-specific indirect prompt-injection assessment, policy/compliance review. |
| Unauthorized cross-profile reads | Bind every L1/outbox/L2 operation to profile + bank + metadata/tag scope; test alpha cannot recall beta, including L1 miss, backfill, replay, and restart. Fail closed on absent/mismatched identity. | Multi-tenant authorization review, server-side tenant isolation, concurrent/race testing, external penetration test. |
| Outbox data leakage on crash | Restrictive file permissions; no payloads in ordinary logs; inspect WAL/outbox/quarantine after synthetic crash/restart; define retention/compaction and deletion behavior. | At-rest encryption/key management, backup/restore access review, forensic recovery, privacy/retention audit. |
| Replay/revision confusion | Test stable `(profile, bank, mnemosyne_id)` mapping, document replacement, delete/tombstone, async operation IDs, retry/quarantine, and malformed responses against pinned/disposable services. | Distributed concurrency/fault-injection campaign and formal data-loss/privacy review. |
| SSRF/path traversal/config injection | Apply the same enforceable endpoint invariant above to initial URLs and every redirect; reject proxy overrides, alternate IP forms, IPv4-mapped IPv6, any disallowed A/AAAA/CNAME result, userinfo, traversal/control characters, and unsafe profile/bank/path components; bind to validated addresses without losing TLS SNI/hostname verification; test YAML/JSON/config parsing with hostile synthetic inputs. | Full network/application penetration test and deployment-specific egress/DNS/proxy controls. |
| Supply-chain compromise | Hash-pinned lockfile, clean build, dependency audit, SBOM, provenance/signature verification, and artifact diff. | Organization-wide supplier risk, signing-key custody, independent build, incident/revocation exercise. |

These controls are proportional to a source/package publication review. They do not establish that a live Hindsight deployment is secure or that the agent is safe against all prompt-injection classes.

**Data-at-rest boundary:** the package and publish set must contain no real outbox, SQLite database/WAL, logs, backups, memory payloads, or credentials. For synthetic disposable tests, restrictive permissions, crash inspection, retention/compaction, and deletion behavior are package/test obligations. If a deployment uses the provider with real data, the outbox/WAL is a data store: its OS permissions, encryption or compensating controls, backup scope/access, retention/deletion, quarantine copies, and operator access must be specified by the deployment owner. Those controls are not silently “outside scope”; real PII/confidential/regulated data or contractual retention duties trigger the full audit even when the release remains private. Encryption is not required merely to publish source that does not ship data, but it is required or formally risk-accepted according to the real deployment/data classification.

## 3. Package and release integrity

A publish candidate must be built from a clean, pinned checkout in an isolated environment. Required evidence:

1. Source commit, tag, Python version, build backend/tool versions, lockfile hash, and dependency hashes.
2. sdist and wheel hashes; inspect their file lists and install into a fresh environment using hashes.
3. Rebuild from the same source in a second clean environment and compare normalized contents/hashes, or document unavoidable nondeterminism. Reproducibility is practical now; a fully independent build service is deferred.
4. Verify the package imports, registers exactly one Hermes provider, has no install-time network call, and fails closed for missing credentials/config.
5. Generate an SBOM and license report for exactly what is distributed. Test that no `.env`, SQLite/WAL, log, fixture with real content, or local path is present.
6. If/when PyPI is selected, use PyPI Trusted Publishing/OIDC rather than a long-lived upload token where supported ([PyPI Trusted Publishers](https://docs.pypi.org/trusted-publishers/)); protect the release workflow, pin actions, require review, and publish only from a protected tag. Attach checksums and provenance/attestation, and record the source revision, build identity, trust-root checks, and artifact digests. SLSA's level definitions ([SLSA v1.0](https://slsa.dev/spec/v1.0/levels)) are the reference for describing provenance maturity; do not claim a SLSA level without satisfying its requirements.

The current task does **not** publish to PyPI or GitHub. Public package release itself is one of the explicit full-audit triggers below, even if these basic integrity checks have already passed.

## 4. Precise full-audit trigger

Escalate from this practical review to a formal security/privacy/license/supply-chain audit on the **first verified occurrence** of any condition below, and do not treat a clean pre-publish gate as a waiver:

For this decision, “private” means an explicitly recorded trust-domain inventory, not merely a private repository, RFC1918 network, or non-public URL. The inventory must name the administering organization/team, permitted hosts and deployment accounts, artifact registries, and CI identities/service accounts (including the exact repository/workflow identities allowed to build or publish). A host, team, CI identity, registry, or operator outside that inventory is external. Installation, source/artifact access, build, signing, or publication involving any such external party is an external-distribution/security-boundary event and triggers the formal audit; a secret or network path that happens to remain private does not make it internal. The owner must keep this inventory with the release evidence and re-evaluate it whenever hosting, maintainers, CI, or publication targets change.

1. **Production promotion:** the real composite is enabled in any production Hermes profile, points at a live Hindsight service, or writes/reads non-test memory data.
2. **External installation or public package release:** a wheel/sdist is uploaded to PyPI or another public package index, or an artifact/source is distributed for installation, building, signing, or operation by any person, organization, host, registry, CI identity, or trust domain outside the explicit private inventory above. This trigger applies even if the artifact is not publicly indexed.
3. **Multi-tenant/cross-customer use:** more than one independent user, customer, organization, or trust domain can share the service, bank namespace, endpoint, or package deployment. This is an unconditional trigger, including when the artifact is private, the tenants are operated by the same team, or isolation is asserted but not independently audited.
4. **Sensitive-data processing:** the provider handles any real user PII, confidential business data, regulated data, secrets, or memory data subject to contractual retention/deletion obligations. This is an unconditional trigger regardless of repository visibility, network reachability, host ownership, or whether the data is encrypted at rest.
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
6. What outbox/WAL file permissions, encryption or compensating controls, retention, compaction, backup scope, and operator redaction policy apply to each deployment's data classification?
7. Which publication target is intended (private artifact, GitHub release only, or PyPI), and what protected workflow/provenance level is required before that target is enabled?
8. What exact data classification and tenant model will exist at promotion time? This answer selects the formal privacy/compliance audit scope.

## Concise recommendation for issue #9

Proceed with harden + publish only after a bounded, reproducible gate: implement package metadata and a lockfile; run version-pinned Ruff, Bandit, focused Semgrep, tests, `pip-audit`, CycloneDX SBOM, license inventory, Gitleaks/history scan, artifact-content/publish-set scan, and the synthetic threat matrix; verify all upstream pins, trust-root evidence, and the final chosen license/notices; and produce clean wheel/sdist/reproducibility evidence. The current fake/stdlib prototype has no third-party runtime dependency and no package license manifest, so it cannot yet satisfy that gate. Defer a formal audit, but make it mandatory on the first production enablement, external installation/public package release, multi-tenant use, real PII/confidential-memory processing, security-boundary expansion, material supply-chain/license finding, or incident.
