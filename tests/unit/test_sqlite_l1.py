"""Focused tests for the concrete SQLite L1 persistence boundary."""

import json
import os
import signal
import sqlite3
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone

import pytest

from hermes_tiered_memory.adapters.sqlite_l1 import SQLiteL1Adapter


def test_atomic_l1_outbox_success_and_shared_topology(tmp_path):
    from hermes_tiered_memory.outbox.store import OutboxStore

    l1 = SQLiteL1Adapter(tmp_path / "l1.db")
    outbox = OutboxStore(tmp_path / "outbox.db")
    l1.configure_atomic_outbox(outbox)
    record = l1.remember_with_outbox("atomic", {"kind": "test"},
                                    document_id_factory=lambda source_id: "doc-1",
                                    promotion_metadata_factory=lambda record: {"kind": record.metadata["kind"]},
                                    outbox_store=outbox)

    assert l1.get(record.source_id) is not None
    item = outbox.get_by_identity("doc-1", 1)
    assert item is not None
    assert item.mnemosyne_id == record.source_id
    assert l1.connection.execute("PRAGMA database_list").fetchall()[-1][2] == str(tmp_path / "outbox.db")
    l1.close()
    outbox.close()


def test_outbox_uses_delete_for_standalone_and_second_instance(tmp_path):
    from hermes_tiered_memory.outbox.store import OutboxStore

    path = tmp_path / "outbox.db"
    first = OutboxStore(path)
    assert first._get_connection().execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    second = OutboxStore(path)
    assert second._get_connection().execute("PRAGMA journal_mode").fetchone()[0] == "delete"
    first.close()
    second.close()


def test_atomic_configuration_accepts_preopened_store_connection_from_other_thread(tmp_path):
    from hermes_tiered_memory.outbox.store import OutboxStore

    outbox = OutboxStore(tmp_path / "outbox.db")
    ready = threading.Event()

    def preopen():
        outbox._get_connection().execute("SELECT 1")
        ready.set()

    thread = threading.Thread(target=preopen)
    thread.start()
    assert ready.wait(5)
    thread.join()

    l1 = SQLiteL1Adapter(tmp_path / "l1.db")
    l1.configure_atomic_outbox(outbox)
    record = l1.remember_with_outbox(
        "thread-safe", {}, document_id_factory=lambda _: "thread-safe-doc",
        promotion_metadata_factory=lambda record: record.metadata, outbox_store=outbox,
    )
    assert outbox.get_by_identity("thread-safe-doc", record.revision) is not None
    l1.close()
    outbox.close()


def test_existing_wal_outbox_is_rejected_without_conversion(tmp_path):
    from hermes_tiered_memory.outbox.store import OutboxStore

    path = tmp_path / "outbox.db"
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    connection.close()
    with pytest.raises(RuntimeError, match="uses WAL"):
        OutboxStore(path)
    check = sqlite3.connect(path)
    assert check.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    check.close()


def test_atomic_outbox_insert_failure_rolls_back_l1_and_is_reusable(tmp_path):
    from hermes_tiered_memory.outbox.store import OutboxStore

    l1 = SQLiteL1Adapter(tmp_path / "l1.db")
    outbox = OutboxStore(tmp_path / "outbox.db")
    l1.configure_atomic_outbox(outbox)

    def fail(*args, **kwargs):
        raise RuntimeError("crash-like enqueue failure")

    outbox.enqueue_on = fail
    with pytest.raises(RuntimeError, match="crash-like"):
        l1.remember_with_outbox("lost", {}, document_id_factory=lambda _: "doc-lost",
                                promotion_metadata_factory=lambda record: record.metadata,
                                outbox_store=outbox)
    assert l1.recall("lost") == []
    assert outbox.get_stats()["pending"] == 0

    outbox.enqueue_on = type(outbox).enqueue_on.__get__(outbox)
    record = l1.remember_with_outbox("retry", {}, document_id_factory=lambda _: "doc-retry",
                                    promotion_metadata_factory=lambda record: record.metadata,
                                    outbox_store=outbox)
    assert l1.get(record.source_id) is not None
    assert outbox.get_stats()["pending"] == 1
    l1.close()
    outbox.close()


