#!/usr/bin/env python3
"""Provisioning helper for protected Hindsight endpoint configuration.

Generates Ed25519 keypairs and mints canonical JCS protected configs with
detached Ed25519 signatures, so an operator can provision signed configs
without hand-rolling canonicalization.

Subcommands
-----------
genkey  Generate an Ed25519 keypair, write the private key to a PEM file
        (mode 0600), and print the base64-encoded public key for the
        ``tiered.verification_keys`` config block.

sign    Build the canonical protected config JSON from endpoint details,
        sign it with an Ed25519 private key, and write the config and the
        detached signature to two files.

verify  Re-load a config + signature through ProtectedConfigLoader after
        registering a public key from a config-block entry, proving the
        round-trip.

This script depends on the ``cryptography`` package (already a project
dependency). It writes files with mode 0600 and refuses to overwrite
existing files unless --force is given.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519
import cryptography.hazmat.primitives.serialization as serialization

# Allow running from a source checkout without installing the package.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from hermes_tiered_memory.config import ProtectedConfigLoader  # noqa: E402


def generate_ed25519_keypair() -> tuple[ed25519.Ed25519PrivateKey, bytes, str]:
    """Return a private key, PEM encoding, and base64 raw public key."""
    private_key = ed25519.Ed25519PrivateKey.generate()
    pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, pem, base64.b64encode(public_bytes).decode("ascii")


def build_signed_config(
    private_key: ed25519.Ed25519PrivateKey,
    *,
    key_id: str,
    config_id: str,
    host: str,
    port: int = 443,
    base_path: str = "/api/v1",
    sequence: int = 1,
    issued_at: str,
    not_before: str,
    expires_at: str,
    ca_file: str | None = None,
) -> tuple[bytes, bytes]:
    """Build canonical protected-config bytes and its detached signature."""
    payload = {
        "schema_version": 1,
        "key_id": key_id,
        "config_id": config_id,
        "sequence": sequence,
        "issued_at": issued_at,
        "not_before": not_before,
        "expires_at": expires_at,
        "hindsight": {
            "scheme": "https",
            "host": host,
            "port": port,
            "base_path": base_path,
            "tls": {"verify": True, "ca_file": ca_file},
            "proxy": {"enabled": False},
        },
    }
    canonical = json.dumps(
        payload, separators=(",", ":"), sort_keys=True, ensure_ascii=False
    ).encode("utf-8")
    return canonical, private_key.sign(ProtectedConfigLoader.DOMAIN_SEPARATOR + canonical)


def _write_file_strict(path: Path, data: bytes, *, force: bool) -> None:
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing file: {path} (use --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
    # O_CREAT | O_EXCL avoids a TOCTOU overwrite race when force is not set.
    # O_NOFOLLOW rejects symlinks at the output path on both paths, preventing
    # a forced write from following a symlink to an unintended target.
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    if not force:
        flags |= os.O_EXCL
    fd = os.open(path, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
    except Exception:
        path.unlink(missing_ok=True)
        raise


def _parse_rfc3339(value: str) -> str:
    """Validate and normalize an RFC 3339 UTC timestamp string."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise SystemExit(f"invalid RFC 3339 timestamp {value!r}: {e}") from e
    if dt.tzinfo is None:
        raise SystemExit(f"timestamp must include a UTC offset: {value!r}")
    return dt.astimezone(timezone.utc).isoformat()


def cmd_genkey(args: argparse.Namespace) -> int:
    private_key, pem, public_b64 = generate_ed25519_keypair()
    out = Path(args.out) if args.out else Path(f"{args.key_id}.private.pem")
    _write_file_strict(out, pem, force=args.force)
    os.chmod(out, 0o600)


    print(f"private key written to: {out} (mode 0600)")
    print()
    print("Add this entry to tiered.verification_keys:")
    print(f'  - key_id: "{args.key_id}"')
    print(f'    algorithm: "ed25519"')
    print(f'    public_key_b64: "{public_b64}"')
    return 0


