"""Disposable pinned-Hermes integration tests for the tiered provider."""

from __future__ import annotations

import importlib.util
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERMES_PIN = "3ef6bbd201263d354fd83ec55b3c306ded2eb72a"
HERMES_SOURCE = Path("/hermes")


def _load_pinned_module(source: Path, name: str, path: str):
    spec = importlib.util.spec_from_file_location(name, source / path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def pinned_hermes(tmp_path_factory, monkeypatch):
    if not (HERMES_SOURCE / ".git").is_dir():
        pytest.skip("pinned Hermes checkout is unavailable")
    resolved = subprocess.check_output(
        ["git", "-C", str(HERMES_SOURCE), "rev-parse", HERMES_PIN], text=True
    ).strip()
    if resolved != HERMES_PIN:
        pytest.skip("pinned Hermes revision is unavailable")

    source = tmp_path_factory.mktemp("pinned-hermes") / "source"
    subprocess.run(
        ["git", "-C", str(HERMES_SOURCE), "worktree", "add", "--detach", str(source), HERMES_PIN],
        check=True,
        capture_output=True,
        text=True,
    )
    monkeypatch.syspath_prepend(str(source))
    for name in list(sys.modules):
        is_agent_module = name == "agent" or name.startswith("agent.")
        is_tiered_module = name == "hermes_tiered_memory" or name.startswith(
            "hermes_tiered_memory."
        )
        is_tiered_plugin_module = name == "_hermes_user_memory.tiered-memory" or name.startswith(
            "_hermes_user_memory.tiered-memory."
        )
        if is_agent_module or is_tiered_module or is_tiered_plugin_module:
            monkeypatch.delitem(sys.modules, name, raising=False)
    agent = _load_pinned_module(source, "agent", "agent/__init__.py")
    agent.__path__ = [str(source / "agent")]
    memory_provider = _load_pinned_module(
        source, "agent.memory_provider", "agent/memory_provider.py"
    )
    try:
        yield memory_provider
    finally:
        subprocess.run(
            ["git", "-C", str(HERMES_SOURCE), "worktree", "remove", "--force", str(source)],
            check=True,
            capture_output=True,
            text=True,
        )


def test_pinned_hermes_loader_fails_closed_without_runtime_config(
    tmp_path, monkeypatch, pinned_hermes
):
    from agent.memory_manager import MemoryManager
    from plugins.memory import load_memory_provider

    from hermes_tiered_memory import __file__ as package_init

    hermes_home = tmp_path / "hermes"
    plugin_dir = hermes_home / "plugins" / "tiered-memory"
    shutil.copytree(Path(package_init).parent, plugin_dir)
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    provider = load_memory_provider("tiered-memory")

    assert provider is not None
    assert isinstance(provider, pinned_hermes.MemoryProvider)
    assert not provider.__class__.__abstractmethods__
    assert provider.name == "tiered-memory"
    assert provider.is_available()

    manager = MemoryManager()
    manager.add_provider(provider)
    manager.add_provider(provider)
    assert [item.name for item in manager.providers] == ["tiered-memory"]

    # The pinned manager does not forward the loaded config mapping. A live
    # composite must refuse this incomplete lifecycle rather than substituting
    # the previous SQLite/mock implementation.
    manager.initialize_all(
        session_id="session-1",
        hermes_home=str(tmp_path / "hermes"),
        platform="cli",
        agent_context="primary",
        agent_identity="disposable",
    )
    assert manager.providers == []
    assert not provider.is_available()
    assert manager.get_all_tool_schemas() == []


def test_pinned_manager_loads_tiered_config_through_provider_config_seam(
    tmp_path, monkeypatch, pinned_hermes
):
    """The real manager lifecycle reaches the canonical provider config loader."""
    from agent.memory_manager import MemoryManager
    from plugins.memory import load_memory_provider

    from hermes_tiered_memory import __file__ as package_init

    hermes_home = tmp_path / "hermes"
    plugin_dir = hermes_home / "plugins" / "tiered-memory"
    shutil.copytree(Path(package_init).parent, plugin_dir)
    (hermes_home / "config.yaml").write_text(
        """\
memory:
  provider: tiered-memory
  tiered:
    bank_mapping:
      disposable: disposable-bank
profiles:
  disposable:
    secrets:
      hindsight:
        api_key: disposable-test-key
""",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))

    provider = load_memory_provider("tiered-memory")
    assert provider is not None
    captured = []
    monkeypatch.setattr(provider, "initialize_all", lambda: captured.append(provider._runtime_config))

    manager = MemoryManager()
    manager.add_provider(provider)
    manager.initialize_all(
        session_id="session-1",
        hermes_home=str(hermes_home),
        platform="cli",
        agent_context="primary",
        agent_identity="disposable",
    )

    assert len(captured) == 1
    runtime_config = captured[0]
    assert runtime_config["tiered"] == {"bank_mapping": {"disposable": "disposable-bank"}}
    assert runtime_config["memory"]["tiered"] == runtime_config["tiered"]
    assert runtime_config["profiles"]["disposable"]["secrets"]["hindsight"] == {
        "api_key": "disposable-test-key"
    }
    assert provider.is_available()