def test_atomic_commit_failure_rolls_back_both_and_retry_has_one_identity(tmp_path):
    import sqlite3
    from hermes_tiered_memory.outbox.store import OutboxStore

    class FaultyConnection(sqlite3.Connection):
        fail_commit = False
        def commit(self):
            if self.fail_commit:
                self.fail_commit = False
                raise sqlite3.OperationalError("injected atomic commit failure")
            return super().commit()

    connection = sqlite3.connect(tmp_path / "l1.db", factory=FaultyConnection)
    l1 = SQLiteL1Adapter(connection=connection)
    outbox = OutboxStore(tmp_path / "outbox.db")
    l1.configure_atomic_outbox(outbox)
    connection.fail_commit = True
    with pytest.raises(sqlite3.OperationalError, match="atomic commit"):
        l1.remember_with_outbox("retryable", {}, document_id_factory=lambda _: "same-doc",
                                promotion_metadata_factory=lambda record: record.metadata,
                                outbox_store=outbox)
    assert l1.recall("retryable") == []
    assert outbox.get_stats()["pending"] == 0

    record = l1.remember_with_outbox("retryable", {}, document_id_factory=lambda _: "same-doc",
                                    promotion_metadata_factory=lambda record: record.metadata,
                                    outbox_store=outbox)
    assert outbox.get_by_identity("same-doc", record.revision) is not None
    assert outbox.get_stats()["pending"] == 1
    l1.close()
    outbox.close()
    connection.close()


def test_configure_atomic_outbox_commit_failure_detaches_and_allows_retry(tmp_path):
    from hermes_tiered_memory.outbox.store import OutboxStore

    class FaultyConnection(sqlite3.Connection):
        fail_commit = False
        def commit(self):
            if self.fail_commit:
                self.fail_commit = False
                raise sqlite3.OperationalError("injected configure commit failure")
            return super().commit()

    connection = sqlite3.connect(tmp_path / "l1.db", factory=FaultyConnection)
    l1 = SQLiteL1Adapter(connection=connection)
    outbox = OutboxStore(tmp_path / "outbox.db")
    connection.fail_commit = True
    with pytest.raises(sqlite3.OperationalError, match="configure commit"):
        l1.configure_atomic_outbox(outbox)
    assert not l1.supports_atomic_outbox
    assert all(row[1] != "outbox" for row in connection.execute("PRAGMA database_list"))
    l1.configure_atomic_outbox(outbox)
    assert l1.supports_atomic_outbox
    l1.close()
    outbox.close()
    connection.close()


def test_atomic_write_kill_restart_has_no_one_sided_state(tmp_path):
    """Probe rollback-journal recovery; this does not prove all crash timings."""
    script = r'''
import os, signal, sys
from pathlib import Path
from hermes_tiered_memory.adapters.sqlite_l1 import SQLiteL1Adapter
from hermes_tiered_memory.outbox.store import OutboxStore
l1 = SQLiteL1Adapter(sys.argv[1])
outbox = OutboxStore(Path(sys.argv[2]))
l1.configure_atomic_outbox(outbox)
outbox.enqueue_on = lambda *args, **kwargs: os.kill(os.getpid(), signal.SIGKILL)
l1.remember_with_outbox('crash-probe', {}, document_id_factory=lambda _: 'crash-probe-doc',
                        promotion_metadata_factory=lambda record: record.metadata,
                        outbox_store=outbox)
'''
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    result = subprocess.run(
        [sys.executable, "-c", script, str(tmp_path / "l1.db"), str(tmp_path / "outbox.db")],
        env=env, check=False,
    )
    assert result.returncode == -signal.SIGKILL
    l1 = SQLiteL1Adapter(tmp_path / "l1.db")
    from hermes_tiered_memory.outbox.store import OutboxStore
    outbox = OutboxStore(tmp_path / "outbox.db")
    assert l1.recall("crash-probe") == []
    assert outbox.get_stats()["pending"] == 0
    l1.close()
    outbox.close()


