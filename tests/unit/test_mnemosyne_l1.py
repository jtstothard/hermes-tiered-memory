"""Isolation tests for the live Mnemosyne L1 adapter sidecar ledger."""

from __future__ import annotations

import sqlite3
import sys
from types import ModuleType

import pytest


@pytest.fixture
def fake_mnemosyne(monkeypatch):
    records: dict[str, dict] = {}
    next_id = 0

    class Beam:
        def __init__(self, memory):
            self.memory = memory
            self.conn = memory.conn

        def remember_batch(self, items):
            return [
                self.memory.remember(
                    item["content"], source=item["source"], metadata=item["metadata"]
                )
                for item in items
            ]

    class Mnemosyne:
        def __init__(self, *, session_id, db_path, bank, channel_id):
            self.session_id = session_id
            self.bank = bank
            self.channel_id = channel_id
            self.conn = sqlite3.connect(":memory:")
            self.beam = Beam(self)

        def remember(self, content, *, source, metadata, scope):
            nonlocal next_id
            next_id += 1
            source_id = f"record-{next_id}"
            records[source_id] = {
                "id": source_id,
                "content": content,
                "metadata": metadata,
                "created_at": "2026-01-01T00:00:00+00:00",
                "timestamp": "2026-01-01T00:00:00+00:00",
            }
            return source_id

        def get(self, source_id):
            return records.get(source_id)

        def forget(self, source_id):
            return source_id in records

        def recall(self, query, *, top_k, channel_id):
            return []

    mnemosyne = ModuleType("mnemosyne")
    core = ModuleType("mnemosyne.core")
    memory = ModuleType("mnemosyne.core.memory")
    memory.Mnemosyne = Mnemosyne
    monkeypatch.setitem(sys.modules, "mnemosyne", mnemosyne)
    monkeypatch.setitem(sys.modules, "mnemosyne.core", core)
    monkeypatch.setitem(sys.modules, "mnemosyne.core.memory", memory)


def _adapter(tmp_path, profile, session_id, channel_id):
    from hermes_tiered_memory.adapters.mnemosyne_l1 import MnemosyneL1Adapter

    return MnemosyneL1Adapter(
        data_dir=tmp_path,
        profile=profile,
        bank="shared-bank",
        session_id=session_id,
        channel_id=channel_id,
    )


def test_shared_bank_ledger_is_fully_scoped_for_reads_tombstones_and_health(
    tmp_path, fake_mnemosyne
):
    owner = _adapter(tmp_path, "owner", "owner-session", "owner-channel")
    foreign = _adapter(tmp_path, "foreign", "foreign-session", "foreign-channel")
    try:
        record = owner.remember("owner-only", {"owner": True})

        assert foreign.get(record.source_id) is None
        assert foreign.get_revision(record.source_id) is None
        assert foreign.health()["record_count"] == 0

        assert owner.forget(record.source_id) is True
        tombstone = owner.get(record.source_id)
        assert tombstone is not None
        assert tombstone.is_deleted is True
        assert tombstone.revision == record.revision + 1
        assert owner.get_revision(record.source_id) == tombstone.revision
        assert foreign.get(record.source_id) is None
        assert foreign.get_revision(record.source_id) is None
        assert foreign.forget(record.source_id) is False
        assert foreign.health()["record_count"] == 0
    finally:
        owner.close()
        foreign.close()


def test_legacy_unscoped_ledger_rows_are_migrated_fail_closed(tmp_path, fake_mnemosyne):
    ledger_path = tmp_path / "banks" / "shared-bank" / "tiered-memory-l1-ledger.sqlite"
    ledger_path.parent.mkdir(parents=True)
    ledger = sqlite3.connect(ledger_path)
    ledger.execute("""CREATE TABLE records (
            source_id TEXT PRIMARY KEY,
            revision INTEGER NOT NULL,
            content TEXT NOT NULL,
            metadata_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            is_deleted INTEGER NOT NULL
        )""")
    ledger.execute(
        "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-deleted",
            2,
            "deleted secret",
            "{}",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            1,
        ),
    )
    ledger.execute(
        "INSERT INTO records VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "legacy-live",
            1,
            "live secret",
            "{}",
            "2026-01-01T00:00:00+00:00",
            "2026-01-01T00:00:00+00:00",
            0,
        ),
    )
    ledger.commit()
    ledger.close()

    adapter = _adapter(tmp_path, "profile", "session", "channel")
    foreign = _adapter(tmp_path, "foreign", "foreign-session", "foreign-channel")
    try:
        for source_id in ("legacy-deleted", "legacy-live"):
            assert adapter.get(source_id) is None
            assert adapter.get_revision(source_id) is None
            assert adapter.forget(source_id) is False
            assert foreign.get(source_id) is None
            assert foreign.get_revision(source_id) is None
            assert foreign.forget(source_id) is False
        assert adapter.health()["record_count"] == 0
        assert foreign.health()["record_count"] == 0
    finally:
        adapter.close()
        foreign.close()
