"""Durable outbox storage for crash-safe L2 promotion."""

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any

from hermes_tiered_memory.adapters.l1 import L1Record


class OutboxState(str, Enum):
    """States for outbox items."""

    PENDING = "pending"
    IN_FLIGHT = "in_flight"
    UNCERTAIN = "uncertain"
    PROMOTED = "promoted"
    QUARANTINED = "quarantined"


class TerminalOutcome(str, Enum):
    """Terminal outcome classifications."""

    NONE = "none"
    CONFIRMED_PERMANENT = "confirmed_permanent"
    AUTOMATIC_RETRY_QUARANTINE = "automatic_retry_quarantine"
    UNRESOLVED_OUTCOME_QUARANTINE = "unresolved_outcome_quarantine"


@dataclass
class OutboxItem:
    """An item in the outbox awaiting L2 promotion."""

    row_id: int | None = None
    document_id: str = ""
    mnemosyne_id: str = ""
    mnemosyne_rev: int = 0
    content_snapshot: str = ""
    metadata_snapshot: str = ""  # JSON string
    state: OutboxState = OutboxState.PENDING
    state_version: int = 0
    terminal_outcome: TerminalOutcome = TerminalOutcome.NONE
    owner_id: str = ""
    lease_id: str = ""
    lease_expires_at: int = 0
    fence: int = 0
    next_retry_at: int = 0
    attempt_no: int = 0
    retry_cycle_no: int = 0
    quarantine_origin_at: int = 0
    last_error_class: str = ""
    last_http_status: int = 0
    created_at: int = 0
    updated_at: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "row_id": self.row_id,
            "document_id": self.document_id,
            "mnemosyne_id": self.mnemosyne_id,
            "mnemosyne_rev": self.mnemosyne_rev,
            "content_snapshot": self.content_snapshot,
            "metadata_snapshot": self.metadata_snapshot,
            "state": self.state.value,
            "state_version": self.state_version,
            "terminal_outcome": self.terminal_outcome.value,
            "owner_id": self.owner_id,
            "lease_id": self.lease_id,
            "lease_expires_at": self.lease_expires_at,
            "fence": self.fence,
            "next_retry_at": self.next_retry_at,
            "attempt_no": self.attempt_no,
            "retry_cycle_no": self.retry_cycle_no,
            "quarantine_origin_at": self.quarantine_origin_at,
            "last_error_class": self.last_error_class,
            "last_http_status": self.last_http_status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "OutboxItem":
        """Create from dictionary."""
        return cls(
            row_id=data.get("row_id"),
            document_id=data["document_id"],
            mnemosyne_id=data["mnemosyne_id"],
            mnemosyne_rev=data["mnemosyne_rev"],
            content_snapshot=data["content_snapshot"],
            metadata_snapshot=data["metadata_snapshot"],
            state=OutboxState(data["state"]),
            state_version=data["state_version"],
            terminal_outcome=TerminalOutcome(data.get("terminal_outcome", TerminalOutcome.NONE.value)),
            owner_id=data.get("owner_id", ""),
            lease_id=data.get("lease_id", ""),
            lease_expires_at=data.get("lease_expires_at", 0),
            fence=data.get("fence", 0),
            next_retry_at=data.get("next_retry_at", 0),
            attempt_no=data.get("attempt_no", 0),
            retry_cycle_no=data.get("retry_cycle_no", 0),
            quarantine_origin_at=data.get("quarantine_origin_at", 0),
            last_error_class=data.get("last_error_class", ""),
            last_http_status=data.get("last_http_status", 0),
            created_at=data["created_at"],
            updated_at=data["updated_at"],
        )