def test_atomic_provider_preserves_promotion_metadata_envelope(tmp_path):
    from unittest.mock import Mock
    from hermes_tiered_memory.outbox.store import OutboxStore
    from hermes_tiered_memory.providers.composite import CompositeProvider
    provider = CompositeProvider(
        session=Mock(), platform="cli", hermes_home=str(tmp_path), context={},
        user_identity="user", chat_identity="chat", thread_identity="thread",
        active_profile_identity="profile",
    )
    provider.l1_adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    provider.outbox_store = OutboxStore(tmp_path / "outbox.db")
    result = provider.memory_store("enveloped", {"custom": "value", "tags": ["caller"]})
    record = provider.l1_adapter.get(result["id"])
    assert record is not None
    item = provider.outbox_store.get_by_identity(provider._generate_document_id(result["id"]), record.revision)
    assert item is not None
    assert json.loads(item.metadata_snapshot) == provider._build_promotion_metadata(record)
    provider.close()

    db_path = tmp_path / "l1.db"
    first = SQLiteL1Adapter(db_path)
    record = first.remember("durable content", {"topic": "test"})
    first.close()

    second = SQLiteL1Adapter(db_path)
    restored = second.get(record.source_id)
    second.close()

    assert restored is not None
    assert restored.source_id == record.source_id
    assert restored.revision == 1
    assert restored.content == "durable content"
    assert restored.metadata == {"topic": "test"}


