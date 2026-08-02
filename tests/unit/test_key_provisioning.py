"""Tests for the production verification-key provisioning seam.

These tests exercise ``ProtectedConfigLoader.register_verification_keys()``,
the only production path that populates ``_pinned_keys``. They use Ed25519
(see ``cryptography``) for the fixtures and keep the existing HMAC tests in
``test_config.py`` untouched.

Fail-closed invariants verified here:
  - unknown key_id is rejected
  - missing signature is rejected
  - empty verification_keys + a signed config fails with Unauthorized key_id
  - malformed key material (wrong length, bad base64, unknown algorithm,
    duplicate key_id) fails closed
  - the seam does not read keys from environment variables
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from pathlib import Path

import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519
import cryptography.hazmat.primitives.serialization as serialization
import pytest

from hermes_tiered_memory.config import (
    ProtectedConfigAuthError,
    ProtectedConfigLoader,
    ProtectedConfigValidationError,
)


@pytest.fixture(autouse=True)
def clear_pinned_keys():
    ProtectedConfigLoader.clear_keys()
    yield
    ProtectedConfigLoader.clear_keys()


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch):
    """Ensure no HERMES_* key env vars can influence the seam."""
    for key in list(os.environ):
        if "VERIFICATION" in key.upper() or "PINNED" in key.upper():
            monkeypatch.delenv(key, raising=False)


# --- helpers ---------------------------------------------------------------


def _ed25519_keypair() -> tuple[ed25519.Ed25519PrivateKey, bytes]:
    private_key = ed25519.Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return private_key, public_bytes


def _config(key_id: str = "admin-ed25519-1") -> dict:
    return {
        "schema_version": 1,
        "key_id": key_id,
        "config_id": "provisioned-config",
        "sequence": 1,
        "issued_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "hindsight": {
            "scheme": "https",
            "host": "hindsight.example.com",
            "port": 443,
            "base_path": "/api/v1",
            "tls": {"verify": True, "ca_file": None},
            "proxy": {"enabled": False},
        },
    }


def _write_config_and_ed25519_sig(directory: Path, data: dict, private_key: ed25519.Ed25519PrivateKey):
    """Write canonical config + Ed25519 detached signature, return (config_path, sig_path)."""
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8")
    config_path = Path(directory) / "protected.json"
    config_path.write_bytes(payload)
    config_path.chmod(0o600)
    signature = private_key.sign(ProtectedConfigLoader.DOMAIN_SEPARATOR + payload)
    sig_path = Path(directory) / "protected.sig"
    sig_path.write_bytes(signature)
    sig_path.chmod(0o600)
    return config_path, sig_path


def _ed25519_entry(key_id: str, public_bytes: bytes) -> dict:
    return {
        "key_id": key_id,
        "algorithm": "ed25519",
        "public_key_b64": base64.b64encode(public_bytes).decode("ascii"),
    }


def _hmac_entry(key_id: str, secret: bytes) -> dict:
    return {
        "key_id": key_id,
        "algorithm": "hmac-sha-256",
        "secret_b64": base64.b64encode(secret).decode("ascii"),
    }


# --- happy path: seam registers keys and a signed config loads --------------


def test_register_verification_keys_ed25519_loads_end_to_end(tmp_path):
    """A signed Ed25519 config loads via the config block with no register_key() call."""
    private_key, public_bytes = _ed25519_keypair()

    # Register via the production seam (no test-only register_key)
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("admin-ed25519-1", public_bytes)])
    assert "admin-ed25519-1" in ProtectedConfigLoader._pinned_keys

    config_path, sig_path = _write_config_and_ed25519_sig(tmp_path, _config(), private_key)
    config = ProtectedConfigLoader(config_path, sig_path).load()

    assert config.config_id == "provisioned-config"
    assert config.key_id == "admin-ed25519-1"
    assert config.hindsight.to_url() == "https://hindsight.example.com:443/api/v1"


def test_register_verification_keys_populates_pinned_keys():
    private_key, public_bytes = _ed25519_keypair()
    ProtectedConfigLoader.clear_keys()
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("k1", public_bytes)])

    assert "k1" in ProtectedConfigLoader._pinned_keys
    assert ProtectedConfigLoader._pinned_keys["k1"] == (public_bytes,)


def test_register_verification_keys_supports_hmac():
    ProtectedConfigLoader.register_verification_keys([_hmac_entry("hmac-1", b"secret-material")])
    assert ProtectedConfigLoader._pinned_keys["hmac-1"] == b"secret-material"


def test_register_multiple_keys():
    private_key, public_bytes = _ed25519_keypair()
    entries = [
        _ed25519_entry("ed25519-1", public_bytes),
        _hmac_entry("hmac-1", b"another-secret"),
    ]
    ProtectedConfigLoader.register_verification_keys(entries)
    assert set(ProtectedConfigLoader._pinned_keys) == {"ed25519-1", "hmac-1"}


# --- fail-closed: empty / absent verification_keys --------------------------


def test_empty_verification_keys_then_signed_config_fails_unauthorized(tmp_path):
    """An empty verification_keys list preserves fail-closed: no key is pinned."""
    ProtectedConfigLoader.register_verification_keys([])  # registers nothing
    assert ProtectedConfigLoader._pinned_keys == {}

    private_key, _ = _ed25519_keypair()
    config_path, sig_path = _write_config_and_ed25519_sig(tmp_path, _config(), private_key)

    with pytest.raises(ProtectedConfigAuthError, match="Unauthorized key_id"):
        ProtectedConfigLoader(config_path, sig_path).load()


# --- fail-closed: malformed key material ------------------------------------


@pytest.mark.parametrize(
    "entry, match",
    [
        # wrong length (31 bytes, not 32)
        (
            {
                "key_id": "bad-len",
                "algorithm": "ed25519",
                "public_key_b64": base64.b64encode(b"x" * 31).decode("ascii"),
            },
            "must be 32 bytes",
        ),
        # bad base64
        (
            {"key_id": "bad-b64", "algorithm": "ed25519", "public_key_b64": "not!!valid!!b64"},
            "Invalid base64",
        ),
        # unknown algorithm
        (
            {"key_id": "bad-alg", "algorithm": "rsa-4096", "public_key_b64": base64.b64encode(b"x" * 32).decode()},
            "Unsupported verification key algorithm",
        ),
        # missing public_key_b64 for ed25519
        (
            {"key_id": "no-material", "algorithm": "ed25519"},
            "requires public_key_b64",
        ),
        # missing secret_b64 for hmac
        (
            {"key_id": "no-secret", "algorithm": "hmac-sha-256"},
            "requires secret_b64",
        ),
        # empty key_id
        (
            {"key_id": "  ", "algorithm": "ed25519", "public_key_b64": base64.b64encode(b"x" * 32).decode()},
            "non-empty key_id",
        ),
    ],
)
def test_malformed_entry_fails_closed(entry, match):
    with pytest.raises(ProtectedConfigValidationError, match=match):
        ProtectedConfigLoader.register_verification_keys([entry])
    # Nothing should have been registered
    assert ProtectedConfigLoader._pinned_keys == {}


def test_duplicate_key_id_within_call_fails_closed():
    private_key, public_bytes = _ed25519_keypair()
    entries = [
        _ed25519_entry("dup", public_bytes),
        _ed25519_entry("dup", public_bytes),
    ]
    with pytest.raises(ProtectedConfigValidationError, match="Duplicate verification key_id"):
        ProtectedConfigLoader.register_verification_keys(entries)
    # Registration is atomic: the first (otherwise-valid) entry is NOT registered.
    assert ProtectedConfigLoader._pinned_keys == {}


def test_malformed_entry_does_not_register_valid_siblings():
    """Atomicity: a bad entry rolls back all siblings in the same call."""
    private_key, public_bytes = _ed25519_keypair()
    entries = [
        _ed25519_entry("valid-sibling", public_bytes),
        {"key_id": "bad", "algorithm": "rsa"},
    ]
    with pytest.raises(ProtectedConfigValidationError):
        ProtectedConfigLoader.register_verification_keys(entries)
    assert ProtectedConfigLoader._pinned_keys == {}


def test_duplicate_key_id_across_calls_fails_closed():
    private_key, public_bytes = _ed25519_keypair()
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("dup", public_bytes)])
    with pytest.raises(ProtectedConfigValidationError, match="Duplicate verification key_id"):
        ProtectedConfigLoader.register_verification_keys([_ed25519_entry("dup", public_bytes)])


def test_non_list_entries_rejected():
    with pytest.raises(ProtectedConfigValidationError, match="must be a list"):
        ProtectedConfigLoader.register_verification_keys({"key_id": "x"})  # type: ignore[arg-type]


def test_non_mapping_entry_rejected():
    with pytest.raises(ProtectedConfigValidationError, match="must be a mapping"):
        ProtectedConfigLoader.register_verification_keys(["not-a-mapping"])  # type: ignore[list-item]


# --- fail-closed: wrong key signs the config --------------------------------


def test_config_signed_by_unregistered_key_fails(tmp_path):
    """A config whose key_id is not in the verification_keys list fails closed."""
    private_key_a, _ = _ed25519_keypair()
    # Register a DIFFERENT key than the one that signed
    private_key_b, public_b = _ed25519_keypair()
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("registered-key", public_b)])

    data = _config(key_id="unregistered-key")
    config_path, sig_path = _write_config_and_ed25519_sig(tmp_path, data, private_key_a)

    with pytest.raises(ProtectedConfigAuthError, match="Unauthorized key_id"):
        ProtectedConfigLoader(config_path, sig_path).load()


def test_config_signed_with_wrong_key_fails_signature_check(tmp_path):
    """key_id is registered but the signature was made by a different private key."""
    private_key_signer, _ = _ed25519_keypair()
    private_key_registered, public_registered = _ed25519_keypair()
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("admin-ed25519-1", public_registered)])

    config_path, sig_path = _write_config_and_ed25519_sig(tmp_path, _config(), private_key_signer)

    with pytest.raises(ProtectedConfigAuthError, match="Invalid ed25519 signature"):
        ProtectedConfigLoader(config_path, sig_path).load()


def test_missing_signature_fails(tmp_path):
    private_key, public_bytes = _ed25519_keypair()
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("admin-ed25519-1", public_bytes)])
    config_path, _ = _write_config_and_ed25519_sig(tmp_path, _config(), private_key)

    with pytest.raises(ProtectedConfigAuthError, match="Signature is required"):
        ProtectedConfigLoader(config_path, None).load()


# --- rollback / freshness still enforced with provisioned keys --------------


def test_rollback_to_lower_sequence_rejected(tmp_path):
    private_key, public_bytes = _ed25519_keypair()
    ProtectedConfigLoader.register_verification_keys([_ed25519_entry("admin-ed25519-1", public_bytes)])

    # Accept sequence 2
    data = _config()
    data["sequence"] = 2
    config_path, sig_path = _write_config_and_ed25519_sig(tmp_path, data, private_key)
    ProtectedConfigLoader(config_path, sig_path).load()

    # Replay sequence 1 -> rejected
    data = _config()
    data["sequence"] = 1
    config_path, sig_path = _write_config_and_ed25519_sig(tmp_path, data, private_key)
    with pytest.raises(Exception, match="less than accepted"):
        ProtectedConfigLoader(config_path, sig_path).load()


# --- the seam must NOT read from environment variables ---------------------


def test_seam_ignores_verification_env_vars(monkeypatch, tmp_path):
    """Environment variables must not supply verification keys (config block only)."""
    monkeypatch.setenv("HERMES_VERIFICATION_KEY_ID", "env-injected")
    monkeypatch.setenv("HERMES_VERIFICATION_KEY", base64.b64encode(b"x" * 32).decode())
    monkeypatch.setenv("TIERED_VERIFICATION_KEYS", json.dumps([{"key_id": "env", "algorithm": "ed25519"}]))

    # The seam only consults the explicit list argument; env is irrelevant.
    ProtectedConfigLoader.register_verification_keys([])
    assert ProtectedConfigLoader._pinned_keys == {}
    assert "env-injected" not in ProtectedConfigLoader._pinned_keys
    assert "env" not in ProtectedConfigLoader._pinned_keys


# --- register_key (test-only) still works alongside the seam ---------------


def test_register_key_still_works_for_tests():
    """The test-only register_key() path must remain functional."""
    private_key, public_bytes = _ed25519_keypair()
    ProtectedConfigLoader.register_key("test-only", (public_bytes,))
    assert ProtectedConfigLoader._pinned_keys["test-only"] == (public_bytes,)
