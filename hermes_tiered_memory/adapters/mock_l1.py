"""Mock L1 adapter for testing."""

from datetime import datetime
from typing import Any

from hermes_tiered_memory.adapters.l1 import L1Adapter, L1Record


class MockL1Adapter(L1Adapter):
    """Mock L1 adapter for testing."""

    def __init__(self):
        """Initialize the mock L1 adapter."""
        self._records: dict[str, tuple[int, L1Record]] = {}
        self._closed = False

    def remember(self, content: str, metadata: Any) -> L1Record:
        """Store a record in L1."""
        source_id = f"mnemosyne-{len(self._records)}"
        revision = 1
        now = datetime.utcnow()

        record = L1Record(
            source_id=source_id,
            revision=revision,
            content=content,
            metadata=metadata,
            created_at=now,
            updated_at=now,
            is_deleted=False,
        )

        self._records[source_id] = (revision, record)
        return record

    def remember_batch(self, items: Any) -> list[L1Record]:
        """Store multiple records in L1."""
        return [self.remember(content, metadata) for content, metadata in items]

    def recall(self, query: str, limit: int = 10) -> list[L1Record]:
        """Query L1 for relevant records."""
        # Simple substring match for testing
        results = []
        query_lower = query.lower()

        for revision, record in self._records.values():
            if len(results) >= limit:
                break
            if record.is_deleted:
                continue
            if query_lower in record.content.lower():
                results.append(record)

        return results

    def forget(self, source_id: str) -> bool:
        """Delete a record from L1."""
        if source_id not in self._records:
            return False

        revision, record = self._records[source_id]
        # Create a tombstone
        tombstone = L1Record(
            source_id=record.source_id,
            revision=revision + 1,
            content=record.content,
            metadata=record.metadata,
            created_at=record.created_at,
            updated_at=datetime.utcnow(),
            is_deleted=True,
        )
        self._records[source_id] = (revision + 1, tombstone)
        return True

    def get(self, source_id: str) -> L1Record | None:
        """Get a specific record by source_id."""
        if source_id not in self._records:
            return None
        return self._records[source_id][1]

    def close(self) -> None:
        """Close the L1 adapter."""
        self._closed = True
        self._records.clear()

    def get_revision(self, source_id: str) -> int | None:
        """Get the current revision for a source_id."""
        if source_id not in self._records:
            return None
        return self._records[source_id][0]

    def health(self) -> dict[str, Any]:
        """Return health status of L1."""
        return {
            "status": "healthy" if not self._closed else "closed",
            "record_count": len(self._records),
        }