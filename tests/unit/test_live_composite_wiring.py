"""No-mock lifecycle tests for the concrete live composite adapters."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from hermes_tiered_memory.adapters.hindsight_l2 import HindsightL2Adapter
from hermes_tiered_memory.adapters.mnemosyne_l1 import MnemosyneL1Adapter
from hermes_tiered_memory.config import ProtectedConfigLoader
from hermes_tiered_memory.providers.composite import CompositeProvider


def _write_signed_config(directory: Path, ca_file: str | None = None) -> tuple[Path, Path]:
    secret = b"composite-wiring-test-key"
    ProtectedConfigLoader.register_key("composite-wiring-key", secret)
    payload = {
        "schema_version": 1,
        "key_id": "composite-wiring-key",
        "config_id": "composite-wiring-config",
        "sequence": 1,
        "issued_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "hindsight": {
            "scheme": "https",
            "host": "hindsight.invalid",
            "port": 443,
            "base_path": "/api/v1",
            "tls": {"verify": True, "ca_file": ca_file},
            "proxy": {"enabled": False},
        },
    }
    config_path = directory / "protected.json"
    signature_path = directory / "protected.sig"
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    config_path.write_bytes(canonical)
    signature_path.write_bytes(
        hmac.new(secret, ProtectedConfigLoader.DOMAIN_SEPARATOR + canonical, hashlib.sha256).digest()
    )
    config_path.chmod(0o600)
    signature_path.chmod(0o600)
    return config_path, signature_path


@pytest.fixture
def disposable_mnemosyne(monkeypatch):
    """Provide only Mnemosyne's unavailable upstream dependency, not an adapter double."""

    class Beam:
        def __init__(self, memory):
            self.memory = memory
            self.conn = sqlite3.connect(":memory:")

        def remember_batch(self, items):
            return [
                self.memory.remember(item["content"], source=item["source"], metadata=item["metadata"])
                for item in items
            ]

    class Mnemosyne:
        def __init__(self, *, session_id, db_path, bank, channel_id):
            del session_id, bank, channel_id
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(db_path)
            self.beam = Beam(self)

        def remember(self, content, *, source, metadata, scope):
            del content, source, metadata, scope
            return "disposable-source"

        def get(self, source_id):
            return None if source_id != "disposable-source" else {
                "id": source_id,
                "content": "disposable content",
                "metadata": {},
                "created_at": "2026-01-01T00:00:00+00:00",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }

        def forget(self, source_id):
            return source_id == "disposable-source"

        def recall(self, query, *, top_k, channel_id):
            del query, top_k, channel_id
            return []

    mnemosyne = ModuleType("mnemosyne")
    core = ModuleType("mnemosyne.core")
    memory = ModuleType("mnemosyne.core.memory")
    memory.Mnemosyne = Mnemosyne
    monkeypatch.setitem(sys.modules, "mnemosyne", mnemosyne)
    monkeypatch.setitem(sys.modules, "mnemosyne.core", core)
    monkeypatch.setitem(sys.modules, "mnemosyne.core.memory", memory)


def test_initialize_wires_real_adapters_outbox_and_worker(tmp_path, disposable_mnemosyne):
    config_path, signature_path = _write_signed_config(tmp_path)
    api_key = "test-hindsight-api-key"
    config = {
        "profiles": {"profile": {"secrets": {"hindsight": {"api_key": api_key}}}},
        "tiered": {
            "bank_mapping": {"profile": "profile-bank"},
            "batch_size": 2,
            "retry": {"max_attempts": 3, "backoff_seconds": [0]},
            "protected_config": {
                "path": str(config_path),
                "signature": {"path": str(signature_path)},
            },
        },
    }
    hermes_home = tmp_path / "hermes-home"
    provider = CompositeProvider(session=SimpleNamespace(config=config))

    try:
        provider.initialize(
            "session-1",
            hermes_home=str(hermes_home),
            agent_identity="profile",
            chat_id="chat-1",
        )

        assert isinstance(provider.l1_adapter, MnemosyneL1Adapter)
        assert isinstance(provider.l2_adapter, HindsightL2Adapter)
        assert provider.l1_adapter.db_path == (
            hermes_home
            / "profiles"
            / "profile"
            / "tiered-memory"
            / "mnemosyne"
            / "banks"
            / "profile-bank"
            / "mnemosyne.db"
        )
        assert provider.l1_adapter.db_path.is_file()
        ledger_row = provider.l1_adapter._ledger.execute("SELECT 1").fetchone()
        assert tuple(ledger_row) == (1,)
        assert provider.l2_adapter._base_url == "https://hindsight.invalid:443/api/v1"
        assert provider.l2_adapter._client.headers["Authorization"] == f"Bearer {api_key}"
        assert provider.outbox_store is not None
        assert (hermes_home / "profiles" / "profile" / "tiered-memory" / "outbox.db").is_file()
        assert provider.promotion_worker is not None
        assert provider.promotion_worker.config.batch_size == 2
        assert provider.promotion_worker.config.max_attempts_per_cycle == 3
    finally:
        provider.close()

    assert provider.l1_adapter is not None and provider.l1_adapter.health()["status"] == "closed"
    assert provider.l2_adapter is not None and provider.l2_adapter.health()["status"] == "closed"


