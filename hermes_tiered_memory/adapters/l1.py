"""L1 adapter interface for Mnemosyne."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class L1Record:
    """A record from L1 (Mnemosyne)."""

    source_id: str
    revision: int
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime
    is_deleted: bool = False


class L1Adapter(ABC):
    """Abstract L1 adapter interface."""

    @abstractmethod
    def remember(self, content: str, metadata: dict[str, Any]) -> L1Record:
        """Store a record in L1 and return the created record with its source_id and revision."""

    @abstractmethod
    def remember_batch(self, items: list[tuple[str, dict[str, Any]]]) -> list[L1Record]:
        """Store multiple records in L1 and return the created records."""

    @abstractmethod
    def recall(self, query: str, limit: int = 10) -> list[L1Record]:
        """Query L1 for relevant records."""

    @abstractmethod
    def forget(self, source_id: str) -> bool:
        """Delete a record from L1. Returns True if deleted, False if not found."""

    @abstractmethod
    def get(self, source_id: str) -> L1Record | None:
        """Get a specific record by source_id."""

    @abstractmethod
    def close(self) -> None:
        """Close the L1 adapter and release resources."""

    @abstractmethod
    def get_revision(self, source_id: str) -> int | None:
        """Get the current revision for a source_id."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return health status of L1."""
