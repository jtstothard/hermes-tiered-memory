"""L2 adapter interface for Hindsight."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class L2Document:
    """A document in L2 (Hindsight)."""

    document_id: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True)
class L2RetainResult:
    """Result of a retain operation."""

    operation_id: str | None
    success: bool
    error: str | None = None


class L2Adapter(ABC):
    """Abstract L2 adapter interface."""

    @abstractmethod
    def retain(
        self,
        document_id: str,
        content: str,
        metadata: dict[str, Any],
        async_op: bool = True,
    ) -> L2RetainResult:
        """Retain a document in L2. If async_op=True, returns operation_id for async processing."""

    @abstractmethod
    def retain_batch(
        self,
        items: list[tuple[str, str, dict[str, Any]]],
        async_op: bool = True,
    ) -> list[L2RetainResult]:
        """Retain multiple documents in L2. Each item is (document_id, content, metadata)."""

    @abstractmethod
    def recall(self, query: str, bank_id: str, limit: int = 10) -> list[L2Document]:
        """Recall documents from L2."""

    @abstractmethod
    def delete_document(self, bank_id: str, document_id: str) -> bool:
        """Delete a document from L2. Returns True if deleted."""

    @abstractmethod
    def get_operation_status(self, operation_id: str) -> dict[str, Any]:
        """Get the status of an async operation."""

    @abstractmethod
    def close(self) -> None:
        """Close the L2 adapter and release resources."""

    @abstractmethod
    def health(self) -> dict[str, Any]:
        """Return health status of L2."""