def test_initialize_forwards_protected_ca_to_owned_l2_client(
    tmp_path, disposable_mnemosyne, monkeypatch
):
    ca_path = tmp_path / "disposable-ca.crt"
    ca_path.write_text("synthetic test CA")
    ca_file = str(ca_path)
    config_path, signature_path = _write_signed_config(tmp_path, ca_file=ca_file)
    config = {
        "profiles": {"profile": {"secrets": {"hindsight": {"api_key": "test-key"}}}},
        "tiered": {
            "protected_config": {
                "path": str(config_path),
                "signature": {"path": str(signature_path)},
            }
        },
    }
    created: dict[str, object] = {}
    tls_context = object()

    class CapturingClient:
        def __init__(self, **kwargs):
            created.update(kwargs)

        def close(self):
            pass

    monkeypatch.setattr("hermes_tiered_memory.adapters.hindsight_l2.httpx.Client", CapturingClient)
    monkeypatch.setattr("hermes_tiered_memory.adapters.hindsight_l2.ssl.create_default_context", lambda *, cafile: tls_context)
    provider = CompositeProvider(session=SimpleNamespace(config=config))

    try:
        provider.initialize(
            "session-1",
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
            chat_id="chat-1",
        )
        assert isinstance(provider.l2_adapter, HindsightL2Adapter)
        assert provider.l2_adapter._tls_verify is tls_context
        assert created["verify"] is tls_context
    finally:
        provider.close()


@pytest.mark.parametrize(
    "profile_config",
    [
        {},
        {"profile": {}},
        {"profile": {"secrets": {"hindsight": {"api_key": ""}}}},
        {"profile": {"secrets": {"hindsight": {"api_key": " invalid "}}}},
    ],
)
def test_initialize_fails_closed_without_valid_profile_hindsight_key(
    tmp_path, disposable_mnemosyne, profile_config
):
    config_path, signature_path = _write_signed_config(tmp_path)
    provider = CompositeProvider(
        session=SimpleNamespace(
            config={
                "profiles": profile_config,
                "tiered": {
                    "protected_config": {
                        "path": str(config_path),
                        "signature": {"path": str(signature_path)},
                    }
                },
            }
        )
    )

    with pytest.raises(RuntimeError, match="Tiered live adapters could not initialize"):
        provider.initialize(
            "session-1",
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
        )

    assert provider.l2_adapter is None
    assert provider.promotion_worker is None


def test_initialize_rejects_configless_hermes_lifecycle_before_adapter_side_effects(tmp_path):
    provider = CompositeProvider()

    with pytest.raises(RuntimeError, match="requires live tiered configuration"):
        provider.initialize(
            "session-1",
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
        )

    assert provider.l1_adapter is None
    assert provider.l2_adapter is None
    assert provider.outbox_store is None


# ---------------------------------------------------------------------------
# Production verification-key provisioning seam (Gap 2)
#
# The tests above use the test-only ProtectedConfigLoader.register_key() to pin
# keys. The tests below prove that a signed Ed25519 protected config loads
# end-to-end through the composite using ONLY the tiered.verification_keys
# config block — no register_key() call, no env-var bridge. This is the
# production path that closes Gap 2.
# ---------------------------------------------------------------------------


