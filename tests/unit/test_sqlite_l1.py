"""Focused tests for the concrete SQLite L1 persistence boundary."""

import sqlite3
import threading
import time
from datetime import datetime, timezone

import pytest

from hermes_tiered_memory.adapters.sqlite_l1 import SQLiteL1Adapter


def test_l1_records_persist_across_adapter_restarts(tmp_path):
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
