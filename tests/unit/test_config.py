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
    path.write_bytes(json.dumps(data, separators=(",", ":"), sort_keys=True, ensure_ascii=False).encode("utf-8"))
    path.chmod(0o600)
    payload = json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8")
    signature_path = Path(directory) / "config.sig"
    signature_path.write_bytes(
        hmac.new(secret, ProtectedConfigLoader.DOMAIN_SEPARATOR + payload, hashlib.sha256).digest()
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


def test_same_sequence_same_payload_survives_loader_restart(tmp_path):
    path, signature_path = _write_signed_config(tmp_path, _config())
    ProtectedConfigLoader(path, signature_path).load()
    assert (tmp_path / "config.json.replay-state").is_file()
    assert ProtectedConfigLoader(path, signature_path).load().sequence == 1


def test_lower_sequence_rejected_after_loader_restart(tmp_path):
    path, signature_path = _write_signed_config(tmp_path, _config(sequence=2))
    ProtectedConfigLoader(path, signature_path).load()
    path, signature_path = _write_signed_config(tmp_path, _config(sequence=1))
    with pytest.raises(ProtectedConfigFreshnessError, match="less than accepted"):
        ProtectedConfigLoader(path, signature_path).load()


def test_noncanonical_json_is_rejected(tmp_path):
    secret = b"test-secret"
    ProtectedConfigLoader.register_key("test-key", secret)
    path = tmp_path / "config.json"
    path.write_text(json.dumps(_config(), indent=2), encoding="utf-8")
    path.chmod(0o600)
    payload = path.read_bytes()
    signature = tmp_path / "config.sig"
    signature.write_bytes(hmac.new(secret, ProtectedConfigLoader.DOMAIN_SEPARATOR + payload, hashlib.sha256).digest())
    signature.chmod(0o600)
    with pytest.raises(ProtectedConfigValidationError, match="canonical"):
        ProtectedConfigLoader(path, signature).load()


def test_directory_config_is_rejected(tmp_path):
    config = tmp_path / "config.json"
    config.mkdir()
    with pytest.raises(ProtectedConfigValidationError, match="regular file"):
        ProtectedConfigLoader(config).load()


@pytest.mark.parametrize("path_kind", ["config", "signature", "replay"])
def test_protected_paths_reject_symlinks(tmp_path, path_kind):
    real = tmp_path / "real"
    real.write_bytes(b"not trusted")
    config, signature = _write_signed_config(tmp_path, _config())
    replay = tmp_path / "replay-state"
    replay.write_text('{"config_id":"x","sequence":1,"digest":"x"}')
    target = {"config": config, "signature": signature, "replay": replay}[path_kind]
    link = tmp_path / f"{path_kind}-link"
    target.unlink()
    link.symlink_to(real)
    paths = {
        "config": (link, signature),
        "signature": (config, link),
        "replay": (config, signature),
    }
    with pytest.raises(ProtectedConfigValidationError, match="symlink"):
        ProtectedConfigLoader(*paths[path_kind], replay_state_path=link if path_kind == "replay" else None).load()


@pytest.mark.parametrize("field", ["sequence", "port"])
def test_bool_is_not_accepted_as_integer(field, tmp_path):
    data = _config()
    if field == "sequence":
        data["sequence"] = True
    else:
        data["hindsight"]["port"] = True
    path, signature = _write_signed_config(tmp_path, data)
    with pytest.raises(ProtectedConfigValidationError):
        ProtectedConfigLoader(path, signature).load()


def test_bool_replay_sequence_is_rejected(tmp_path):
    path, signature = _write_signed_config(tmp_path, _config())
    replay = tmp_path / "replay.json"
    replay.write_text('{"config_id":"x","sequence":true,"digest":"x"}')
    with pytest.raises(ProtectedConfigValidationError):
        ProtectedConfigLoader(path, signature, replay).load()


def test_unsupported_numeric_canonicalization_is_rejected(tmp_path):
    data = _config()
    data["sequence"] = 1.0
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, separators=(",", ":"), sort_keys=True), encoding="utf-8")
    path.chmod(0o600)
    with pytest.raises(ProtectedConfigValidationError, match="floating-point"):
        ProtectedConfigLoader(path).load()