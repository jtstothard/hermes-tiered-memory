"""SQLite-backed L1 persistence boundary.

This module provides local persistence and an explicit transaction context around
one sqlite3 connection.  The connection/context is intentionally exposed for a
future caller to coordinate an L1 write with an attached outbox transaction.
It does not implement the live Mnemosyne adapter or atomic outbox writes.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hermes_tiered_memory.adapters.l1 import L1Adapter, L1Record


class SQLiteL1Adapter(L1Adapter):
    """Concrete durable L1 adapter backed by SQLite.

    Pass either ``db_path`` to let the adapter open and own a connection, or
    ``connection`` to inject an existing connection.  An injected connection
    remains the caller's responsibility and is never closed by ``close()``.
    ``transaction()`` is the explicit shared transaction seam for future L1 +
    outbox coordination; callers may use the yielded connection directly.
    """

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        connection: sqlite3.Connection | None = None,
    ) -> None:
        if (db_path is None) == (connection is None):
            raise ValueError("provide exactly one of db_path or connection")

        self._owns_connection = connection is None
        if connection is None:
            path = Path(db_path)  # type: ignore[arg-type]
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False
        self._initialize_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the underlying connection for explicit transaction sharing."""
        self._ensure_open()
        return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield the shared connection in one explicit commit/rollback boundary."""
        self._ensure_open()
        with self._lock:
            if self._connection.in_transaction:
                raise RuntimeError("cannot start an L1 transaction while one is active")
            self._connection.execute("BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._connection.rollback()
                raise
            else:
                self._connection.commit()

    def _initialize_schema(self) -> None:
        with self.transaction() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS l1_records (
                    source_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_deleted INTEGER NOT NULL DEFAULT 0
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_l1_records_content "
                "ON l1_records(content)"
            )

    def remember(self, content: str, metadata: dict[str, Any]) -> L1Record:
        self._ensure_open()
        source_id = f"sqlite-l1-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            connection.execute(
                """
                INSERT INTO l1_records
                    (source_id, revision, content, metadata, created_at, updated_at, is_deleted)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (source_id, 1, content, json.dumps(metadata, separators=(",", ":")),
                 now.isoformat(), now.isoformat(), 0),
            )
        record = self.get(source_id)
        if record is None:
            raise RuntimeError("SQLite L1 write was not readable after commit")
        return record

    def remember_batch(self, items: list[tuple[str, dict[str, Any]]]) -> list[L1Record]:
        self._ensure_open()
        records: list[L1Record] = []
        with self.transaction() as connection:
            for content, metadata in items:
                source_id = f"sqlite-l1-{uuid.uuid4().hex}"
                now = datetime.now(timezone.utc)
                connection.execute(
                    """
                    INSERT INTO l1_records
                        (source_id, revision, content, metadata, created_at, updated_at, is_deleted)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (source_id, 1, content, json.dumps(metadata, separators=(",", ":")),
                     now.isoformat(), now.isoformat(), 0),
                )
                records.append(
                    L1Record(source_id, 1, content, metadata, now, now, False)
                )
        return records

    def recall(self, query: str, limit: int = 10) -> list[L1Record]:
        self._ensure_open()
        rows = self.connection.execute(
            """
            SELECT * FROM l1_records
            WHERE is_deleted = 0 AND content LIKE ?
            ORDER BY updated_at DESC LIMIT ?
            """,
            (f"%{query}%", limit),
        ).fetchall()
        return [self._row_to_record(row) for row in rows]

    def forget(self, source_id: str) -> bool:
        self._ensure_open()
        with self.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM l1_records WHERE source_id = ? AND is_deleted = 0",
                (source_id,),
            )
        return cursor.rowcount > 0

    def get(self, source_id: str) -> L1Record | None:
        self._ensure_open()
        row = self.connection.execute(
            "SELECT * FROM l1_records WHERE source_id = ?", (source_id,)
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def get_revision(self, source_id: str) -> int | None:
        self._ensure_open()
        row = self.connection.execute(
            "SELECT revision FROM l1_records WHERE source_id = ?", (source_id,)
        ).fetchone()
        return int(row[0]) if row is not None else None

    def health(self) -> dict[str, Any]:
        if self._closed:
            return {"status": "closed"}
        count = self.connection.execute(
            "SELECT COUNT(*) FROM l1_records WHERE is_deleted = 0"
        ).fetchone()[0]
        return {"status": "healthy", "record_count": count}

    def close(self) -> None:
        if self._closed:
            return
        if self._owns_connection:
            self._connection.close()
        self._closed = True

    def _row_to_record(self, row: sqlite3.Row) -> L1Record:
        return L1Record(
            source_id=row["source_id"],
            revision=int(row["revision"]),
            content=row["content"],
            metadata=json.loads(row["metadata"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            is_deleted=bool(row["is_deleted"]),
        )

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLite L1 adapter is closed")
