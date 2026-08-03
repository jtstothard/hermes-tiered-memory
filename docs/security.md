# Security

This document describes the security properties implemented by v1.0.0. It does
not contain secrets, live endpoints, user data, or estate-specific filesystem
paths.

## Protected configuration

The endpoint document is canonical compact JSON: object keys are sorted, there
is no insignificant whitespace, duplicate object keys are rejected, and floats
and non-finite numbers are rejected. The implementation intentionally claims
this restricted canonical JSON format rather than full RFC 8785 number
canonicalization.

The detached signature covers:

```text
hermes-tiered-memory/config/v1\0 || exact-canonical-config-bytes
```

Supported signature algorithms are Ed25519 and HMAC-SHA-256. Ed25519 is the
provisioning CLI's default and is recommended for operator-managed
configuration. Verification keys are explicitly pinned in
`memory.tiered.verification_keys`; environment variables and the test-only
`register_key()` helper are not production key sources.

Each key entry has a unique `key_id`, an `algorithm`, and either
`public_key_b64` (Ed25519, exactly 32 decoded bytes) or `secret_b64` (HMAC,
non-empty decoded bytes). Registration is atomic: malformed entries or
conflicting duplicate IDs fail without partially registering the list.

The signed document includes schema/config identity, a monotonically increasing
sequence, issuance/not-before/expiry timestamps, and an HTTPS Hindsight
endpoint. The loader rejects unknown keys, missing or invalid signatures,
non-canonical bytes, invalid schema, expired/not-yet-valid documents, and
sequence rollback. Accepted sequence and digest state is persisted separately
so a previously accepted configuration cannot silently roll back.

## File protection

Configuration, signature, replay-state, and generated private-key files must be
regular files owned by root or the current service identity, must be readable,
and must not be group- or world-writable. Symlinks are rejected. The
provisioning CLI creates output files with mode `0600`, refuses overwrites
unless `--force` is explicit, and uses no-follow file creation.

The Ed25519 private key is signing material. Keep it offline or in an approved
secret store; do not put it in the Hermes config, wheel, repository, issue,
log, or generated public patch. The public verification key may be included in
profile configuration.

## Secret boundary and authentication modes

`memory.tiered.hindsight.auth_mode` accepts only:

- `api_key`: the active Hermes profile must contain a non-empty
  `profiles.<active-profile>.secrets.hindsight.api_key`. The value is passed to
  the Hindsight adapter as a bearer credential and is never copied into the
  protected endpoint document or provisioning output.
- `none`: no Hindsight API key may be present in the active profile. The adapter
  sends no authorization header. Use only with an endpoint whose access policy
  intentionally requires no API key.

Missing credentials, stray credentials in `none` mode, blank-padded keys, or an
unknown mode fail closed during initialization. `provision` does not read
credentials, and `doctor` probes only `/health` without credentials.

## Transport and diagnostics

The live Hindsight adapter requires an absolute HTTP(S) URL, and the protected
configuration schema requires HTTPS. TLS verification is enabled by default;
a configured CA bundle is used to build a verified TLS context. Disabling TLS
verification together with a CA file is rejected.

`doctor` uses isolated replay-state storage for protected-config validation and
keeps exception details, endpoint details, key material, and credentials out
of its stable operator-facing errors. An optional L2 check reports only
reachability/status, never response credentials.

## Release boundary

The wheel is checked in CI for forbidden secret, credential, key, PEM, database,
and environment-file names. Gitleaks scans repository history. These are
release gates, not a claim that deployments are automatically secure: review
key custody, profile permissions, endpoint TLS, and the selected `auth_mode`
before enabling the provider.
