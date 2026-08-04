"""Shared protected-configuration provisioning primitives."""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519
import cryptography.hazmat.primitives.serialization as serialization

from hermes_tiered_memory.config import ProtectedConfigLoader


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
    """Build canonical protected-config bytes and a detached Ed25519 signature."""
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
    """Write private/protected material without following output symlinks."""
    if path.exists() and not force:
        raise SystemExit(f"refusing to overwrite existing file: {path} (use --force)")
    path.parent.mkdir(parents=True, exist_ok=True)
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
    """Validate and normalize an RFC 3339 timestamp string to UTC."""
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"invalid RFC 3339 timestamp {value!r}: {exc}") from exc
    if dt.tzinfo is None:
        raise SystemExit(f"timestamp must include a UTC offset: {value!r}")
    return dt.astimezone(UTC).isoformat()


__all__ = [
    "_parse_rfc3339",
    "_write_file_strict",
    "build_signed_config",
    "generate_ed25519_keypair",
    "timedelta",
]