def cmd_sign(args: argparse.Namespace) -> int:
    private_pem = Path(args.private_key).read_bytes()
    private_key = serialization.load_pem_private_key(private_pem, password=None)
    if not isinstance(private_key, ed25519.Ed25519PrivateKey):
        raise SystemExit("private key is not an Ed25519 key")

    not_before = _parse_rfc3339(args.not_before) if args.not_before else datetime.now(timezone.utc).isoformat()
    expires_at = _parse_rfc3339(args.expires_at) if args.expires_at else (
        datetime.now(timezone.utc) + timedelta(days=365)
    ).isoformat()
    issued_at = _parse_rfc3339(args.issued_at) if args.issued_at else not_before

    canonical, signature = build_signed_config(
        private_key,
        key_id=args.key_id,
        config_id=args.config_id,
        host=args.host,
        port=int(args.port),
        base_path=args.base_path,
        sequence=int(args.sequence),
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        ca_file=args.ca_file or None,
    )

    out_path = Path(args.out)
    sig_path = Path(args.sig)
    _write_file_strict(out_path, canonical, force=args.force)
    _write_file_strict(sig_path, signature, force=args.force)
    os.chmod(out_path, 0o600)
    os.chmod(sig_path, 0o600)
    print(f"config written to:     {out_path} (mode 0600)")
    print(f"signature written to:  {sig_path} (mode 0600)")
    print(f"key_id:                {args.key_id}")
    print(f"config_id:             {args.config_id}")
    print(f"sequence:              {args.sequence}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    """Round-trip verify: register a public key, then load the signed config."""
    public_b64 = args.public_key_b64
    if public_b64 is None and args.public_key_file:
        # Expect the file to contain just the base64 string.
        public_b64 = Path(args.public_key_file).read_text().strip()
    if not public_b64:
        raise SystemExit("provide --public-key-b64 or --public-key-file")
    ProtectedConfigLoader.clear_keys()
    ProtectedConfigLoader.register_verification_keys([
        {"key_id": args.key_id, "algorithm": "ed25519", "public_key_b64": public_b64}
    ])
    loader = ProtectedConfigLoader(Path(args.config), Path(args.sig))
    config = loader.load()
    print(f"OK: loaded {config.config_id} (key_id={config.key_id}, sequence={config.sequence})")
    print(f"    endpoint: {config.hindsight.to_url()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    p_genkey = sub.add_parser("genkey", help="Generate an Ed25519 keypair")
    p_genkey.add_argument("--key-id", required=True, help="key_id for the verification_keys block")
    p_genkey.add_argument("--out", help="private key PEM output path (default: <key_id>.private.pem)")
    p_genkey.add_argument("--force", action="store_true", help="overwrite existing files")
    p_genkey.set_defaults(func=cmd_genkey)

    p_sign = sub.add_parser("sign", help="Mint a canonical config + detached Ed25519 signature")
    p_sign.add_argument("--private-key", required=True, help="Ed25519 private key PEM path")
    p_sign.add_argument("--key-id", required=True)
    p_sign.add_argument("--config-id", required=True)
    p_sign.add_argument("--host", required=True, help="Hindsight hostname (HTTPS)")
    p_sign.add_argument("--port", default="443")
    p_sign.add_argument("--base-path", default="/api/v1")
    p_sign.add_argument("--sequence", default="1")
    p_sign.add_argument("--issued-at", default=None, help="RFC 3339 UTC (default: same as not-before)")
    p_sign.add_argument("--not-before", default=None, help="RFC 3339 UTC (default: now)")
    p_sign.add_argument("--expires-at", default=None, help="RFC 3339 UTC (default: now + 365d)")
    p_sign.add_argument("--ca-file", default=None, help="optional CA bundle path")
    p_sign.add_argument("--out", required=True, help="config JSON output path")
    p_sign.add_argument("--sig", required=True, help="signature output path")
    p_sign.add_argument("--force", action="store_true", help="overwrite existing files")
    p_sign.set_defaults(func=cmd_sign)

    p_verify = sub.add_parser("verify", help="Round-trip verify a signed config against a pinned public key")
    p_verify.add_argument("--config", required=True)
    p_verify.add_argument("--sig", required=True)
    p_verify.add_argument("--key-id", required=True)
    p_verify.add_argument("--public-key-b64", default=None)
    p_verify.add_argument("--public-key-file", default=None)
    p_verify.set_defaults(func=cmd_verify)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
