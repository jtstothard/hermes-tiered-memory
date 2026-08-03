"""Command-line interface for hermes-tiered-memory."""

from __future__ import annotations

import argparse
import base64
import binascii
import importlib.util
import json
import os
import sys
import tempfile
from datetime import UTC, datetime, timedelta
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

import httpx

from hermes_tiered_memory.config import ProtectedConfigLoader
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
        '  provider: "tiered"',
        "  tiered:",
        "    hindsight:",
        f"      auth_mode: {_yaml_scalar(auth_mode)}",
        "    verification_keys:",
        "      - key_id: " + _yaml_scalar(key_id),
        '        algorithm: "ed25519"',
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
    private_path = (
        Path(args.private_key) if args.private_key else output_dir / f"{args.key_id}.private.pem"
    )
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
    not_before = (
        _parse_rfc3339(args.not_before) if args.not_before else datetime.now(UTC).isoformat()
    )
    expires_at = (
        _parse_rfc3339(args.expires_at)
        if args.expires_at
        else (datetime.now(UTC) + timedelta(days=365)).isoformat()
    )
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


def _module_available(module_name: str) -> bool:
    """Return whether an optional runtime module can be imported."""
    return importlib.util.find_spec(module_name) is not None


def _check_optional_extra(label: str, module_name: str) -> tuple[bool, str]:
    distribution = {"mnemosyne": "mnemosyne-memory", "hindsight": "hindsight-client"}[module_name]
    try:
        version(distribution)
    except PackageNotFoundError:
        if not _module_available(module_name):
            return False, f"{label}: unavailable (install the [{module_name}] extra)"
    return True, f"{label}: installed"


def _parse_verification_key(value: str) -> dict[str, str]:
    """Parse a CLI key reference without including key material in errors."""
    key_id, separator, material = value.partition("=")
    if not separator or not key_id or not material or key_id != key_id.strip():
        raise ValueError("expected KEY_ID=BASE64_PUBLIC_KEY")
    try:
        decoded = base64.b64decode(material, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise ValueError("verification key is not valid base64") from exc
    if len(decoded) != 32:
        raise ValueError("Ed25519 verification key must decode to 32 bytes")
    return {"key_id": key_id, "algorithm": "ed25519", "public_key_b64": material}


def _check_protected_config(
    config_path: str | None, signature_path: str | None, verification_keys: list[str]
) -> tuple[bool, str]:
    if not config_path or not signature_path:
        return False, "protected config: missing --config or --signature"
    try:
        entries = [_parse_verification_key(value) for value in verification_keys]
        if not entries:
            raise ValueError("at least one --verification-key is required")
        # Normal startup persists replay state. Doctor uses isolated state so it
        # cannot mutate the operator's live config or replay ledger.
        original_keys = ProtectedConfigLoader._pinned_keys.copy()
        try:
            ProtectedConfigLoader.clear_keys()
            ProtectedConfigLoader.register_verification_keys(entries)
            with tempfile.TemporaryDirectory(prefix="hermes-tiered-memory-doctor-") as isolated:
                loader = ProtectedConfigLoader(
                    Path(config_path), Path(signature_path), Path(isolated) / "replay-state.json"
                )
                loader.load()
        finally:
            ProtectedConfigLoader._pinned_keys = original_keys
    except Exception:
        # Exception text can contain paths, endpoint details, or supplied key
        # material. Keep the operator-facing failure stable and non-sensitive.
        return False, "protected config: invalid signature, schema, permissions, or expired config"
    return True, "protected config: valid signature and unexpired"


def _check_l2_reachability(url: str, timeout_seconds: float) -> tuple[bool, str]:
    """Probe only /health; no credentials are sent or reported."""
    try:
        parsed = httpx.URL(url)
        if parsed.scheme not in {"http", "https"} or not parsed.host:
            return False, "invalid endpoint URL"
        response = httpx.get(str(parsed).rstrip("/") + "/health", timeout=timeout_seconds)
        if 200 <= response.status_code < 300:
            return True, "reachable"
        return False, f"returned HTTP {response.status_code}"
    except (httpx.HTTPError, ValueError):
        return False, "endpoint unreachable"


def _doctor(args: argparse.Namespace) -> int:
    """Run deterministic, read-only checks and fail if any check fails."""
    failures = 0
    for label, module_name in (
        ("optional extra: mnemosyne", "mnemosyne"),
        ("optional extra: hindsight", "hindsight"),
    ):
        passed, detail = _check_optional_extra(label, module_name)
        print(f"{'PASS' if passed else 'FAIL'} {detail}")
        failures += not passed

    passed, detail = _check_protected_config(args.config, args.signature, args.verification_key)
    print(f"{'PASS' if passed else 'FAIL'} {detail}")
    failures += not passed

    l1_available = _module_available("mnemosyne")
    l1_detail = "available" if l1_available else "unavailable (install the [mnemosyne] extra)"
    print(f"{'PASS' if l1_available else 'FAIL'} L1: {l1_detail}")
    failures += not l1_available

    if args.l2_url is None:
        print("SKIP L2: no endpoint configured")
    else:
        reachable, detail = _check_l2_reachability(args.l2_url, args.l2_timeout)
        print(f"{'PASS' if reachable else 'FAIL'} L2: {detail}")
        failures += not reachable
    return 1 if failures else 0


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
    doctor = subparsers.add_parser(
        "doctor",
        help="Run health checks against installed backends and config.",
    )
    doctor.add_argument("--config", help="protected Hindsight config JSON")
    doctor.add_argument("--signature", help="detached protected-config signature")
    doctor.add_argument(
        "--verification-key",
        action="append",
        default=[],
        metavar="KEY_ID=BASE64_PUBLIC_KEY",
        help="pinned Ed25519 public key (repeatable; key material is never reported)",
    )
    doctor.add_argument("--l2-url", help="optional Hindsight endpoint to probe")
    doctor.add_argument("--l2-timeout", type=float, default=5.0)
    doctor.set_defaults(func=_doctor)

    args = parser.parse_args(argv)

    if args.command == "provision" or args.command == "doctor":
        return args.func(args)

    parser.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
