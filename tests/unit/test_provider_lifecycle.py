"""Fail-closed tests at the Hermes registration and startup boundaries."""

import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import hermes_tiered_memory as package
from hermes_tiered_memory.providers.composite import CompositeProvider


def _provider(tmp_path, config):
    return CompositeProvider(
        session=SimpleNamespace(config=config),
        platform="cli",
        hermes_home=str(tmp_path),
        context={},
        user_identity="user",
        chat_identity="chat",
        thread_identity="thread",
        active_profile_identity="profile",
    )


def test_registration_rejects_current_non_memory_provider(monkeypatch):
    class MemoryProvider:
        pass

    monkeypatch.setitem(sys.modules, "agent.memory_provider", SimpleNamespace(MemoryProvider=MemoryProvider))
    context = Mock()

    with pytest.raises(RuntimeError, match="does not implement"):
        package.register(context)

    context.register_memory_provider.assert_not_called()


def test_registration_fails_closed_when_hermes_api_is_unavailable(monkeypatch):
    monkeypatch.delitem(sys.modules, "agent.memory_provider", raising=False)
    context = Mock()

    with pytest.raises(RuntimeError, match="API is unavailable"):
        package.register(context)

    context.register_memory_provider.assert_not_called()


@pytest.mark.parametrize(
    "config, match",
    [({}, "path is required"), ({"tiered": {"protected_config": {"path": "/missing"}}}, "invalid")],
)
def test_startup_rejects_missing_or_malformed_config(tmp_path, config, match):
    with pytest.raises(RuntimeError, match=match):
        _provider(tmp_path, config).initialize_all()


def test_startup_rejects_unsigned_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "key_id": "test-key",
                "config_id": "config",
                "sequence": 1,
                "issued_at": "2026-01-01T00:00:00+00:00",
                "not_before": "2026-01-01T00:00:00+00:00",
                "expires_at": "2099-01-01T00:00:00+00:00",
                "hindsight": {
                    "scheme": "https",
                    "host": "example.com",
                    "port": 443,
                    "base_path": "/api",
                    "tls": {"verify": True, "ca_file": None},
                    "proxy": {"enabled": False},
                },
            }
        ),
        encoding="utf-8",
    )
    config_path.chmod(0o600)
    config = {"tiered": {"protected_config": {"path": str(config_path)}}}

    with pytest.raises(RuntimeError, match="invalid"):
        _provider(tmp_path, config).initialize_all()


def test_startup_rejects_malformed_config(tmp_path):
    config_path = tmp_path / "config.json"
    config_path.write_text("not-json", encoding="utf-8")
    config_path.chmod(0o600)
    config = {"tiered": {"protected_config": {"path": str(config_path)}}}

    with pytest.raises(RuntimeError, match="invalid"):
        _provider(tmp_path, config).initialize_all()


def test_loader_accepts_same_sequence_identical_payload(tmp_path):
    from tests.unit.test_config import _config, _write_signed_config
    from hermes_tiered_memory.config import ProtectedConfigLoader

    path, signature_path = _write_signed_config(tmp_path, _config())
    loader = ProtectedConfigLoader(path, signature_path)
    loader.load()

    assert loader.load().sequence == 1
