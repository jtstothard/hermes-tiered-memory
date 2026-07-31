"""Unit tests for the outbox store."""

import tempfile
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_tiered_memory.outbox.store import (
    OutboxItem,
    OutboxState,
    OutboxStore,
    TerminalOutcome,
)


@pytest.fixture
def temp_db_path():
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "outbox.db"
        yield db_path


@pytest.fixture
def outbox_store(temp_db_path):
    """Create an outbox store for testing."""
    store = OutboxStore(temp_db_path)
    yield store
    store.close()


class TestOutboxStoreBasics:
    """Test basic outbox store functionality."""

    def test_initialization_creates_schema(self, temp_db_path):
        """Test initialization creates the schema."""
        store = OutboxStore(temp_db_path)

        # Check tables exist
        conn = store._get_connection()
        cursor = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='outbox'"
        )
        result = cursor.fetchone()
        assert result is not None

        store.close()

    def test_enqueue_creates_item(self, outbox_store):
        """Test enqueuing creates an item."""
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Test content",
            metadata={"key": "value"},
        )

        assert item.document_id == "doc-1"
        assert item.mnemosyne_id == "mne-1"
        assert item.mnemosyne_rev == 1
        assert item.content_snapshot == "Test content"
        assert item.state == OutboxState.PENDING
        assert item.attempt_no == 0
        assert item.retry_cycle_no == 0
        assert item.row_id is not None

    def test_get_retrieves_item(self, outbox_store):
        """Test getting an item by row_id."""
        enqueued = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Test content",
            metadata={},
        )

        retrieved = outbox_store.get(enqueued.row_id)

        assert retrieved is not None
        assert retrieved.row_id == enqueued.row_id
        assert retrieved.document_id == enqueued.document_id

    def test_get_by_identity(self, outbox_store):
        """Test getting an item by logical identity."""
        outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content 1",
            metadata={},
        )

        outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="Content 2",
            metadata={},
        )

        # Get by (document_id, revision)
        item = outbox_store.get_by_identity("doc-1", 1)

        assert item is not None
        assert item.content_snapshot == "Content 1"
        assert item.mnemosyne_rev == 1

    def test_unique_constraint_enforced(self, outbox_store):
        """Test unique constraint on (document_id, mnemosyne_rev)."""
        outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content 1",
            metadata={},
        )

        # Attempt to enqueue same logical item
        with pytest.raises(Exception):  # SQLite IntegrityError
            outbox_store.enqueue(
                document_id="doc-1",
                mnemosyne_id="mne-1",
                mnemosyne_rev=1,
                content="Content 2",
                metadata={},
            )


class TestOutboxClaiming:
    """Test outbox item claiming."""

    def test_claim_batch(self, outbox_store):
        """Test claiming a batch of items."""
        # Enqueue multiple items
        for i in range(5):
            outbox_store.enqueue(
                document_id=f"doc-{i}",
                mnemosyne_id=f"mne-{i}",
                mnemosyne_rev=1,
                content=f"Content {i}",
                metadata={},
            )

        # Claim a batch
        items = outbox_store.claim_batch(
            batch_size=3,
            owner_id="owner-1",
            lease_id="lease-1",
            lease_duration_seconds=60,
        )

        assert len(items) <= 3

        # Check items are claimed
        for item in items:
            assert item.state == OutboxState.IN_FLIGHT
            assert item.owner_id == "owner-1"
            assert item.lease_id == "lease-1"

    def test_claim_respects_next_retry_at(self, outbox_store):
        """Test claiming respects next_retry_at scheduling."""
        # Enqueue item with future retry time
        now = int(time.time())
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={},
        )

        # Update to have future retry time
        outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.PENDING,
            next_retry_at=now + 3600,
        )

        # Should not be claimed
        items = outbox_store.claim_batch(
            batch_size=10,
            owner_id="owner-1",
            lease_id="lease-1",
            lease_duration_seconds=60,
        )

        assert len(items) == 0


class TestOutboxStateUpdates:
    """Test outbox state updates."""

    def test_update_state_promoted(self, outbox_store):
        """Test updating state to promoted."""
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={},
        )

        success = outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.PROMOTED,
        )

        assert success is True

        # Verify state changed
        updated = outbox_store.get(item.row_id)
        assert updated.state == OutboxState.PROMOTED
        assert updated.state_version == item.state_version + 1

    def test_update_state_cas_protection(self, outbox_store):
        """Test CAS protection on state updates."""
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={},
        )

        # First update succeeds
        success1 = outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.IN_FLIGHT,
        )
        assert success1 is True

        # Second update with stale version fails
        success2 = outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,  # Stale
            new_state=OutboxState.PROMOTED,
        )
        assert success2 is False

    def test_update_state_quarantine(self, outbox_store):
        """Test quarantining an item."""
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={},
        )

        success = outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.QUARANTINED,
            terminal_outcome=TerminalOutcome.CONFIRMED_PERMANENT,
            error_class="permanent",
        )

        assert success is True

        updated = outbox_store.get(item.row_id)
        assert updated.state == OutboxState.QUARANTINED
        assert updated.terminal_outcome == TerminalOutcome.CONFIRMED_PERMANENT
        assert updated.last_error_class == "permanent"
        assert updated.quarantine_origin_at > 0


class TestOutboxStats:
    """Test outbox statistics."""

    def test_get_stats(self, outbox_store):
        """Test getting statistics."""
        # Enqueue items in different states
        for i in range(3):
            outbox_store.enqueue(
                document_id=f"doc-{i}",
                mnemosyne_id=f"mne-{i}",
                mnemosyne_rev=1,
                content=f"Content {i}",
                metadata={},
            )

        # Claim one
        item = outbox_store.enqueue(
            document_id="doc-3",
            mnemosyne_id="mne-3",
            mnemosyne_rev=1,
            content="Content 3",
            metadata={},
        )
        outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.IN_FLIGHT,
        )

        stats = outbox_store.get_stats()

        assert stats["pending"] >= 3
        assert stats["in_flight"] >= 1


class TestOutboxPersistence:
    """Test outbox persistence."""

    def test_persists_across_restart(self, temp_db_path):
        """Test outbox persists across restart."""
        # Create first store instance
        store1 = OutboxStore(temp_db_path)
        store1.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={},
        )
        stats1 = store1.get_stats()
        store1.close()

        # Create second store instance
        store2 = OutboxStore(temp_db_path)
        stats2 = store2.get_stats()
        store2.close()

        assert stats2["pending"] == stats1["pending"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])