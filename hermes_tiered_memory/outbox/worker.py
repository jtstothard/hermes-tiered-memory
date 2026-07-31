"""Promotion worker for draining the outbox."""

import logging
import threading
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_tiered_memory.adapters.l2 import L2Adapter
    from hermes_tiered_memory.outbox.store import OutboxStore

from hermes_tiered_memory.adapters.l2 import L2RetainResult
from hermes_tiered_memory.outbox.store import (
    OutboxItem,
    OutboxState,
    TerminalOutcome,
)


logger = logging.getLogger(__name__)


@dataclass
class WorkerConfig:
    """Configuration for the promotion worker."""

    batch_size: int = 10
    lease_duration_seconds: int = 300
    retry_delays_seconds: list[int] = None  # type: ignore[assignment]
    max_attempts_per_cycle: int = 5

    def __post_init__(self):
        if self.retry_delays_seconds is None:
            self.retry_delays_seconds = [10, 30, 90, 300]


class PromotionWorker:
    """Background worker that drains the outbox and promotes items to L2."""

    def __init__(
        self,
        outbox_store: "OutboxStore",
        l2_adapter: "L2Adapter",
        config: WorkerConfig | None = None,
    ):
        """Initialize the promotion worker."""
        self.outbox_store = outbox_store
        self.l2_adapter = l2_adapter
        self.config = config or WorkerConfig()

        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._owner_id = str(uuid.uuid4())

    def start(self) -> None:
        """Start the background worker thread."""
        if self._thread is not None:
            logger.warning("Worker already started")
            return

        self._stop_event.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="tiered-memory-promotion-worker",
            daemon=True,
        )
        self._thread.start()
        logger.info(f"Promotion worker started (owner_id={self._owner_id})")

    def stop(self) -> None:
        """Stop the background worker thread."""
        if self._thread is None:
            return

        self._stop_event.set()
        self._thread.join(timeout=5.0)
        self._thread = None
        logger.info("Promotion worker stopped")

    def _run_loop(self) -> None:
        """Main worker loop."""
        logger.info("Worker loop started")

        while not self._stop_event.is_set():
            try:
                self._drain_once()
            except Exception as e:
                logger.exception(f"Error in worker loop: {e}")

            # Sleep if no more work
            if self._stop_event.wait(1.0):
                break

        logger.info("Worker loop stopped")

    def _drain_once(self) -> None:
        """Drain one batch of pending items."""
        lease_id = str(uuid.uuid4())

        items = self.outbox_store.claim_batch(
            batch_size=self.config.batch_size,
            owner_id=self._owner_id,
            lease_id=lease_id,
            lease_duration_seconds=self.config.lease_duration_seconds,
        )

        if not items:
            return

        logger.info(f"Claimed {len(items)} items for processing")

        for item in items:
            if self._stop_event.is_set():
                # Return remaining items to pending
                self._return_to_pending(item)
                continue

            self._process_item(item)

    def _process_item(self, item: OutboxItem) -> None:
        """Process a single outbox item."""
        import json

        if item.row_id is None:
            logger.error(f"Cannot process item with no row_id: {item.document_id}")
            return

        metadata = json.loads(item.metadata_snapshot)

        # A committed tombstone supersedes every older promotion. Check before
        # touching L2 so a stale retry can never resurrect deleted content.
        operation = metadata.get("operation", "upsert")
        if operation != "delete":
            delete_revision = self.outbox_store.latest_delete_revision(item.document_id)
            if delete_revision is not None and item.mnemosyne_rev < delete_revision:
                self._mark_stale(item)
                return

        try:
            if operation == "delete":
                deleted = self.l2_adapter.delete_document(
                    metadata.get("bank", ""), item.document_id
                )
                result = L2RetainResult(
                    operation_id=None,
                    success=bool(deleted),
                    error=None if deleted else "L2 delete failed",
                )
            else:
                result = self.l2_adapter.retain(
                    document_id=item.document_id,
                    content=item.content_snapshot,
                    metadata=metadata,
                    async_op=True,
                )

            if result.success:
                # Promotion succeeded
                success = self.outbox_store.update_state(
                    row_id=item.row_id,
                    expected_state_version=item.state_version,
                    new_state=OutboxState.PROMOTED,
                    terminal_outcome=TerminalOutcome.NONE,
                )

                if success:
                    logger.info(
                        f"Promoted item {item.row_id} "
                        f"(document_id={item.document_id}, operation_id={result.operation_id})"
                    )
                else:
                    # CAS conflict, will be retried
                    logger.warning(
                        f"CAS conflict updating item {item.row_id} to PROMOTED"
                    )
            else:
                # Promotion failed
                self._handle_failure(item, result.error or "Unknown error")

        except Exception as e:
            logger.exception(f"Error processing item {item.row_id}: {e}")
            self._handle_failure(item, str(e))

    def _mark_stale(self, item: OutboxItem) -> None:
        """Record that an older upsert was safely skipped after deletion."""
        if item.row_id is None:
            return
        success = self.outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.QUARANTINED,
            terminal_outcome=TerminalOutcome.STALE_SUPERSEDED,
            error_class="stale_superseded",
        )
        if not success:
            logger.warning("CAS conflict marking stale item %s", item.row_id)

    def _handle_failure(self, item: OutboxItem, error: str) -> None:
        """Handle a promotion failure."""
        if item.row_id is None:
            logger.error(f"Cannot handle failure for item with no row_id: {item.document_id}")
            return
        error_class = self._classify_error(error)
        is_transient = error_class in ("transient", "rate_limit")

        if is_transient:
            # Transient failure - schedule retry
            if item.attempt_no < self.config.max_attempts_per_cycle:
                # Calculate next retry time
                attempt_index = min(item.attempt_no, len(self.config.retry_delays_seconds) - 1)
                delay_seconds = self.config.retry_delays_seconds[attempt_index]
                next_retry_at = int(time.time()) + delay_seconds

                success = self.outbox_store.update_state(
                    row_id=item.row_id,
                    expected_state_version=item.state_version,
                    new_state=OutboxState.PENDING,
                    error_class=error_class,
                    next_retry_at=next_retry_at,
                    increment_attempt=True,
                )

                if success:
                    logger.info(
                        f"Retry scheduled for item {item.row_id} "
                        f"(attempt {item.attempt_no + 1}, delay={delay_seconds}s)"
                    )
                else:
                    logger.warning(f"CAS conflict scheduling retry for item {item.row_id}")
            else:
                # Exhausted retries - quarantine
                self._quarantine_item(
                    item,
                    TerminalOutcome.AUTOMATIC_RETRY_QUARANTINE,
                    error_class,
                )
        else:
            # Permanent failure - immediate quarantine
            self._quarantine_item(
                item,
                TerminalOutcome.CONFIRMED_PERMANENT,
                error_class,
            )

    def _classify_error(self, error: str) -> str:
        """Classify an error as transient or permanent."""
        error_lower = error.lower()

        # Transient errors
        if any(
            keyword in error_lower
            for keyword in [
                "timeout",
                "connection refused",
                "dns",
                "5",
                "rate limit",
                "429",
            ]
        ):
            if "429" in error_lower or "rate limit" in error_lower:
                return "rate_limit"
            return "transient"

        # Permanent errors
        if any(
            keyword in error_lower
            for keyword in [
                "400",
                "401",
                "403",
                "404",
                "413",
                "422",
                "malformed",
            ]
        ):
            return "permanent"

        # Default to permanent
        return "permanent"

    def _quarantine_item(
        self,
        item: OutboxItem,
        terminal_outcome: TerminalOutcome,
        error_class: str,
    ) -> None:
        """Quarantine an item."""
        if item.row_id is None:
            logger.error(f"Cannot quarantine item with no row_id: {item.document_id}")
            return

        success = self.outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.QUARANTINED,
            terminal_outcome=terminal_outcome,
            error_class=error_class,
        )

        if success:
            logger.warning(
                f"Quarantined item {item.row_id} "
                f"(document_id={item.document_id}, outcome={terminal_outcome.value}, "
                f"error_class={error_class})"
            )
        else:
            logger.warning(f"CAS conflict quarantining item {item.row_id}")

    def _return_to_pending(self, item: OutboxItem) -> None:
        """Return an item to pending state when shutting down."""
        if item.row_id is None:
            logger.error(f"Cannot return item with no row_id to pending: {item.document_id}")
            return

        success = self.outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.PENDING,
        )

        if not success:
            logger.warning(f"CAS conflict returning item {item.row_id} to pending")