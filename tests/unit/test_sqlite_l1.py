"""Focused tests for the concrete SQLite L1 persistence boundary."""

import sqlite3
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


def test_shared_transaction_api_exposes_adapter_owned_connection(tmp_path):
    adapter = SQLiteL1Adapter(tmp_path / "l1.db")

    with adapter.transaction() as connection:
        assert isinstance(connection, sqlite3.Connection)
        assert connection is adapter.connection
        assert connection.execute("SELECT 1").fetchone()[0] == 1

    adapter.close()
    with pytest.raises(sqlite3.ProgrammingError):
        connection.execute("SELECT 1")


def test_external_connection_is_not_closed_by_adapter(tmp_path):
    connection = sqlite3.connect(tmp_path / "l1.db")
    adapter = SQLiteL1Adapter(connection=connection)

    adapter.remember("external", {})
    adapter.close()

    assert connection.execute("SELECT 1").fetchone()[0] == 1
    connection.close()


def test_mock_l1_injection_path_remains_unchanged():
    from hermes_tiered_memory.adapters.mock_l1 import MockL1Adapter
    from hermes_tiered_memory.adapters.l1 import L1Adapter

    assert issubclass(MockL1Adapter, L1Adapter)
