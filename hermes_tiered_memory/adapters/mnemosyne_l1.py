"""Live, scoped adapter for Mnemosyne v3.14.x.

The upstream API has no revision or tombstone primitive.  This adapter keeps a
small sidecar ledger beside the explicitly selected Mnemosyne bank so the L1
contract can expose stable revisions and deleted-record state without relying
on Mnemosyne's process-global convenience functions.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from hermes_tiered_memory.adapters.l1 import L1Adapter, L1Record

_SCOPE_KEY = "__hermes_tiered_memory_scope__"
_METADATA_KEY = "__hermes_tiered_memory_metadata__"
_SOURCE = "hermes-tiered-memory"


class MnemosyneL1Adapter(L1Adapter):
    """Adapt one explicitly scoped Mnemosyne bank/session/channel to ``L1Adapter``.

    ``data_dir`` is mandatory.  This prevents a test or caller from silently
    falling back to Mnemosyne's ambient default database.
    """

    def __init__(
        self,
        *,
        data_dir: str | Path,
        profile: str,
        bank: str,
        session_id: str,
        channel_id: str,
    ) -> None:
        self._validate_scope(profile, bank, session_id, channel_id)
        self.data_dir = Path(data_dir)
        self.profile = profile
        self.bank = bank
        self.session_id = session_id
        self.channel_id = channel_id
        self._closed = False
        self._lock = threading.RLock()

        try:
            from mnemosyne.core.memory import Mnemosyne
        except ImportError as exc:
            raise RuntimeError("Mnemosyne v3.14.x dependency is unavailable") from exc

        bank_dir = self.data_dir / "banks" / self.bank
        bank_dir.mkdir(parents=True, exist_ok=True)
        self.db_path = bank_dir / "mnemosyne.db"
        self._ledger = sqlite3.connect(bank_dir / "tiered-memory-l1-ledger.sqlite", check_same_thread=False)
        self._initialize_ledger()
        try:
            self._memory = Mnemosyne(
                session_id=self.session_id,
                db_path=self.db_path,
                bank=self.bank,
                channel_id=self.channel_id,
            )
        except Exception:
            self._ledger.close()
            raise

    def remember(self, content: str, metadata: dict[str, Any]) -> L1Record:
        self._validate_input(content, metadata)
        with self._lock:
            self._ensure_open()
            try:
                source_id = self._memory.remember(
                    content,
                    source=_SOURCE,
                    metadata=self._encode_metadata(metadata),
                    scope="session",
                )
                if not isinstance(source_id, str) or not source_id:
                    raise RuntimeError("Mnemosyne remember returned no source identity")
                record = self._normalize_live(source_id)
                self._insert_ledger(record)
                return record
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Mnemosyne remember failed: {exc}") from exc

    def remember_batch(self, items: list[tuple[str, dict[str, Any]]]) -> list[L1Record]:
        for content, metadata in items:
            self._validate_input(content, metadata)
        if not items:
            return []
        with self._lock:
            self._ensure_open()
            upstream_items = [
                {
                    "content": content,
                    "source": _SOURCE,
                    "metadata": self._encode_metadata(metadata),
                    "channel_id": self.channel_id,
                }
                for content, metadata in items
            ]
            try:
                source_ids = self._memory.beam.remember_batch(upstream_items)
                if not isinstance(source_ids, list) or len(source_ids) != len(items):
                    raise RuntimeError("Mnemosyne batch response does not match requested item count")
                if any(not isinstance(source_id, str) or not source_id for source_id in source_ids):
                    raise RuntimeError("Mnemosyne batch response contains an invalid source identity")
                records = [self._normalize_live(source_id) for source_id in source_ids]
                with self._ledger:
                    for record in records:
                        self._insert_ledger(record, commit=False)
                return records
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Mnemosyne batch remember failed: {exc}") from exc

    def recall(self, query: str, limit: int = 10) -> list[L1Record]:
        if not isinstance(query, str) or not query:
            raise ValueError("query must be a non-empty string")
        if not isinstance(limit, int) or limit < 1:
            raise ValueError("limit must be a positive integer")
        with self._lock:
            self._ensure_open()
            try:
                raw_results = self._memory.recall(query, top_k=limit, channel_id=self.channel_id)
                if not isinstance(raw_results, list):
                    raise RuntimeError("Mnemosyne recall returned a malformed response")
                records: list[L1Record] = []
                for item in raw_results:
                    if not isinstance(item, dict) or not isinstance(item.get("id"), str):
                        raise RuntimeError("Mnemosyne recall returned a malformed record")
                    record = self._normalize_live(item["id"])
                    if not record.is_deleted:
                        records.append(record)
                return records[:limit]
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Mnemosyne recall failed: {exc}") from exc

    def get(self, source_id: str) -> L1Record | None:
        with self._lock:
            self._ensure_open()
            ledger = self._ledger_row(source_id)
            if ledger is None:
                return None
            if ledger["is_deleted"]:
                return self._record_from_ledger(ledger)
            try:
                return self._normalize_live(source_id, ledger=ledger)
            except RuntimeError:
                raise
            except Exception as exc:
                raise RuntimeError(f"Mnemosyne get failed: {exc}") from exc

    def get_revision(self, source_id: str) -> int | None:
        with self._lock:
            self._ensure_open()
            row = self._ledger_row(source_id)
            return int(row["revision"]) if row is not None else None

    def forget(self, source_id: str) -> bool:
        with self._lock:
            self._ensure_open()
            existing = self.get(source_id)
            if existing is None:
                return False
            if existing.is_deleted:
                return True
            try:
                if self._memory.forget(source_id) is not True:
                    return False
                tombstone = L1Record(
                    source_id=source_id,
                    revision=existing.revision + 1,
                    content=existing.content,
                    metadata=existing.metadata,
                    created_at=existing.created_at,
                    updated_at=datetime.now(timezone.utc),
                    is_deleted=True,
                )
                self._replace_ledger(tombstone)
                return True
            except Exception as exc:
                raise RuntimeError(f"Mnemosyne forget failed: {exc}") from exc

    def health(self) -> dict[str, Any]:
        with self._lock:
            if self._closed:
                return {"status": "closed"}
            try:
                self._memory.conn.execute("SELECT 1").fetchone()
                count = self._ledger.execute(
                    "SELECT COUNT(*) FROM records WHERE is_deleted = 0"
                ).fetchone()[0]
                return {
                    "status": "healthy",
                    "profile": self.profile,
                    "bank": self.bank,
                    "session_id": self.session_id,
                    "channel_id": self.channel_id,
                    "record_count": count,
                }
            except Exception as exc:
                return {"status": "unhealthy", "error": str(exc)}

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            errors: list[BaseException] = []
            for connection in (getattr(self._memory, "beam", None), self._memory):
                conn = getattr(connection, "conn", None)
                if conn is not None:
                    try:
                        conn.close()
                    except Exception as exc:  # preserve shutdown failure for callers
                        errors.append(exc)
            try:
                self._ledger.close()
            except Exception as exc:
                errors.append(exc)
            self._closed = True
            if errors:
                raise RuntimeError(f"Mnemosyne close failed: {errors[0]}") from errors[0]

    def _normalize_live(self, source_id: str, *, ledger: sqlite3.Row | None = None) -> L1Record:
        raw = self._memory.get(source_id)
        if not isinstance(raw, dict):
            raise RuntimeError("Mnemosyne returned a malformed record")
        if raw.get("id") != source_id or not isinstance(raw.get("content"), str):
            raise RuntimeError("Mnemosyne returned a malformed record")
        metadata = self._decode_metadata(raw.get("metadata"))
        if ledger is not None and int(ledger["revision"]) != 1:
            raise RuntimeError("live Mnemosyne record disagrees with local revision ledger")
        timestamp = self._parse_timestamp(raw.get("created_at") or raw.get("timestamp"))
        return L1Record(
            source_id=source_id,
            revision=1 if ledger is None else int(ledger["revision"]),
            content=raw["content"],
            metadata=metadata,
            created_at=timestamp,
            updated_at=self._parse_timestamp(raw.get("timestamp") or raw.get("created_at")),
            is_deleted=False,
        )

    def _encode_metadata(self, metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            _SCOPE_KEY: {
                "profile": self.profile,
                "bank": self.bank,
                "session_id": self.session_id,
                "channel_id": self.channel_id,
                "source": _SOURCE,
            },
            _METADATA_KEY: metadata,
        }

    def _decode_metadata(self, value: Any) -> dict[str, Any]:
        if isinstance(value, str):
            try:
                value = json.loads(value)
            except json.JSONDecodeError as exc:
                raise RuntimeError("Mnemosyne record metadata is malformed") from exc
        if not isinstance(value, dict):
            raise RuntimeError("Mnemosyne record metadata is malformed")
        scope = value.get(_SCOPE_KEY)
        metadata = value.get(_METADATA_KEY)
        if not isinstance(scope, dict) or not isinstance(metadata, dict):
            raise RuntimeError("Mnemosyne record is missing adapter provenance")
        expected = {
            "profile": self.profile,
            "bank": self.bank,
            "session_id": self.session_id,
            "channel_id": self.channel_id,
            "source": _SOURCE,
        }
        if scope != expected:
            raise RuntimeError("Mnemosyne record is outside this adapter scope")
        return metadata

    def _initialize_ledger(self) -> None:
        self._ledger.row_factory = sqlite3.Row
        with self._ledger:
            self._ledger.execute(
                """CREATE TABLE IF NOT EXISTS records (
                    source_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_deleted INTEGER NOT NULL CHECK (is_deleted IN (0, 1))
                )"""
            )

    def _insert_ledger(self, record: L1Record, *, commit: bool = True) -> None:
        if self._ledger_row(record.source_id) is not None:
            raise RuntimeError("Mnemosyne returned an existing source identity; refusing unsafe overwrite")
        self._replace_ledger(record, commit=commit)

    def _replace_ledger(self, record: L1Record, *, commit: bool = True) -> None:
        statement = (
            "INSERT OR REPLACE INTO records "
            "(source_id, revision, content, metadata_json, created_at, updated_at, is_deleted) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        values = (
            record.source_id,
            record.revision,
            record.content,
            json.dumps(record.metadata, separators=(",", ":")),
            record.created_at.isoformat(),
            record.updated_at.isoformat(),
            int(record.is_deleted),
        )
        if commit:
            with self._ledger:
                self._ledger.execute(statement, values)
        else:
            self._ledger.execute(statement, values)

    def _ledger_row(self, source_id: str) -> sqlite3.Row | None:
        if not isinstance(source_id, str) or not source_id:
            return None
        return self._ledger.execute("SELECT * FROM records WHERE source_id = ?", (source_id,)).fetchone()

    @staticmethod
    def _record_from_ledger(row: sqlite3.Row) -> L1Record:
        return L1Record(
            source_id=row["source_id"],
            revision=int(row["revision"]),
            content=row["content"],
            metadata=json.loads(row["metadata_json"]),
            created_at=MnemosyneL1Adapter._parse_timestamp(row["created_at"]),
            updated_at=MnemosyneL1Adapter._parse_timestamp(row["updated_at"]),
            is_deleted=bool(row["is_deleted"]),
        )

    @staticmethod
    def _parse_timestamp(value: Any) -> datetime:
        if not isinstance(value, str) or not value:
            raise RuntimeError("Mnemosyne record is missing a timestamp")
        try:
            result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise RuntimeError("Mnemosyne record contains an invalid timestamp") from exc
        return result if result.tzinfo is not None else result.replace(tzinfo=timezone.utc)

    @staticmethod
    def _validate_scope(profile: str, bank: str, session_id: str, channel_id: str) -> None:
        for name, value in {
            "profile": profile,
            "bank": bank,
            "session_id": session_id,
            "channel_id": channel_id,
        }.items():
            if not isinstance(value, str) or not value:
                raise ValueError(f"{name} must be a non-empty string")
        if not all(character.isalnum() or character in "-_" for character in bank):
            raise ValueError("bank must contain only alphanumeric characters, hyphens, or underscores")

    @staticmethod
    def _validate_input(content: str, metadata: dict[str, Any]) -> None:
        if not isinstance(content, str) or not content:
            raise ValueError("content must be a non-empty string")
        if not isinstance(metadata, dict):
            raise ValueError("metadata must be a dictionary")
        try:
            json.dumps(metadata, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise ValueError("metadata must be JSON serializable") from exc

    def _ensure_open(self) -> None:
        if self._closed:
            raise RuntimeError("Mnemosyne L1 adapter is closed")