def test_pinned_manager_disables_failed_provider_before_tool_exposure(
    tmp_path, monkeypatch, pinned_hermes
):
    from agent.memory_manager import MemoryManager, inject_memory_provider_tools

    from hermes_tiered_memory.adapters import sqlite_l1
    from hermes_tiered_memory.providers.composite import CompositeProvider

    class FailingSQLiteL1Adapter:
        def __init__(self, path):
            raise OSError(f"cannot open {path}")

    monkeypatch.setattr(sqlite_l1, "SQLiteL1Adapter", FailingSQLiteL1Adapter)
    provider = CompositeProvider()
    manager = MemoryManager()
    manager.add_provider(provider)

    # As in agent-init, no config mapping is forwarded to MemoryManager.
    manager.initialize_all(
        session_id="session-1",
        hermes_home=str(tmp_path / "hermes"),
        platform="cli",
        agent_context="primary",
        agent_identity="disposable",
    )

    assert manager.providers == []
    assert not provider.is_available()
    assert manager.get_all_tool_schemas() == []
    assert not manager.has_tool("tiered_memory_store")
    assert "No memory provider handles tool 'tiered_memory_store'" in manager.handle_tool_call(
        "tiered_memory_store", {"content": "must not dispatch"}
    )

    agent = type(
        "Agent",
        (),
        {
            "_memory_manager": manager,
            "tools": [],
            "enabled_toolsets": ["memory"],
            "valid_tool_names": set(),
        },
    )()
    assert inject_memory_provider_tools(agent) == 0
    assert agent.tools == []


def test_pinned_manager_unregisters_provider_when_initialization_cleanup_fails(
    tmp_path, monkeypatch, pinned_hermes
):
    """Cleanup failures must not leave a failed provider's tools routable."""
    from agent.memory_manager import MemoryManager

    from hermes_tiered_memory.adapters import sqlite_l1
    from hermes_tiered_memory.outbox import store
    from hermes_tiered_memory.providers.composite import CompositeProvider

    class ClosingFailsSQLiteL1Adapter:
        def __init__(self, path):
            self.path = path

        def close(self):
            raise OSError("L1 cleanup failed")

    class FailingOutboxStore:
        def __init__(self, path):
            raise OSError(f"cannot open {path}")

    monkeypatch.setattr(sqlite_l1, "SQLiteL1Adapter", ClosingFailsSQLiteL1Adapter)
    monkeypatch.setattr(store, "OutboxStore", FailingOutboxStore)
    provider = CompositeProvider()
    manager = MemoryManager()
    manager.add_provider(provider)

    manager.initialize_all(
        session_id="session-1",
        hermes_home=str(tmp_path / "hermes"),
        platform="cli",
        agent_context="primary",
        agent_identity="disposable",
    )

    assert provider not in manager.providers
    for tool_name in (
        "tiered_memory_store",
        "tiered_memory_search",
        "tiered_memory_delete",
    ):
        assert not manager.has_tool(tool_name)
        assert f"No memory provider handles tool '{tool_name}'" in manager.handle_tool_call(
            tool_name, {}
        )


def test_plugin_and_runtime_fail_closed_without_partial_registration(
    tmp_path, monkeypatch, pinned_hermes
):
    from hermes_tiered_memory import register
    from hermes_tiered_memory.providers.composite import CompositeProvider

    class Collector:
        def __init__(self):
            self.calls = []

        def register_memory_provider(self, provider):
            self.calls.append(provider)

    collector = Collector()
    register(collector)
    assert len(collector.calls) == 1

    provider = CompositeProvider()
    with pytest.raises(RuntimeError, match="Tiered live adapters could not initialize"):
        provider.initialize(
            "session-1",
            hermes_home=str(tmp_path / "hermes"),
            agent_identity="disposable",
            config={"tiered": {}},
        )
    assert provider.l1_adapter is None
    assert provider.l2_adapter is None
    assert provider.outbox_store is None

    monkeypatch.setitem(sys.modules, "agent.memory_provider", None)
    blocked = Collector()
    with pytest.raises(RuntimeError, match="API is unavailable"):
        register(blocked)
    assert blocked.calls == []