def test_l1_transaction_rolls_back_record_and_is_reusable(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    now = datetime.now(timezone.utc).isoformat()
    with pytest.raises(RuntimeError):
        with adapter.transaction() as connection:
            connection.execute(
                "INSERT INTO l1_records "
                "(source_id, revision, content, metadata, created_at, updated_at, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("rolled-back", 1, "not durable", "{}", now, now, 0),
            )
            raise RuntimeError("abort transaction")

    assert adapter.get("rolled-back") is None
    with adapter.transaction() as connection:
        connection.execute(
            "INSERT INTO l1_records "
            "(source_id, revision, content, metadata, created_at, updated_at, is_deleted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("committed", 1, "durable", "{}", now, now, 0),
        )
    assert adapter.get("committed").content == "durable"
    adapter.close()


def test_commit_failure_rolls_back_and_connection_is_reusable(tmp_path):
    class FaultyConnection(sqlite3.Connection):
        fail_commit = False

        def commit(self):
            if self.fail_commit:
                self.fail_commit = False
                raise sqlite3.OperationalError("injected commit failure")
            return super().commit()

    connection = sqlite3.connect(tmp_path / "l1.db", factory=FaultyConnection)
    adapter = SQLiteL1Adapter(connection=connection)
    connection.fail_commit = True
    with pytest.raises(sqlite3.OperationalError, match="injected"):
        adapter.remember("not durable", {})
    assert adapter.recall("not durable") == []
    assert adapter.remember("durable", {}).content == "durable"
    adapter.close()
    connection.close()


def test_concurrent_read_write_operations_are_serialized(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    errors = []

    def writer():
        try:
            for index in range(25):
                adapter.remember(f"concurrent-{index}", {"index": index})
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    def reader():
        try:
            for _ in range(50):
                adapter.recall("concurrent", limit=100)
                adapter.health()
        except Exception as exc:  # pragma: no cover - failure is asserted below
            errors.append(exc)

    threads = [threading.Thread(target=writer), threading.Thread(target=reader)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(5)
    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert adapter.health()["record_count"] == 25
    adapter.close()


def test_shared_transaction_api_serializes_close_until_transaction_finishes(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    entered = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    def writer():
        with adapter.transaction() as connection:
            entered.set()
            assert release.wait(2)
            connection.execute(
                "INSERT INTO l1_records "
                "(source_id, revision, content, metadata, created_at, updated_at, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                ("serialized", 1, "visible", "{}", "now", "now", 0),
            )

    def closer():
        adapter.close()
        closed.set()

    writer_thread = threading.Thread(target=writer)
    close_thread = threading.Thread(target=closer)
    writer_thread.start()
    assert entered.wait(2)
    close_thread.start()
    time.sleep(0.05)
    assert not closed.is_set()
    release.set()
    writer_thread.join(2)
    close_thread.join(2)
    assert closed.is_set()
    assert adapter.health() == {"status": "closed"}


def test_existing_unsupported_schema_is_rejected(tmp_path):
    connection = sqlite3.connect(tmp_path / "legacy.db")
    connection.execute("CREATE TABLE l1_records (source_id TEXT PRIMARY KEY)")
    connection.commit()
    connection.close()
    with pytest.raises(RuntimeError, match="unsupported existing l1_records schema"):
        SQLiteL1Adapter(tmp_path / "legacy.db")


def test_injected_row_factory_is_preserved(tmp_path):
    connection = sqlite3.connect(tmp_path / "l1.db")
    connection.row_factory = lambda cursor, row: tuple(row)
    adapter = SQLiteL1Adapter(connection=connection)
    assert connection.row_factory is not None
    record = adapter.remember("plain tuple", {"kind": "test"})
    assert adapter.get(record.source_id).metadata == {"kind": "test"}
    adapter.close()
    connection.close()


def test_injected_mapping_row_factory_supports_schema_initialization(tmp_path):
    def as_dict(cursor, row):
        return {column[0]: value for column, value in zip(cursor.description, row)}

    connection = sqlite3.connect(tmp_path / "l1.db")
    connection.row_factory = as_dict
    adapter = SQLiteL1Adapter(connection=connection)
    assert connection.row_factory is as_dict
    record = adapter.remember("mapping row", {"kind": "test"})
    restored = adapter.get(record.source_id)
    assert restored is not None
    assert restored.content == "mapping row"
    assert adapter.get_revision(record.source_id) == 1
    assert adapter.health() == {"status": "healthy", "record_count": 1}
    adapter.close()
    assert connection.row_factory is as_dict
    connection.close()


def test_sqlite_l1_contract_recall_forget_revision_health_and_hard_delete(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    first, second = adapter.remember_batch([("alpha", {"n": 1}), ("beta", {})])
    assert {record.content for record in adapter.recall("a")} == {"alpha", "beta"}
    assert adapter.get_revision(first.source_id) == 1
    assert adapter.health() == {"status": "healthy", "record_count": 2}
    assert adapter.forget(first.source_id) is True
    assert adapter.get(first.source_id) is None
    assert adapter.get_revision(first.source_id) is None
    assert adapter.health() == {"status": "healthy", "record_count": 1}
    adapter.close()


def test_batch_is_atomic_and_metadata_is_validated(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    with pytest.raises(ValueError, match="JSON serializable"):
        adapter.remember_batch([("kept out", {}), ("rejected", {"bad": object()})])
    assert adapter.health()["record_count"] == 0
    with pytest.raises(ValueError, match="dictionary"):
        adapter.remember("bad", [])
    adapter.close()


def test_all_operations_fail_after_close_except_health(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")
    adapter.close()
    with pytest.raises(RuntimeError, match="closed"):
        adapter.remember("x", {})
    with pytest.raises(RuntimeError, match="closed"):
        adapter.recall("x")
    with pytest.raises(RuntimeError, match="closed"):
        adapter.forget("x")
    with pytest.raises(RuntimeError, match="closed"):
        adapter.get("x")
    with pytest.raises(RuntimeError, match="closed"):
        adapter.get_revision("x")
    assert adapter.health() == {"status": "closed"}


def test_external_connection_is_not_closed_or_mutated_by_adapter(tmp_path):
    connection = sqlite3.connect(tmp_path / "l1.db")
    original_factory = connection.row_factory
    adapter = SQLiteL1Adapter(connection=connection)
    adapter.remember("external", {})
    adapter.close()
    assert connection.row_factory is original_factory
    assert connection.execute("SELECT 1").fetchone()[0] == 1
    connection.close()


def test_mock_l1_injection_path_remains_unchanged():
    from hermes_tiered_memory.adapters.mock_l1 import MockL1Adapter
    from hermes_tiered_memory.adapters.l1 import L1Adapter

    assert issubclass(MockL1Adapter, L1Adapter)