class OutboxStore:
    """Durable SQLite-based outbox store with crash recovery."""

    SCHEMA_VERSION = 1

    def __init__(self, db_path: Path):
        """Initialize the outbox store."""
        self.db_path = db_path
        self._local = threading.local()
        self._lock = threading.Lock()

        # Ensure parent directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Initialize schema
        self._initialize_schema()

    def _get_connection(self) -> sqlite3.Connection:
        """Get a thread-local connection."""
        if not hasattr(self._local, "conn"):
            self._local.conn = sqlite3.connect(
                self.db_path, check_same_thread=False
            )
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    def _initialize_schema(self) -> None:
        """Initialize the outbox schema."""
        conn = self._get_connection()
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                row_id INTEGER PRIMARY KEY AUTOINCREMENT,
                document_id TEXT NOT NULL,
                mnemosyne_id TEXT NOT NULL,
                mnemosyne_rev INTEGER NOT NULL,
                content_snapshot TEXT NOT NULL,
                metadata_snapshot TEXT NOT NULL,
                state TEXT NOT NULL,
                state_version INTEGER NOT NULL,
                terminal_outcome TEXT NOT NULL DEFAULT 'none',
                owner_id TEXT,
                lease_id TEXT,
                lease_expires_at INTEGER,
                fence INTEGER NOT NULL DEFAULT 0,
                next_retry_at INTEGER,
                attempt_no INTEGER NOT NULL DEFAULT 0,
                retry_cycle_no INTEGER NOT NULL DEFAULT 0,
                quarantine_origin_at INTEGER,
                last_error_class TEXT,
                last_http_status INTEGER,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL,
                UNIQUE(document_id, mnemosyne_rev)
            )
        """
        )
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_outbox_state_retry
            ON outbox(state, next_retry_at)
        """
        )
        conn.commit()

    def enqueue(
        self,
        document_id: str,
        mnemosyne_id: str,
        mnemosyne_rev: int,
        content: str,
        metadata: dict[str, Any],
    ) -> OutboxItem:
        """Enqueue an item for promotion. Returns the created item."""
        now = int(time.time())
        metadata_json = json.dumps(metadata, separators=(",", ":"))

        conn = self._get_connection()
        cursor = conn.execute(
            """
            INSERT INTO outbox (
                document_id, mnemosyne_id, mnemosyne_rev,
                content_snapshot, metadata_snapshot,
                state, state_version, terminal_outcome,
                fence, attempt_no, retry_cycle_no,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                document_id,
                mnemosyne_id,
                mnemosyne_rev,
                content,
                metadata_json,
                OutboxState.PENDING.value,
                0,
                TerminalOutcome.NONE.value,
                0,
                0,
                0,
                now,
                now,
            ),
        )
        conn.commit()

        row_id = cursor.lastrowid
        item = self.get(row_id)
        if item is None:
            raise RuntimeError("Failed to retrieve enqueued item")
        return item

    def get(self, row_id: int) -> OutboxItem | None:
        """Get an item by row_id."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM outbox WHERE row_id = ?", (row_id,)
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return OutboxItem.from_dict(dict(row))

    def get_by_identity(
        self, document_id: str, mnemosyne_rev: int
    ) -> OutboxItem | None:
        """Get an item by logical identity."""
        conn = self._get_connection()
        cursor = conn.execute(
            "SELECT * FROM outbox WHERE document_id = ? AND mnemosyne_rev = ?",
            (document_id, mnemosyne_rev),
        )
        row = cursor.fetchone()
        if row is None:
            return None
        return OutboxItem.from_dict(dict(row))

    def claim_batch(
        self,
        batch_size: int,
        owner_id: str,
        lease_id: str,
        lease_duration_seconds: int,
    ) -> list[OutboxItem]:
        """
        Claim a batch of pending items for processing.
        Returns the claimed items with leases.
        """
        now = int(time.time())
        lease_expires_at = now + lease_duration_seconds

        conn = self._get_connection()

        # Select and claim pending items in a transaction
        cursor = conn.execute(
            """
            SELECT row_id FROM outbox
            WHERE state = 'pending'
              AND (next_retry_at IS NULL OR next_retry_at <= ?)
            ORDER BY row_id
            LIMIT ?
            """,
            (now, batch_size),
        )

        row_ids = [row[0] for row in cursor.fetchall()]
        if not row_ids:
            return []

        # Claim each item
        items = []
        for row_id in row_ids:
            if row_id is None:
                continue
            new_fence = now ^ row_id  # Simple deterministic fencing

            cursor = conn.execute(
                """
                UPDATE outbox
                SET state = 'in_flight',
                    state_version = state_version + 1,
                    owner_id = ?,
                    lease_id = ?,
                    lease_expires_at = ?,
                    fence = ?,
                    updated_at = ?
                WHERE row_id = ? AND state = 'pending'
                """,
                (owner_id, lease_id, lease_expires_at, new_fence, now, row_id),
            )

            if cursor.rowcount == 0:
                # Someone else claimed it
                continue

            item = self.get(row_id)
            if item:
                items.append(item)

        conn.commit()
        return items

    def update_state(
        self,
        row_id: int,
        expected_state_version: int,
        new_state: OutboxState,
        terminal_outcome: TerminalOutcome | None = None,
        error_class: str | None = None,
        http_status: int | None = None,
        next_retry_at: int | None = None,
        increment_attempt: bool = False,
    ) -> bool:
        """
        Update an item's state with CAS protection.
        Returns True if the update succeeded.
        """
        now = int(time.time())
        set_clauses = ["state = ?", "state_version = state_version + 1", "updated_at = ?"]
        params = [new_state.value, now]

        if terminal_outcome is not None:
            set_clauses.append("terminal_outcome = ?")
            params.append(terminal_outcome.value)

        if error_class is not None:
            set_clauses.append("last_error_class = ?")
            params.append(error_class)

        if http_status is not None:
            set_clauses.append("last_http_status = ?")
            params.append(http_status)

        if next_retry_at is not None:
            set_clauses.append("next_retry_at = ?")
            params.append(next_retry_at)

        if increment_attempt:
            set_clauses.append("attempt_no = attempt_no + 1")

        # Set quarantine_origin_at when entering quarantine
        if new_state == OutboxState.QUARANTINED:
            set_clauses.append(
                "quarantine_origin_at = CASE WHEN quarantine_origin_at IS NULL OR quarantine_origin_at = 0 THEN ? ELSE quarantine_origin_at END"
            )
            params.append(now)

        params.extend([row_id, expected_state_version])

        sql = f"""
            UPDATE outbox
            SET {', '.join(set_clauses)}
            WHERE row_id = ? AND state_version = ?
        """

        conn = self._get_connection()
        cursor = conn.execute(sql, params)
        conn.commit()

        return cursor.rowcount > 0

    def get_stats(self) -> dict[str, int]:
        """Get statistics about the outbox."""
        conn = self._get_connection()
        stats = {}

        for state in OutboxState:
            cursor = conn.execute(
                "SELECT COUNT(*) FROM outbox WHERE state = ?", (state.value,)
            )
            stats[state.value] = cursor.fetchone()[0]

        return stats

    def close(self) -> None:
        """Close the database connection."""
        if hasattr(self._local, "conn"):
            self._local.conn.close()
            delattr(self._local, "conn")