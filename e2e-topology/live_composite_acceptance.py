#!/usr/bin/env python3
"""Disposable live acceptance probe for the Hermes tiered-memory plugin.

This process owns only the topology root passed through ``TOPOLOGY_ROOT``. It
creates an ephemeral signed endpoint configuration and synthetic data; it never
reads an ambient Hermes profile, credential, or production endpoint.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import shutil
import sys
import time
from pathlib import Path


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_protected_config(root: Path, port: int) -> tuple[Path, Path]:
    from hermes_tiered_memory.config import ProtectedConfigLoader

    config_path = root / "protected-hindsight.json"
    signature_path = root / "protected-hindsight.sig"
    secret = b"disposable-e2e-hmac-key-not-a-credential"
    ProtectedConfigLoader.register_key("disposable-e2e-key", secret)
    payload: dict[str, object] = {
        "schema_version": 1,
        "key_id": "disposable-e2e-key",
        "config_id": "disposable-e2e-config",
        "sequence": 1,
        "issued_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "hindsight": {
            "scheme": "https",
            "host": "127.0.0.1",
            "port": port,
            "base_path": "/",
            "tls": {"verify": True, "ca_file": str(root / "tls" / "ca.crt")},
            "proxy": {"enabled": False},
        },
    }
    canonical = _canonical(payload)
    config_path.write_bytes(canonical)
    signature_path.write_bytes(
        hmac.new(secret, ProtectedConfigLoader.DOMAIN_SEPARATOR + canonical, hashlib.sha256).digest()
    )
    config_path.chmod(0o600)
    signature_path.chmod(0o600)
    return config_path, signature_path


def _wait_until(predicate, description: str, timeout_seconds: float = 45.0) -> None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.25)
    raise AssertionError(f"timed out waiting for {description}")


def main() -> None:
    root = Path(os.environ["TOPOLOGY_ROOT"])
    repo_root = Path(__file__).resolve().parents[1]
    port = int((root / "state" / "hindsight-port").read_text().strip())
    hermes_home = root / "hermes"
    plugin_dir = hermes_home / "plugins" / "tiered-memory"
    if plugin_dir.exists():
        shutil.rmtree(plugin_dir)
    shutil.copytree(repo_root / "hermes_tiered_memory", plugin_dir)
    protected_config, signature = _write_protected_config(root, port)
    (hermes_home / "config.yaml").write_text(
        f"""memory:
  provider: tiered-memory
  tiered:
    bank_mapping:
      disposable: disposable-bank
    batch_size: 1
    retry:
      max_attempts: 3
      backoff_seconds: [0]
    protected_config:
      path: {protected_config}
      signature:
        path: {signature}
profiles:
  disposable:
    secrets:
      hindsight:
        api_key: disposable-test-key
""",
        encoding="utf-8",
    )
    os.environ["HERMES_HOME"] = str(hermes_home)
    os.environ["MNEMOSYNE_DATA_DIR"] = str(root / "mnemosyne")
    os.environ["MNEMOSYNE_DB_PATH"] = str(root / "mnemosyne" / "e2e.sqlite")

    from agent.memory_manager import MemoryManager
    from plugins.memory import load_memory_provider

    provider = load_memory_provider("tiered-memory")
    assert provider is not None
    manager = MemoryManager()
    manager.add_provider(provider)
    manager.add_provider(provider)
    assert [item.name for item in manager.providers] == ["tiered-memory"]
    manager.initialize_all(
        session_id="disposable-session",
        hermes_home=str(hermes_home),
        platform="cli",
        agent_context="acceptance",
        agent_identity="disposable",
        chat_id="disposable-chat",
    )
    assert [item.name for item in manager.providers] == ["tiered-memory"]
    assert len(manager.get_all_tool_schemas()) == 3
    assert provider.health()["l1"]["status"] == "healthy"
    assert provider.health()["l2"]["status"] == "healthy"

    stored = json.loads(
        manager.handle_tool_call(
            "tiered_memory_store",
            {
                "content": "composite-e2e-001: synthetic durable memory",
                "metadata": {"scenario": "lifecycle", "nested": {"preserved": [1, None]}},
            },
        )
    )
    source_id = stored["id"]
    assert stored["metadata"] == {"scenario": "lifecycle", "nested": {"preserved": [1, None]}}
    assert json.loads(manager.handle_tool_call("tiered_memory_search", {"query": "synthetic durable"}))[0]["id"] == source_id

    _wait_until(
        lambda: provider.outbox_store is not None
        and provider.outbox_store.get_stats().get("promoted", 0) >= 1,
        "live L2 promotion",
    )
    assert provider.l2_adapter is not None
    matching = []
    recalled = []
    for _ in range(30):
        recalled = provider.l2_adapter.recall("composite-e2e-001", "disposable", limit=10)
        matching = [item for item in recalled if item.metadata.get("mnemosyne_id") == source_id]
        if matching:
            break
        time.sleep(1)
    assert len(matching) == 1, [item.metadata for item in recalled]
    assert matching[0].metadata["scenario"] == "lifecycle"
    assert matching[0].metadata["nested"] == {"preserved": [1, None]}

    assert json.loads(manager.handle_tool_call("tiered_memory_delete", {"memory_id": source_id}))["deleted"] is True
    _wait_until(
        lambda: provider.outbox_store is not None
        and provider.outbox_store.get_stats().get("promoted", 0) >= 2,
        "live L2 delete",
    )
    assert provider.l1_adapter is not None
    deleted = provider.l1_adapter.get(source_id)
    assert deleted is not None and deleted.is_deleted
    assert not [item for item in provider.l2_adapter.recall("synthetic durable memory", "disposable", limit=10) if item.metadata.get("mnemosyne_id") == source_id]
    provider.close()
    print("COMPOSITE_ACCEPTANCE_PASS load=1 lifecycle=store-retrieve-delete wire_fidelity=preserved")


if __name__ == "__main__":
    main()
