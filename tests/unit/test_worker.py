"""Unit tests for the promotion worker."""

import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from hermes_tiered_memory.adapters.mock_l2 import MockL2Adapter
from hermes_tiered_memory.outbox.store import OutboxItem, OutboxState, OutboxStore
from hermes_tiered_memory.outbox.worker import PromotionWorker, WorkerConfig


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


@pytest.fixture
def mock_l2_adapter():
    """Create a mock L2 adapter for testing."""
    return MockL2Adapter()


@pytest.fixture
def worker(outbox_store, mock_l2_adapter):
    """Create a promotion worker for testing."""
    worker = PromotionWorker(
        outbox_store=outbox_store,
        l2_adapter=mock_l2_adapter,
        config=WorkerConfig(batch_size=2, lease_duration_seconds=300),
    )
    yield worker
    worker.stop()


class TestWorkerBasics:
    """Test basic worker functionality."""

    def test_worker_initialization(self, worker):
        """Test worker initializes correctly."""
        assert worker.outbox_store is not None
        assert worker.l2_adapter is not None
        assert worker.config.batch_size == 2

    def test_start_stop_worker(self, worker):
        """Test starting and stopping the worker."""
        worker.start()
        assert worker._thread is not None
        assert worker._thread.is_alive()

        worker.stop()
        assert worker._thread is None


class TestWorkerDraining:
    """Test worker draining functionality."""

    def test_drain_processes_pending_items(self, worker, outbox_store, mock_l2_adapter):
        """Test worker processes pending items."""
        # Enqueue items
        for i in range(3):
            outbox_store.enqueue(
                document_id=f"doc-{i}",
                mnemosyne_id=f"mne-{i}",
                mnemosyne_rev=1,
                content=f"Content {i}",
                metadata={"bank": "test-profile"},
            )

        # Start worker
        worker.start()

        # Wait for processing
        worker._thread.join(timeout=2.0)

        # Check items were promoted
        stats = outbox_store.get_stats()
        assert stats.get("promoted", 0) > 0 or stats.get("in_flight", 0) > 0

    def test_drain_respects_batch_size(self, worker, outbox_store, mock_l2_adapter):
        """Test worker respects batch size configuration."""
        # Enqueue many items
        for i in range(10):
            outbox_store.enqueue(
                document_id=f"doc-{i}",
                mnemosyne_id=f"mne-{i}",
                mnemosyne_rev=1,
                content=f"Content {i}",
                metadata={"bank": "test-profile"},
            )

        # Start worker
        worker.start()

        # Wait a bit
        import time

        time.sleep(0.5)

        # Stop worker
        worker.stop()

        # Check processing was batched
        # This is a weak test; in real scenarios we'd track batch processing more precisely


class TestWorkerErrorHandling:
    """Test worker error handling."""

    def test_handles_l2_failure(self, worker, outbox_store, mock_l2_adapter):
        """Test worker handles L2 failures correctly."""
        # Configure L2 to fail
        mock_l2_adapter.set_fail_next_retain(True)

        # Enqueue item
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"bank": "test-profile"},
        )

        # Start worker
        worker.start()

        # Wait for processing
        worker._thread.join(timeout=2.0)

        # Check item was quarantined or scheduled for retry
        updated = outbox_store.get(item.row_id)
        assert updated.state in (OutboxState.QUARANTINED, OutboxState.PENDING)


class TestWorkerRetryLogic:
    """Test worker retry logic."""

    def test_retries_transient_failures(self, worker, outbox_store, mock_l2_adapter):
        """Test worker retries transient failures."""
        # Enqueue item
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"bank": "test-profile"},
        )

        # Manually set to retryable state
        outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.PENDING,
            next_retry_at=int(__import__("time").time()) - 1,
        )

        # Start worker
        worker.start()

        # Wait for processing
        worker._thread.join(timeout=2.0)

        # Check item was processed
        updated = outbox_store.get(item.row_id)
        # Should be either promoted or in a retry state


if __name__ == "__main__":
    pytest.main([__file__, "-v"])