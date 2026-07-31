"""Lifecycle wiring tests for the real composite adapter classes."""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

from hermes_tiered_memory.adapters.l1 import L1Record
from hermes_tiered_memory.adapters.l2 import L2RetainResult
from hermes_tiered_memory.config import ProtectedConfigLoader
from hermes_tiered_memory.providers.composite import CompositeProvider


def _write_signed_config(directory: Path) -> tuple[Path, Path]:
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
            "host": "hindsight.example.com",
            "port": 443,
            "base_path": "/api/v1",
            "tls": {"verify": True, "ca_file": None},
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


def test_initialize_wires_live_adapter_classes_outbox_and_worker(tmp_path, monkeypatch):
    from hermes_tiered_memory.adapters import hindsight_l2, mnemosyne_l1

    class RecordingL1:
        instances = []

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.closed = False
            self.records = {}
            type(self).instances.append(self)

        def remember(self, content, metadata):
            record = L1Record(
                source_id="live-source-1",
                revision=1,
                content=content,
                metadata=metadata,
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
            )
            self.records[record.source_id] = record
            return record

        def remember_batch(self, items):
            return [self.remember(content, metadata) for content, metadata in items]

        def recall(self, query, limit=10):
            return [record for record in self.records.values() if query in record.content][:limit]

        def forget(self, source_id):
            return self.records.pop(source_id, None) is not None

        def get(self, source_id):
            return self.records.get(source_id)

        def get_revision(self, source_id):
            record = self.records.get(source_id)
            return record.revision if record else None

        def health(self):
            return {"status": "closed" if self.closed else "healthy"}

        def close(self):
            self.closed = True

    class RecordingL2:
        instances = []

        def __init__(self, base_url):
            self.base_url = base_url
            self.closed = False
            type(self).instances.append(self)

        def retain(self, document_id, content, metadata, async_op=True):
            return L2RetainResult(operation_id="operation-1", success=True)

        def retain_batch(self, items, async_op=True):
            return []

        def recall(self, query, bank_id, limit=10):
            return []

        def delete_document(self, bank_id, document_id):
            return True

        def get_operation_status(self, operation_id):
            return {"status": "completed"}

        def health(self):
            return {"status": "closed" if self.closed else "healthy"}

        def close(self):
            self.closed = True

    monkeypatch.setattr(mnemosyne_l1, "MnemosyneL1Adapter", RecordingL1)
    monkeypatch.setattr(hindsight_l2, "HindsightL2Adapter", RecordingL2)
    config_path, signature_path = _write_signed_config(tmp_path)
    config = {
        "tiered": {
            "bank_mapping": {"profile": "profile-bank"},
            "batch_size": 2,
            "retry": {"max_attempts": 3, "backoff_seconds": [0]},
            "protected_config": {
                "path": str(config_path),
                "signature": {"path": str(signature_path)},
            },
        }
    }
    provider = CompositeProvider(session=SimpleNamespace(config=config))

    try:
        provider.initialize(
            "session-1",
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
            chat_id="chat-1",
        )

        assert RecordingL1.instances[0].kwargs == {
            "data_dir": tmp_path / "hermes-home" / "profiles" / "profile" / "tiered-memory" / "mnemosyne",
            "profile": "profile",
            "bank": "profile-bank",
            "session_id": "session-1",
            "channel_id": "chat-1",
        }
        assert RecordingL2.instances[0].base_url == "https://hindsight.example.com:443/api/v1"
        assert provider.outbox_store is not None
        assert provider.promotion_worker is not None
        assert provider.promotion_worker.config.batch_size == 2
        assert provider.promotion_worker.config.max_attempts_per_cycle == 3

        stored = provider.memory_store("durable live route", {"kind": "test"})
        assert stored["source"] == "l1"
        assert provider.outbox_store.get_by_identity(
            provider._generate_document_id("live-source-1"), 1
        ) is not None
    finally:
        provider.close()

    assert RecordingL1.instances[0].closed
    assert RecordingL2.instances[0].closed


def test_initialize_rejects_configless_hermes_lifecycle_before_adapter_side_effects(tmp_path):
    provider = CompositeProvider()

    try:
        provider.initialize(
            "session-1",
            hermes_home=str(tmp_path / "hermes-home"),
            agent_identity="profile",
        )
    except RuntimeError as exc:
        assert "requires live tiered configuration" in str(exc)
    else:
        raise AssertionError("configless provider initialization was accepted")

    assert provider.l1_adapter is None
    assert provider.l2_adapter is None
    assert provider.outbox_store is None