def _ed25519_signed_config(
    directory: Path, key_id: str, private_key
) -> tuple[Path, Path, bytes]:
    """Write a canonical config + Ed25519 signature; return (config, sig, public_bytes)."""
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519
    import cryptography.hazmat.primitives.serialization as ser

    public_bytes = private_key.public_key().public_bytes(
        encoding=ser.Encoding.Raw, format=ser.PublicFormat.Raw
    )
    payload = {
        "schema_version": 1,
        "key_id": key_id,
        "config_id": "seam-config",
        "sequence": 1,
        "issued_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "hindsight": {
            "scheme": "https",
            "host": "hindsight.invalid",
            "port": 443,
            "base_path": "/api/v1",
            "tls": {"verify": True, "ca_file": None},
            "proxy": {"enabled": False},
        },
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    config_path = directory / "protected.json"
    signature_path = directory / "protected.sig"
    config_path.write_bytes(canonical)
    config_path.chmod(0o600)
    signature = private_key.sign(ProtectedConfigLoader.DOMAIN_SEPARATOR + canonical)
    signature_path.write_bytes(signature)
    signature_path.chmod(0o600)
    return config_path, signature_path, public_bytes


def test_initialize_loads_signed_config_via_verification_keys_block(
    tmp_path, disposable_mnemosyne
):
    """A signed Ed25519 config loads using ONLY tiered.verification_keys (no register_key)."""
    import base64
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519

    ProtectedConfigLoader.clear_keys()
    private_key = ed25519.Ed25519PrivateKey.generate()
    config_path, signature_path, public_bytes = _ed25519_signed_config(
        tmp_path, "prod-ed25519-1", private_key
    )

    config = {
        "profiles": {"profile": {"secrets": {"hindsight": {"api_key": "test-key"}}}},
        "tiered": {
            "bank_mapping": {"profile": "profile-bank"},
            "batch_size": 1,
            "retry": {"max_attempts": 1, "backoff_seconds": [0]},
            "verification_keys": [
                {
                    "key_id": "prod-ed25519-1",
                    "algorithm": "ed25519",
                    "public_key_b64": base64.b64encode(public_bytes).decode("ascii"),
                }
            ],
            "protected_config": {
                "path": str(config_path),
                "signature": {"path": str(signature_path)},
            },
        },
    }
    hermes_home = tmp_path / "hermes-home"
    provider = CompositeProvider()

    try:
        provider.initialize(
            "session-1",
            config=config,
            hermes_home=str(hermes_home),
            agent_identity="profile",
            chat_id="chat-1",
        )
        assert provider.protected_config is not None
        assert provider.protected_config.key_id == "prod-ed25519-1"
        assert provider.l2_adapter is not None
        assert provider.l2_adapter._base_url == "https://hindsight.invalid:443/api/v1"
    finally:
        provider.close()
        ProtectedConfigLoader.clear_keys()


def test_initialize_fails_closed_without_verification_keys(tmp_path, disposable_mnemosyne):
    """No verification_keys + a signed config => fail closed (Unauthorized key_id)."""
    import cryptography.hazmat.primitives.asymmetric.ed25519 as ed25519

    ProtectedConfigLoader.clear_keys()
    private_key = ed25519.Ed25519PrivateKey.generate()
    config_path, signature_path, _ = _ed25519_signed_config(
        tmp_path, "prod-ed25519-1", private_key
    )

    config = {
        "profiles": {"profile": {"secrets": {"hindsight": {"api_key": "test-key"}}}},
        "tiered": {
            "protected_config": {
                "path": str(config_path),
                "signature": {"path": str(signature_path)},
            },
        },
    }
    provider = CompositeProvider()

    with pytest.raises(RuntimeError, match="startup aborted"):
        provider.initialize(
            "session-1",
            config=config,
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
            chat_id="chat-1",
        )
    assert provider.protected_config is None
    assert provider.l2_adapter is None
    ProtectedConfigLoader.clear_keys()


def test_initialize_rejects_malformed_verification_keys(tmp_path, disposable_mnemosyne):
    """A malformed verification_keys entry fails closed at startup before loading."""
    config = {
        "profiles": {"profile": {"secrets": {"hindsight": {"api_key": "k"}}}},
        "tiered": {
            "verification_keys": [
                {"key_id": "bad", "algorithm": "ed25519", "public_key_b64": "too-short"}
            ],
            "protected_config": {
                "path": str(tmp_path / "nope.json"),
                "signature": {"path": str(tmp_path / "nope.sig")},
            },
        },
    }
    ProtectedConfigLoader.clear_keys()
    provider = CompositeProvider()
    with pytest.raises(RuntimeError) as exc_info:
        provider.initialize(
            "session-1",
            config=config,
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
            chat_id="chat-1",
        )
    # initialize() wraps startup failures; the verification_keys cause must be
    # visible in the exception chain so an operator can diagnose it.
    chain = "".join(str(e) for e in [exc_info.value, exc_info.value.__cause__] if e)
    assert "verification_keys" in chain
    assert provider.protected_config is None
    ProtectedConfigLoader.clear_keys()
