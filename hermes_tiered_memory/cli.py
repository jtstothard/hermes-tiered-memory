"""Command-line interface for hermes-tiered-memory."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts.generate_protected_config import (
    _parse_rfc3339,
    _write_file_strict,
    build_signed_config,
    generate_ed25519_keypair,
)


def _yaml_scalar(value: str) -> str:
    """Quote a YAML scalar using JSON's safe double-quoted string form."""
    return json.dumps(value, ensure_ascii=False)


def _memory_tiered_patch(
    *, key_id: str, public_key_b64: str, auth_mode: str, config_path: Path, signature_path: Path
) -> bytes:
    """Return a deterministic, reviewable YAML fragment for memory.tiered."""
    lines = [
        "memory:",
        "  provider: \"tiered\"",
        "  tiered:",
        "    hindsight:",
        f"      auth_mode: {_yaml_scalar(auth_mode)}",
        "    verification_keys:",
        "      - key_id: " + _yaml_scalar(key_id),
        "        algorithm: \"ed25519\"",
        "        public_key_b64: " + _yaml_scalar(public_key_b64),
        "    protected_config:",
        "      path: " + _yaml_scalar(str(config_path.resolve())),
        "      signature:",
        "        path: " + _yaml_scalar(str(signature_path.resolve())),
        "",
    ]
    return "\n".join(lines).encode("utf-8")


def _provision(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    private_path = Path(args.private_key) if args.private_key else output_dir / f"{args.key_id}.private.pem"
    config_path = Path(args.config) if args.config else output_dir / "protected-config.json"
    signature_path = Path(args.signature) if args.signature else output_dir / "protected-config.sig"
    patch_path = Path(args.patch) if args.patch else output_dir / "memory.tiered.patch.yaml"
    output_paths = (private_path, config_path, signature_path, patch_path)
    if len(set(output_paths)) != len(output_paths):
        raise SystemExit("provision output paths must be distinct")
    if not args.force:
        existing = [str(path) for path in output_paths if path.exists()]
        if existing:
            raise SystemExit(
                "refusing to overwrite existing file(s): " + ", ".join(existing) + " (use --force)"
            )

    private_key, private_pem, public_key_b64 = generate_ed25519_keypair()
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
        port=args.port,
        base_path=args.base_path,
        sequence=args.sequence,
        issued_at=issued_at,
        not_before=not_before,
        expires_at=expires_at,
        ca_file=args.ca_file,
    )
    patch = _memory_tiered_patch(
        key_id=args.key_id,
        public_key_b64=public_key_b64,
        auth_mode=args.auth_mode,
        config_path=config_path,
        signature_path=signature_path,
    )
    # No secret is accepted or consulted here. In particular, api_key mode only
    # records the runtime auth policy; the cloud credential remains Hermes-owned.
    for path, data in (
        (private_path, private_pem),
        (config_path, canonical),
        (signature_path, signature),
        (patch_path, patch),
    ):
        _write_file_strict(path, data, force=args.force)
        os.chmod(path, 0o600)
    print(f"private key written to: {private_path} (mode 0600)")
    print(f"protected config written to: {config_path} (mode 0600)")
    print(f"detached signature written to: {signature_path} (mode 0600)")
    print(f"reviewable Hermes patch written to: {patch_path} (mode 0600)")
    print(f"public_key_b64: {public_key_b64}")
    return 0


def main(argv: list[str] | None = None) -> int:
    """Entry point for the ``hermes-tiered-memory`` console script."""
    parser = argparse.ArgumentParser(
        prog="hermes-tiered-memory",
        description="hermes-tiered-memory — provision and diagnose the tiered memory plugin.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    provision = subparsers.add_parser(
        "provision",
        help="Provision L1/L2 backends and generate verification keys.",
    )
    provision.add_argument("--output-dir", default=".")
    provision.add_argument("--key-id", required=True)
    provision.add_argument("--config-id", required=True)
    provision.add_argument("--host", required=True)
    provision.add_argument("--port", type=int, default=443)
    provision.add_argument("--base-path", default="/api/v1")
    provision.add_argument("--sequence", type=int, default=1)
    provision.add_argument("--issued-at")
    provision.add_argument("--not-before")
    provision.add_argument("--expires-at")
    provision.add_argument("--ca-file")
    provision.add_argument("--auth-mode", choices=("api_key", "none"), default="api_key")
    provision.add_argument("--private-key")
    provision.add_argument("--config")
    provision.add_argument("--signature")
    provision.add_argument("--patch")
    provision.add_argument("--force", action="store_true")
    provision.set_defaults(func=_provision)
    subparsers.add_parser(
        "doctor",
        help="Run health checks against installed backends and config.",
    )

    args = parser.parse_args(argv)

    if args.command == "provision":
        return args.func(args)
    elif args.command == "doctor":
        print("doctor: not yet implemented")
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
