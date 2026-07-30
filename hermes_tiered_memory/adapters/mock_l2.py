"""Mock L2 adapter for testing."""

from datetime import datetime
from typing import Any

from hermes_tiered_memory.adapters.l2 import L2Adapter, L2Document, L2RetainResult


class MockL2Adapter(L2Adapter):
    """Mock L2 adapter for testing."""

    def __init__(self):
        """Initialize the mock L2 adapter."""
        self._documents: dict[str, L2Document] = {}
        self._operations: dict[str, dict[str, Any]] = {}
        self._closed = False
        self._fail_next_retain = False
        self._fail_next_delete = False

    def retain(
        self,
        document_id: str,
        content: str,
        metadata: Any,
        async_op: bool = True,
    ) -> L2RetainResult:
        """Retain a document in L2."""
        if self._fail_next_retain:
            self._fail_next_retain = False
            return L2RetainResult(
                operation_id=None,
                success=False,
                error="Simulated retain failure",
            )

        now = datetime.utcnow()

        # Check if document exists
        if document_id in self._documents:
            # Replace
            doc = L2Document(
                document_id=document_id,
                content=content,
                metadata=metadata,
                created_at=self._documents[document_id].created_at,
                updated_at=now,
            )
        else:
            # Create new
            doc = L2Document(
                document_id=document_id,
                content=content,
                metadata=metadata,
                created_at=now,
                updated_at=now,
            )

        self._documents[document_id] = doc

        operation_id = None
        if async_op:
            operation_id = f"op-{len(self._operations)}"
            self._operations[operation_id] = {
                "status": "completed",
                "document_id": document_id,
                "created_at": now,
            }

        return L2RetainResult(operation_id=operation_id, success=True)

    def retain_batch(
        self,
        items: list[tuple[str, str, dict[str, Any]]],
        async_op: bool = True,
    ) -> list[L2RetainResult]:
        """Retain multiple documents in L2."""
        return [
            self.retain(document_id, content, metadata, async_op)
            for document_id, content, metadata in items
        ]

    def recall(self, query: str, bank_id: str, limit: int = 10) -> list[L2Document]:
        """Recall documents from L2."""
        # Simple substring match for testing
        results = []
        query_lower = query.lower()

        for doc in self._documents.values():
            if len(results) >= limit:
                break
            # Filter by bank_id from metadata
            if doc.metadata.get("bank") != bank_id:
                continue
            if query_lower in doc.content.lower():
                results.append(doc)

        return results

    def delete_document(self, bank_id: str, document_id: str) -> bool:
        """Delete a document from L2."""
        if self._fail_next_delete:
            self._fail_next_delete = False
            return False

        if document_id not in self._documents:
            return False

        # Verify bank_id matches
        if self._documents[document_id].metadata.get("bank") != bank_id:
            return False

        del self._documents[document_id]
        return True

    def get_operation_status(self, operation_id: str) -> dict[str, Any]:
        """Get the status of an async operation."""
        if operation_id not in self._operations:
            return {"status": "not_found"}

        return self._operations[operation_id]

    def close(self) -> None:
        """Close the L2 adapter."""
        self._closed = True
        self._documents.clear()
        self._operations.clear()

    def health(self) -> dict[str, Any]:
        """Return health status of L2."""
        return {
            "status": "healthy" if not self._closed else "closed",
            "document_count": len(self._documents),
        }

    def set_fail_next_retain(self, fail: bool) -> None:
        """Set whether the next retain should fail."""
        self._fail_next_retain = fail

    def set_fail_next_delete(self, fail: bool) -> None:
        """Set whether the next delete should fail."""
        self._fail_next_delete = fail