"""SQLite-backed L1 persistence boundary.

This is a local, non-live Mnemosyne seam.  It intentionally does not provide
atomic outbox writes.  Tombstones and delete ordering belong to a later slice;
``forget`` therefore uses hard delete here.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_tiered_memory.outbox.store import OutboxStore
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from hermes_tiered_memory.adapters.l1 import L1Adapter, L1Record


_SCHEMA_VERSION = 1
_SCHEMA_COLUMNS = (
    ("source_id", "TEXT", 0, None, 1),
    ("revision", "INTEGER", 1, None, 0),
    ("content", "TEXT", 1, None, 0),
    ("metadata", "TEXT", 1, None, 0),
    ("created_at", "TEXT", 1, None, 0),
    ("updated_at", "TEXT", 1, None, 0),
    ("is_deleted", "INTEGER", 1, "0", 0),
)


def _row_value(row: Any, key: str, index: int) -> Any:
    """Read a SQLite row regardless of the connection's row factory shape."""
    if isinstance(row, Mapping):
        return row[key]
    return row[index]


class SQLiteL1Adapter(L1Adapter):
    """Concrete durable L1 adapter backed by one serialized SQLite connection.

    ``transaction`` holds the adapter lock for the complete transaction,
    including commit/rollback.  ``close`` takes that same lock, so it waits for
    an active transaction and no SQLite operation can race connection close.
    Injected connections remain caller-owned and their ``row_factory`` is never
    changed.
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
        self._connection = connection
        self._lock = threading.RLock()
        self._closed = False
        self._attached_outbox_schema: str | None = None
        self._initialize_schema()

    def configure_atomic_outbox(self, outbox_store: "OutboxStore") -> None:
        """Attach the outbox DB using rollback journals for atomic commits."""
        with self._lock:
            self._ensure_open()
            if self._attached_outbox_schema is not None:
                raise RuntimeError("atomic outbox is already configured")
            attached = False
            try:
                self._connection.execute("ATTACH DATABASE ? AS outbox", (str(outbox_store.db_path),))
                attached = True
                outbox_store.prepare_for_atomic_attach(self._connection, "outbox")
                outbox_store.initialize_schema_on(self._connection, "outbox")
                self._connection.commit()
                self._attached_outbox_schema = "outbox"
            except BaseException:
                self._rollback()
                if attached:
                    try:
                        self._connection.execute("DETACH DATABASE outbox")
                    except sqlite3.DatabaseError:
                        pass
                raise

    @property
    def supports_atomic_outbox(self) -> bool:
        return self._attached_outbox_schema is not None

    def remember_with_outbox(self, content: str, metadata: dict[str, Any], *,
                             document_id_factory: Callable[[str], str],
                             promotion_metadata_factory: Callable[[L1Record], dict[str, Any]],
                             outbox_store: "OutboxStore") -> L1Record:
        """Store L1 and its promotion row in one ATTACH transaction.

        The provider supplies the promotion envelope through a callback; this
        adapter does not depend on provider metadata-building internals.
        """
        self._validate_metadata(metadata)
        if not self.supports_atomic_outbox:
            raise RuntimeError("SQLite L1 adapter has no configured atomic outbox")
        source_id = f"sqlite-l1-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            metadata_json = json.dumps(metadata, separators=(",", ":"))
            connection.execute(
                "INSERT INTO l1_records (source_id, revision, content, metadata, created_at, updated_at, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_id, 1, content, metadata_json, now.isoformat(), now.isoformat(), 0),
            )
            record = L1Record(source_id, 1, content, metadata, now, now, False)
            outbox_store.enqueue_on(connection, schema="outbox", document_id=document_id_factory(source_id),
                                    mnemosyne_id=source_id, mnemosyne_rev=1,
                                    content=content, metadata=promotion_metadata_factory(record))
        return record

    @property
    def connection(self) -> sqlite3.Connection:
        """Return the connection; execute on it only inside ``transaction``."""
        with self._lock:
            self._ensure_open()
            return self._connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Yield the connection under the shared commit/rollback/close lock."""
        with self._lock:
            self._ensure_open()
            if self._connection.in_transaction:
                raise RuntimeError("cannot start an L1 transaction while one is active")
            self._connection.execute("BEGIN")
            try:
                yield self._connection
            except BaseException:
                self._rollback()
                raise
            else:
                try:
                    self._connection.commit()
                except BaseException:
                    # A failed commit may leave the connection in a transaction.
                    # Roll it back before releasing the lock and preserve the
                    # original commit exception as the observable failure.
                    self._rollback()
                    raise

    def _initialize_schema(self) -> None:
        with self._lock:
            self._ensure_open()
            version_row = self._connection.execute("PRAGMA user_version").fetchone()
            version = int(_row_value(version_row, "user_version", 0))
            exists = self._connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'l1_records'"
            ).fetchone()
            if exists and version != _SCHEMA_VERSION:
                raise RuntimeError(
                    f"unsupported existing l1_records schema: user_version={version}; "
                    f"expected {_SCHEMA_VERSION}"
                )
            if not exists and version not in (0, _SCHEMA_VERSION):
                raise RuntimeError(f"unsupported SQLite schema version: {version}")

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
                columns = tuple(
                    tuple(_row_value(row, key, index) for key, index in (
                        ("name", 1),
                        ("type", 2),
                        ("notnull", 3),
                        ("dflt_value", 4),
                        ("pk", 5),
                    ))
                    for row in connection.execute("PRAGMA table_info(l1_records)").fetchall()
                )
                if columns != _SCHEMA_COLUMNS:
                    raise RuntimeError("unsupported existing l1_records schema: columns do not match")
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS idx_l1_records_content "
                    "ON l1_records(content)"
                )
                connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")

    def remember(self, content: str, metadata: dict[str, Any]) -> L1Record:
        self._validate_metadata(metadata)
        source_id = f"sqlite-l1-{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc)
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO l1_records "
                "(source_id, revision, content, metadata, created_at, updated_at, is_deleted) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (source_id, 1, content, json.dumps(metadata, separators=(",", ":")),
                 now.isoformat(), now.isoformat(), 0),
            )
        record = self.get(source_id)
        if record is None:
            raise RuntimeError("SQLite L1 write was not readable after commit")
        return record

    def remember_batch(self, items: list[tuple[str, dict[str, Any]]]) -> list[L1Record]:
        for content, metadata in items:
            self._validate_metadata(metadata)
        records: list[L1Record] = []
        with self.transaction() as connection:
            for content, metadata in items:
                source_id = f"sqlite-l1-{uuid.uuid4().hex}"
                now = datetime.now(timezone.utc)
                connection.execute(
                    "INSERT INTO l1_records "
                    "(source_id, revision, content, metadata, created_at, updated_at, is_deleted) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (source_id, 1, content, json.dumps(metadata, separators=(",", ":")),
                     now.isoformat(), now.isoformat(), 0),
                )
                records.append(L1Record(source_id, 1, content, metadata, now, now, False))
        return records

    def recall(self, query: str, limit: int = 10) -> list[L1Record]:
        with self._lock:
            self._ensure_open()
            cursor = self._connection.execute(
                "SELECT source_id, revision, content, metadata, created_at, updated_at, is_deleted "
                "FROM l1_records WHERE is_deleted = 0 AND content LIKE ? "
                "ORDER BY updated_at DESC LIMIT ?", (f"%{query}%", limit)
            )
            return [self._row_to_record(row, cursor.description) for row in cursor.fetchall()]

    def forget(self, source_id: str) -> bool:
        # Hard delete is intentional for this prerequisite seam.  Tombstones and
        # cross-tier delete ordering are deferred to the later outbox slice.
        with self.transaction() as connection:
            cursor = connection.execute("DELETE FROM l1_records WHERE source_id = ?", (source_id,))
            return cursor.rowcount > 0

    def get(self, source_id: str) -> L1Record | None:
        with self._lock:
            self._ensure_open()
            cursor = self._connection.execute(
                "SELECT source_id, revision, content, metadata, created_at, updated_at, is_deleted "
                "FROM l1_records WHERE source_id = ?", (source_id,)
            )
            row = cursor.fetchone()
            return self._row_to_record(row, cursor.description) if row is not None else None

    def get_revision(self, source_id: str) -> int | None:
        with self._lock:
            self._ensure_open()
            row = self._connection.execute(
                "SELECT revision AS revision FROM l1_records WHERE source_id = ?", (source_id,)
            ).fetchone()
            return int(_row_value(row, "revision", 0)) if row is not None else None

    def health(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {"status": "closed"}
            row = self._connection.execute(
                "SELECT COUNT(*) AS record_count FROM l1_records WHERE is_deleted = 0"
            ).fetchone()
            count = _row_value(row, "record_count", 0)
            return {"status": "healthy", "record_count": count}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            if self._connection.in_transaction:
                self._rollback()
            if self._owns_connection:
                self._connection.close()
            self._closed = True

    def _rollback(self) -> None:
        try:
            self._connection.rollback()
        except sqlite3.ProgrammingError:
            # The connection can only be externally closed through the exposed
            # escape hatch; do not mask the transaction's original exception.
            pass

    @staticmethod
    def _row_to_record(row: Any, description: Any) -> L1Record:
        names = [column[0] for column in description]
        values = row if isinstance(row, Mapping) else dict(zip(names, row))
        return L1Record(
            source_id=values["source_id"], revision=int(values["revision"]),
            content=values["content"], metadata=json.loads(values["metadata"]),
            created_at=datetime.fromisoformat(values["created_at"]),
            updated_at=datetime.fromisoformat(values["updated_at"]),
            is_deleted=bool(values["is_deleted"]),
        )

    @staticmethod
    def _validate_metadata(metadata: Any) -> None:
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        try:
            json.dumps(metadata, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("SQLite L1 adapter is closed")
