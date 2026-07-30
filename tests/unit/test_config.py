"""Tests for protected configuration loading."""

import hashlib
import hmac
import json
import tempfile
from pathlib import Path

import pytest

from hermes_tiered_memory.config import (
    ProtectedConfigFreshnessError,
    ProtectedConfigLoader,
    ProtectedConfigValidationError,
)


@pytest.fixture(autouse=True)
def clear_pinned_keys():
    ProtectedConfigLoader.clear_keys()
    yield
    ProtectedConfigLoader.clear_keys()


def _write_signed_config(directory, data):
    secret = b"test-secret"
    ProtectedConfigLoader.register_key("test-key", secret)
    path = Path(directory) / "config.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    path.chmod(0o600)
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature_path = Path(directory) / "config.sig"
    signature_path.write_bytes(
        hmac.new(ProtectedConfigLoader.DOMAIN_SEPARATOR + secret, payload, hashlib.sha256).digest()
    )
    signature_path.chmod(0o600)
    return path, signature_path


def _config(sequence=1, expires_at="2099-01-01T00:00:00+00:00"):
    return {
        "schema_version": 1,
        "key_id": "test-key",
        "config_id": "test-config",
        "sequence": sequence,
        "issued_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "expires_at": expires_at,
        "hindsight": {
            "scheme": "https",
            "host": "hindsight.example.com",
            "port": 443,
            "base_path": "/api/v1",
            "tls": {"verify": True, "ca_file": None},
            "proxy": {"enabled": False},
        },
    }


def test_load_valid_config():
    with tempfile.TemporaryDirectory() as directory:
        path, signature_path = _write_signed_config(directory, _config())
        loader = ProtectedConfigLoader(path, signature_path)
        config = loader.load()
        assert config.config_id == "test-config"
        assert config.hindsight.to_url() == "https://hindsight.example.com:443/api/v1"


def test_reject_duplicate_keys():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        path.write_text('{"schema_version":1,"schema_version":1}', encoding="utf-8")
        with pytest.raises(ProtectedConfigValidationError):
            ProtectedConfigLoader(path).load()


def test_reject_proxy():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "config.json"
        data = _config()
        data["hindsight"]["proxy"]["enabled"] = True
        path.write_text(json.dumps(data), encoding="utf-8")
        with pytest.raises(ProtectedConfigValidationError):
            ProtectedConfigLoader(path).load()


def test_same_sequence_changed_payload_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path, signature_path = _write_signed_config(directory, _config())
        loader = ProtectedConfigLoader(path, signature_path)
        loader.load()
        data = _config()
        data["hindsight"]["host"] = "other.example.com"
        _write_signed_config(directory, data)
        with pytest.raises(ProtectedConfigFreshnessError):
            loader.load()