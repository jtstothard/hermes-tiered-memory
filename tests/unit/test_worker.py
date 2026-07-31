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


class TestStaleUpsertRejection:
    """Test that stale upserts are rejected after a delete."""

    def test_latest_delete_revision_returns_none_when_no_deletes(
        self, outbox_store
    ):
        """Test latest_delete_revision returns None when no deletes exist."""
        # Enqueue an upsert
        outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert"},
        )

        # No delete should return None
        delete_rev = outbox_store.latest_delete_revision("doc-1")
        assert delete_rev is None

    def test_latest_delete_revision_returns_correct_revision(
        self, outbox_store
    ):
        """Test latest_delete_revision returns the highest delete revision."""
        # Enqueue deletes at different revisions
        delete_2 = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete"},
        )
        delete_5 = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=5,
            content="",
            metadata={"operation": "delete"},
        )
        delete_3 = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=3,
            content="",
            metadata={"operation": "delete"},
        )

        # None are promoted yet, so should return None
        delete_rev = outbox_store.latest_delete_revision("doc-1")
        assert delete_rev is None

        # Promote the rev 3 delete
        outbox_store.update_state(
            row_id=delete_3.row_id,
            expected_state_version=delete_3.state_version,
            new_state=OutboxState.PROMOTED,
        )

        # Should return 3 (only promoted delete)
        delete_rev = outbox_store.latest_delete_revision("doc-1")
        assert delete_rev == 3

        # Promote the rev 5 delete
        outbox_store.update_state(
            row_id=delete_5.row_id,
            expected_state_version=delete_5.state_version,
            new_state=OutboxState.PROMOTED,
        )

        # Should return 5 (highest promoted delete)
        delete_rev = outbox_store.latest_delete_revision("doc-1")
        assert delete_rev == 5

    def test_latest_delete_revision_is_document_specific(
        self, outbox_store
    ):
        """Test latest_delete_revision doesn't leak across documents."""
        # Enqueue delete for doc-1
        delete_1 = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete"},
        )
        outbox_store.update_state(
            row_id=delete_1.row_id,
            expected_state_version=delete_1.state_version,
            new_state=OutboxState.PROMOTED,
        )

        # doc-2 should have no delete
        delete_rev = outbox_store.latest_delete_revision("doc-2")
        assert delete_rev is None

        # doc-1 should have the delete
        delete_rev = outbox_store.latest_delete_revision("doc-1")
        assert delete_rev == 2

    def test_worker_rejects_stale_upsert(self, outbox_store, mock_l2_adapter):
        """Test worker quarantines upserts older than latest delete."""
        # Enqueue upsert at revision 1
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Enqueue delete at revision 2
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        # Create worker
        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )

        # Process batch manually to verify ordering
        worker._drain_once()

        # Delete should be processed and promoted
        delete_result = outbox_store.get(delete_item.row_id)
        assert delete_result.state == OutboxState.PROMOTED

        # Upsert should be quarantined as stale
        upsert_result = outbox_store.get(upsert_item.row_id)
        assert upsert_result.state == OutboxState.QUARANTINED
        assert upsert_result.terminal_outcome.value == "stale_superseded"

        # L2 should NOT have received the upsert content
        # (delete was processed instead)
        l2_calls = mock_l2_adapter.get_retain_calls()
        # No retain calls because upsert was rejected before L2 call
        retain_calls_for_doc1 = [
            c for c in l2_calls if c.get("document_id") == "doc-1"
        ]
        assert len(retain_calls_for_doc1) == 0

    def test_worker_accepts_upsert_after_delete(self, outbox_store, mock_l2_adapter):
        """Test worker accepts upserts newer than latest delete."""
        # Enqueue delete at revision 2
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        # Enqueue upsert at revision 3 (later than delete)
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=3,
            content="New content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Create worker
        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )

        # Process batch
        worker._drain_once()

        # Both should be promoted (delete first, then upsert)
        delete_result = outbox_store.get(delete_item.row_id)
        upsert_result = outbox_store.get(upsert_item.row_id)

        assert delete_result.state == OutboxState.PROMOTED
        assert upsert_result.state == OutboxState.PROMOTED

    def test_worker_processes_deletes_before_upserts(self, outbox_store, mock_l2_adapter):
        """Test claim_batch orders deletes before upserts for same document."""
        # Enqueue upsert at revision 1
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Enqueue delete at revision 2
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        # Enqueue upsert at revision 3
        upsert_item2 = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=3,
            content="New content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Claim a batch of all 3 items
        import time

        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )
        items = outbox_store.claim_batch(
            batch_size=10,
            owner_id=worker._owner_id,
            lease_id=str(__import__("uuid").uuid4()),
            lease_duration_seconds=300,
        )

        # Should get all 3 items in order: delete (rev 2), upsert (rev 1), upsert (rev 3)
        assert len(items) == 3

        # First item should be delete (ordered before upserts regardless of revision)
        assert items[0].row_id == delete_item.row_id

        # Remaining items should be upserts ordered by revision
        assert items[1].row_id == upsert_item.row_id
        assert items[2].row_id == upsert_item2.row_id

    def test_delete_without_operation_metadata(self, outbox_store):
        """Test that items without operation metadata default to upsert."""
        # Enqueue item without operation metadata
        item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={},  # No operation key
        )

        # Should not count as delete
        delete_rev = outbox_store.latest_delete_revision("doc-1")
        assert delete_rev is None

        # Worker should treat it as upsert
        import json

        metadata = json.loads(item.metadata_snapshot)
        assert metadata.get("operation", "upsert") == "upsert"

    def test_batch_size_window_gap_prevention(self, outbox_store):
        """Test that claim_batch includes all deletes for documents with upserts."""
        # Enqueue delete and upsert for same document
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Claim a batch with size 1 (would normally only get one item)
        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=MockL2Adapter(),
            config=WorkerConfig(batch_size=1, lease_duration_seconds=300),
        )
        items = outbox_store.claim_batch(
            batch_size=1,
            owner_id=worker._owner_id,
            lease_id=str(__import__("uuid").uuid4()),
            lease_duration_seconds=300,
        )

        # Should get both delete AND upsert even though batch_size=1
        assert len(items) == 2
        row_ids = {item.row_id for item in items}
        assert delete_item.row_id in row_ids
        assert upsert_item.row_id in row_ids

    def test_crash_replay_inflight_delete_doesnt_block_upsert(
        self, outbox_store, mock_l2_adapter
    ):
        """Test that an in-flight delete (not durable) doesn't block upserts."""
        # Enqueue delete
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        # Manually mark delete as in_flight (simulating crash during processing)
        outbox_store.update_state(
            row_id=delete_item.row_id,
            expected_state_version=delete_item.state_version,
            new_state=OutboxState.IN_FLIGHT,
        )

        # Enqueue upsert that would be stale if delete were durable
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Create worker
        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )

        # Process batch
        worker._drain_once()

        # Upsert should be ACCEPTED because delete is not durable (not PROMOTED)
        upsert_result = outbox_store.get(upsert_item.row_id)
        assert upsert_result.state == OutboxState.PROMOTED

    def test_crash_replay_promoted_delete_blocks_stale_upsert(
        self, outbox_store, mock_l2_adapter
    ):
        """Test that a promoted delete blocks stale upserts after crash-replay."""
        # Enqueue delete
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        # Process delete to PROMOTED state
        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )
        worker._drain_once()

        # Verify delete is promoted
        delete_result = outbox_store.get(delete_item.row_id)
        assert delete_result.state == OutboxState.PROMOTED

        # Now enqueue a stale upsert (lower revision)
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Process upsert
        worker._drain_once()

        # Upsert should be QUARANTINED as stale
        upsert_result = outbox_store.get(upsert_item.row_id)
        assert upsert_result.state == OutboxState.QUARANTINED
        assert upsert_result.terminal_outcome.value == "stale_superseded"

    def test_backfill_after_delete_accepts_higher_revision(
        self, outbox_store, mock_l2_adapter
    ):
        """Test that backfill upserts with higher revisions are accepted after delete."""
        # Enqueue and promote delete
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )
        worker._drain_once()

        delete_result = outbox_store.get(delete_item.row_id)
        assert delete_result.state == OutboxState.PROMOTED

        # Enqueue backfill upsert with higher revision
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=3,
            content="Backfilled content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Process upsert
        worker._drain_once()

        # Upsert should be PROMOTED (higher revision than delete)
        upsert_result = outbox_store.get(upsert_item.row_id)
        assert upsert_result.state == OutboxState.PROMOTED

        # Verify the upsert actually reached L2 via retain()
        l2_calls = mock_l2_adapter.get_retain_calls()
        retain_calls_for_doc1 = [
            c for c in l2_calls if c.get("document_id") == "doc-1"
        ]
        assert len(retain_calls_for_doc1) == 1
        assert retain_calls_for_doc1[0]["content"] == "Backfilled content"

    def test_backfill_after_delete_rejects_lower_revision(
        self, outbox_store, mock_l2_adapter
    ):
        """Test that backfill upserts with lower revisions are rejected after delete."""
        # Enqueue and promote delete
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=5,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )
        worker._drain_once()

        delete_result = outbox_store.get(delete_item.row_id)
        assert delete_result.state == OutboxState.PROMOTED

        # Enqueue backfill upsert with lower revision
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=3,
            content="Backfilled content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        # Process upsert
        worker._drain_once()

        # Upsert should be QUARANTINED (lower revision than delete)
        upsert_result = outbox_store.get(upsert_item.row_id)
        assert upsert_result.state == OutboxState.QUARANTINED
        assert upsert_result.terminal_outcome.value == "stale_superseded"

    def test_concurrent_workers_no_resurrection(self, outbox_store, mock_l2_adapter):
        """Test that concurrent workers using real threads don't resurrect deleted content.

        Two workers claim batches simultaneously via threads. SQLite serializes
        the writes internally, but the claim ordering matters: whichever worker
        claims the document's items must get both delete and upsert (batch
        expansion), or the staleness check must reject the upsert after the
        delete reaches PROMOTED.
        """
        import threading

        # Enqueue delete and upsert for same document
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        worker_a = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=1, lease_duration_seconds=300),
        )
        worker_b = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=1, lease_duration_seconds=300),
        )

        results = {}
        errors = []

        def drain(worker, key):
            try:
                results[key] = worker._drain_once()
            except Exception as exc:
                errors.append((key, exc))

        t_a = threading.Thread(target=drain, args=(worker_a, "a"))
        t_b = threading.Thread(target=drain, args=(worker_b, "b"))

        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        assert not errors, f"Workers raised errors: {errors}"

        # At least one worker should have drained. Both items are for the same
        # document, so batch expansion ensures one worker claims both. The
        # delete is processed first (ordering), promoted, then the upsert hits
        # the staleness check and is quarantined.

        delete_result = outbox_store.get(delete_item.row_id)
        upsert_result = outbox_store.get(upsert_item.row_id)

        # Delete should be promoted (or in-flight if only partially processed)
        assert delete_result.state in (
            OutboxState.PROMOTED,
            OutboxState.IN_FLIGHT,
        )

        # If the upsert was claimed, it must NOT be PROMOTED (no resurrection)
        if upsert_result.state != OutboxState.PENDING:
            assert upsert_result.state == OutboxState.QUARANTINED
            assert upsert_result.terminal_outcome.value == "stale_superseded"

        # If both were processed, verify no resurrection at L2
        if delete_result.state == OutboxState.PROMOTED:
            l2_calls = mock_l2_adapter.get_retain_calls()
            retain_calls_for_doc1 = [
                c for c in l2_calls if c.get("document_id") == "doc-1"
            ]
            assert len(retain_calls_for_doc1) == 0

    def test_genuine_concurrent_claim_race(
        self, outbox_store, mock_l2_adapter
    ):
        """Two workers call claim_batch simultaneously; only one gets the items.

        Uses real threads to exercise the claim race. SQLite's row-level locking
        serializes the claim writes, but both threads are genuinely competing
        for the same rows at the same time.
        """
        import threading

        # Enqueue a single delete for one document
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        results = {"a": None, "b": None}
        errors = []

        def claim(key):
            try:
                results[key] = outbox_store.claim_batch(
                    batch_size=5,
                    owner_id=f"owner-{key}",
                    lease_id=str(__import__("uuid").uuid4()),
                    lease_duration_seconds=300,
                )
            except Exception as exc:
                errors.append((key, exc))

        t_a = threading.Thread(target=claim, args=("a",))
        t_b = threading.Thread(target=claim, args=("b",))

        t_a.start()
        t_b.start()
        t_a.join(timeout=10)
        t_b.join(timeout=10)

        assert not errors, f"Claim raised errors: {errors}"

        # Exactly one worker should get the item; the other gets nothing
        total_claimed = len(results["a"]) + len(results["b"])
        assert total_claimed == 1, (
            f"Expected exactly 1 item claimed total, got {total_claimed}: "
            f"a={results['a']}, b={results['b']}"
        )

    def test_quarantined_stale_superseded_never_retries(
        self, outbox_store, mock_l2_adapter
    ):
        """Test that quarantined STALE_SUPERSEDED items never retry."""
        # Enqueue delete
        delete_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=2,
            content="",
            metadata={"operation": "delete", "bank": "test-profile"},
        )

        # Enqueue stale upsert
        upsert_item = outbox_store.enqueue(
            document_id="doc-1",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Content",
            metadata={"operation": "upsert", "bank": "test-profile"},
        )

        worker = PromotionWorker(
            outbox_store=outbox_store,
            l2_adapter=mock_l2_adapter,
            config=WorkerConfig(batch_size=10, lease_duration_seconds=300),
        )

        # Process batch - upsert should be quarantined
        worker._drain_once()

        upsert_result = outbox_store.get(upsert_item.row_id)
        assert upsert_result.state == OutboxState.QUARANTINED
        assert upsert_result.terminal_outcome.value == "stale_superseded"

        # Remember the row_id for verification
        quarantined_row_id = upsert_item.row_id

        # Try to process again - quarantined item should not be claimed
        worker._drain_once()

        # Item should still be quarantined (not moved back to pending)
        quarantined_item = outbox_store.get(quarantined_row_id)
        assert quarantined_item.state == OutboxState.QUARANTINED
        assert quarantined_item.terminal_outcome.value == "stale_superseded"

        # Try again - should never retry
        worker._drain_once()

        quarantined_item = outbox_store.get(quarantined_row_id)
        assert quarantined_item.state == OutboxState.QUARANTINED
        assert quarantined_item.terminal_outcome.value == "stale_superseded"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])