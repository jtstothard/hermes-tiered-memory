"""Main composite Hermes memory provider."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from hermes_tiered_memory.config import ProtectedConfig, ProtectedConfigLoader

logger = logging.getLogger(__name__)


class CompositeProvider:
    """
    Composite Hermes memory provider combining Mnemosyne L1 and Hindsight L2.

    Implements the Hermes MemoryProvider interface with:
    - One Hermes-facing tool namespace (memory_search, memory_store)
    - Mnemosyne L1 synchronous writes and cache-first reads
    - Hindsight L2 retain/retain_batch_async promotion (no reflect dependency)
    - Durable local outbox with crash-safe recovery
    - L2 miss fallback and backfill
    - Profile/bank isolation
    - Metadata provenance envelope
    """

    def __init__(
        self,
        session,
        platform,
        hermes_home: str,
        context,
        user_identity: str,
        chat_identity: str,
        thread_identity: str,
        active_profile_identity: str,
    ):
        """Initialize the composite provider."""
        self.session = session
        self.platform = platform
        self.hermes_home = Path(hermes_home)
        self.context = context
        self.user_identity = user_identity
        self.chat_identity = chat_identity
        self.thread_identity = thread_identity
        self.active_profile_identity = active_profile_identity

        # Adapters (imported here to avoid circular imports)
        from hermes_tiered_memory.adapters.l1 import L1Adapter
        from hermes_tiered_memory.adapters.l2 import L2Adapter
        from hermes_tiered_memory.outbox.store import OutboxStore
        from hermes_tiered_memory.outbox.worker import PromotionWorker

        self.l1_adapter: L1Adapter | None = None
        self.l2_adapter: L2Adapter | None = None

        # Outbox and worker
        self.outbox_store: OutboxStore | None = None
        self.promotion_worker: PromotionWorker | None = None

        # Protected configuration
        self.protected_config: ProtectedConfig | None = None

        logger.info(
            f"CompositeProvider initialized for profile={active_profile_identity}, "
            f"user={user_identity}, chat={chat_identity}, thread={thread_identity}"
        )

    def initialize_all(self) -> None:
        """Initialize all adapters and the promotion worker."""
        logger.info("Initializing composite provider components")

        # Load protected configuration
        self._load_protected_config()

        # Initialize L1 adapter (Mnemosyne)
        self._initialize_l1()

        # Initialize L2 adapter (Hindsight)
        self._initialize_l2()

        # Initialize outbox
        self._initialize_outbox()

        # Initialize and start promotion worker
        self._initialize_worker()

        logger.info("Composite provider fully initialized")

    def _load_protected_config(self) -> None:
        """Load and validate protected Hindsight endpoint configuration."""
        config = self.session.config if self.session else {}
        tiered_config = config.get("tiered", {})

        protected_config_path = tiered_config.get("protected_config", {}).get("path")
        signature_path = tiered_config.get("protected_config", {}).get("signature", {}).get("path")

        if not protected_config_path:
            raise RuntimeError(
                "Protected Hindsight configuration path is required; "
                "composite provider startup aborted"
            )

        try:
            loader = ProtectedConfigLoader(
                Path(protected_config_path),
                Path(signature_path) if signature_path else None,
            )
            self.protected_config = loader.load()
            logger.info(
                f"Loaded protected config: {self.protected_config.config_id} "
                f"(sequence={self.protected_config.sequence})"
            )
        except Exception as e:
            logger.error(f"Failed to load protected configuration: {e}")
            raise RuntimeError(
                "Protected Hindsight configuration is invalid; "
                "composite provider startup aborted"
            ) from e

    def _initialize_l1(self) -> None:
        """Initialize the L1 (Mnemosyne) adapter."""
        from hermes_tiered_memory.adapters.l1 import L1Adapter

        # For now, we'll use a mock L1 adapter
        # In production, this would initialize Mnemosyne
        logger.info("L1 adapter initialization (mock for now)")

    def _initialize_l2(self) -> None:
        """Initialize the L2 (Hindsight) adapter."""
        from hermes_tiered_memory.adapters.l2 import L2Adapter

        # For now, we'll use a mock L2 adapter
        # In production, this would initialize Hindsight client
        if self.protected_config:
            logger.info(
                f"L2 adapter would connect to {self.protected_config.hindsight.to_url()}"
            )
        else:
            logger.warning("No protected config available for L2 adapter")

    def _initialize_outbox(self) -> None:
        """Initialize the durable outbox store."""
        from hermes_tiered_memory.outbox.store import OutboxStore

        # Outbox lives in the profile's data directory
        profile_data_dir = self.hermes_home / "profiles" / self.active_profile_identity / "tiered-memory"
        outbox_db_path = profile_data_dir / "outbox.db"

        self.outbox_store = OutboxStore(outbox_db_path)
        logger.info(f"Outbox initialized at {outbox_db_path}")

    def _initialize_worker(self) -> None:
        """Initialize and start the promotion worker."""
        from hermes_tiered_memory.outbox.worker import PromotionWorker, WorkerConfig

        if not self.outbox_store or not self.l2_adapter:
            logger.warning("Cannot start worker: outbox or L2 adapter not initialized")
            return

        config = WorkerConfig()
        self.promotion_worker = PromotionWorker(
            outbox_store=self.outbox_store,
            l2_adapter=self.l2_adapter,
            config=config,
        )
        self.promotion_worker.start()
        logger.info("Promotion worker started")

    def memory_search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Search for memories matching the query.

        Implements cache-first policy:
        1. Query L1 first
        2. On miss, query L2 and backfill L1
        """
        if not self.l1_adapter:
            logger.warning("L1 adapter not initialized")
            return []

        # Query L1 first (cache-first)
        try:
            l1_results = self.l1_adapter.recall(query, limit=limit)

            if l1_results:
                logger.info(f"L1 hit: {len(l1_results)} results")
                return [self._l1_record_to_dict(r) for r in l1_results]
        except Exception as e:
            logger.exception(f"L1 recall error: {e}")

        # L1 miss - query L2
        if self.l2_adapter:
            try:
                logger.info("L1 miss, querying L2")
                bank_id = self._get_bank_id()
                l2_results = self.l2_adapter.recall(query, bank_id, limit=limit)

                if l2_results:
                    # Backfill L1
                    self._backfill_l1(l2_results)
                    return [self._l2_document_to_dict(d) for d in l2_results]
            except Exception as e:
                logger.exception(f"L2 recall error: {e}")

        return []

    def memory_store(self, content: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """
        Store a memory.

        Implements write-through policy:
        1. Synchronously write to L1
        2. Enqueue asynchronous L2 promotion
        """
        if metadata is None:
            metadata = {}

        if not self.l1_adapter:
            logger.warning("L1 adapter not initialized")
            raise RuntimeError("L1 adapter not initialized")

        # Concrete SQLite L1 uses the shared ATTACH transaction. Other adapters
        # retain their isolated injection behavior and are not advertised atomic.
        from hermes_tiered_memory.adapters.sqlite_l1 import SQLiteL1Adapter
        if isinstance(self.l1_adapter, SQLiteL1Adapter) and self.outbox_store:
            if not self.l1_adapter.supports_atomic_outbox:
                self.l1_adapter.configure_atomic_outbox(self.outbox_store)
            try:
                l1_record = self.l1_adapter.remember_with_outbox(
                    content, metadata, document_id_factory=self._generate_document_id,
                    promotion_metadata_factory=self._build_promotion_metadata,
                    outbox_store=self.outbox_store
                )
            except Exception as e:
                logger.exception(f"Atomic L1/outbox write error: {e}")
                raise RuntimeError(f"L1/outbox write failed: {e}") from e
            return self._l1_record_to_dict(l1_record)

        # Isolated adapters use the historical two-step behavior.
        try:
            l1_record = self.l1_adapter.remember(content, metadata)
            logger.info(
                f"L1 write succeeded: source_id={l1_record.source_id}, "
                f"revision={l1_record.revision}"
            )
        except Exception as e:
            logger.exception(f"L1 write error: {e}")
            raise RuntimeError(f"L1 write failed: {e}")

        # Enqueue L2 promotion (asynchronous)
        if self.outbox_store:
            try:
                document_id = self._generate_document_id(l1_record.source_id)
                promotion_metadata = self._build_promotion_metadata(l1_record)

                self.outbox_store.enqueue(
                    document_id=document_id,
                    mnemosyne_id=l1_record.source_id,
                    mnemosyne_rev=l1_record.revision,
                    content=l1_record.content,
                    metadata=promotion_metadata,
                )
                logger.info(
                    f"L2 promotion enqueued: document_id={document_id}, "
                    f"mnemosyne_id={l1_record.source_id}, revision={l1_record.revision}"
                )
            except Exception as e:
                logger.exception(f"Outbox enqueue error: {e}")
                # Don't fail the request if enqueue fails - L1 is durable

        return self._l1_record_to_dict(l1_record)

    def memory_delete(self, memory_id: str) -> bool:
        """
        Delete a memory.

        Implements tombstone semantics:
        1. Delete/tombstone from L1 synchronously
        2. Enqueue L2 deletion asynchronously (atomic for SQLiteL1Adapter with configured outbox)
        """
        if not self.l1_adapter:
            logger.warning("L1 adapter not initialized")
            return False

        # Concrete SQLite L1 uses the shared ATTACH transaction. Other adapters
        # retain their isolated injection behavior and are not advertised atomic.
        from hermes_tiered_memory.adapters.sqlite_l1 import SQLiteL1Adapter
        if isinstance(self.l1_adapter, SQLiteL1Adapter) and self.outbox_store:
            if not self.l1_adapter.supports_atomic_outbox:
                self.l1_adapter.configure_atomic_outbox(self.outbox_store)
            try:
                new_revision = self.l1_adapter.forget_with_outbox(
                    memory_id, document_id_factory=self._generate_document_id,
                    delete_metadata_factory=self._build_delete_metadata,
                    outbox_store=self.outbox_store
                )
                logger.info(
                    f"Atomic L1 delete: source_id={memory_id}, new_revision={new_revision}"
                )
                return new_revision is not None
            except Exception as e:
                logger.exception(f"Atomic L1/outbox delete error: {e}")
                raise RuntimeError(f"L1/outbox delete failed: {e}") from e

        # Isolated adapters use the historical two-step behavior.
        try:
            deleted = self.l1_adapter.forget(memory_id)
            logger.info(f"L1 delete: source_id={memory_id}, success={deleted}")
        except Exception as e:
            logger.exception(f"L1 delete error: {e}")
            return False

        # Enqueue L2 deletion (asynchronous)
        if deleted and self.outbox_store:
            try:
                document_id = self._generate_document_id(memory_id)
                delete_metadata = self._build_delete_metadata(memory_id, self.l1_adapter.get_revision(memory_id) or 0)

                self.outbox_store.enqueue(
                    document_id=document_id,
                    mnemosyne_id=memory_id,
                    mnemosyne_rev=self.l1_adapter.get_revision(memory_id) or 0,
                    content="",
                    metadata=delete_metadata,
                )
                logger.info(
                    f"L2 delete enqueued: document_id={document_id}, "
                    f"mnemosyne_id={memory_id}"
                )
            except Exception as e:
                logger.exception(f"L2 delete enqueue error: {e}")
                # Don't fail the request if enqueue fails - L1 is durable

        return deleted

    def _build_delete_metadata(self, source_id: str, revision: int) -> dict[str, Any]:
        """Build the metadata envelope for L2 deletion."""
        return {
            "operation": "delete",
            "origin": "mnemosyne",
            "profile": self.active_profile_identity,
            "bank": self._get_bank_id(),
            "mnemosyne_id": source_id,
            "mnemosyne_revision": revision,
        }

    def close(self) -> None:
        """Close the provider and release resources."""
        logger.info("Closing composite provider")

        if self.promotion_worker:
            self.promotion_worker.stop()
            logger.info("Promotion worker stopped")

        if self.l1_adapter:
            self.l1_adapter.close()
            logger.info("L1 adapter closed")

        if self.l2_adapter:
            self.l2_adapter.close()
            logger.info("L2 adapter closed")

        if self.outbox_store:
            self.outbox_store.close()
            logger.info("Outbox store closed")

        logger.info("Composite provider closed")

    def health(self) -> dict[str, Any]:
        """Return health status of all components."""
        health = {
            "provider": "tiered-memory-composite",
            "profile": self.active_profile_identity,
            "l1": self.l1_adapter.health() if self.l1_adapter else {"status": "not_initialized"},
            "l2": self.l2_adapter.health() if self.l2_adapter else {"status": "not_initialized"},
        }

        if self.outbox_store:
            health["outbox"] = self.outbox_store.get_stats()

        if self.protected_config:
            health["protected_config"] = {
                "config_id": self.protected_config.config_id,
                "sequence": self.protected_config.sequence,
                "hindsight_url": self.protected_config.hindsight.to_url(),
            }

        return health

    def _get_bank_id(self) -> str:
        """Get the Hindsight bank ID for the current profile."""
        # Use the profile identity as the bank ID for isolation
        return self.active_profile_identity

    def _generate_document_id(self, mnemosyne_id: str) -> str:
        """Generate a stable document ID from a Mnemosyne ID."""
        # Use SHA-256 for stability
        digest = hashlib.sha256(
            f"{self.active_profile_identity}:{self._get_bank_id()}:{mnemosyne_id}".encode()
        ).hexdigest()
        return f"mnemosyne-{digest[:32]}"

    def _build_promotion_metadata(self, l1_record) -> dict[str, Any]:
        """Build the metadata envelope for L2 promotion."""
        return {
            "origin": "mnemosyne",
            "profile": self.active_profile_identity,
            "bank": self._get_bank_id(),
            "mnemosyne_id": l1_record.source_id,
            "mnemosyne_revision": l1_record.revision,
            "created_at": l1_record.created_at.isoformat() if l1_record.created_at else None,
            "updated_at": l1_record.updated_at.isoformat() if l1_record.updated_at else None,
            "tags": [f"profile:{self.active_profile_identity}", f"bank:{self._get_bank_id()}"],
            **l1_record.metadata,
        }

    def _l1_record_to_dict(self, record) -> dict[str, Any]:
        """Convert an L1Record to a dictionary."""
        return {
            "id": record.source_id,
            "content": record.content,
            "metadata": record.metadata,
            "revision": record.revision,
            "created_at": record.created_at.isoformat() if record.created_at else None,
            "updated_at": record.updated_at.isoformat() if record.updated_at else None,
            "is_deleted": record.is_deleted,
            "source": "l1",
        }

    def _l2_document_to_dict(self, document) -> dict[str, Any]:
        """Convert an L2Document to a dictionary."""
        return {
            "id": document.document_id,
            "content": document.content,
            "metadata": document.metadata,
            "created_at": document.created_at.isoformat() if document.created_at else None,
            "updated_at": document.updated_at.isoformat() if document.updated_at else None,
            "source": "l2",
        }

    def _backfill_l1(self, l2_results) -> None:
        """Backfill L1 with L2 results."""
        if not self.l1_adapter:
            return

        for doc in l2_results:
            try:
                # Extract provenance metadata
                metadata = doc.metadata.copy()
                provenance = {
                    "origin": "l2_backfill",
                    "document_id": doc.document_id,
                }
                metadata.update(provenance)

                # Store in L1
                self.l1_adapter.remember(doc.content, metadata)
                logger.debug(f"Backfilled L1 with document {doc.document_id}")
            except Exception as e:
                logger.exception(f"Error backfilling L1 with document {doc.document_id}: {e}")

    def inject_memory_provider_tools(self, agent):
        """Inject memory tools into the agent."""
        # Register the memory tools
        agent.add_tool(
            name="memory_search",
            func=self.memory_search,
            description="Search for memories matching a query",
        )

        agent.add_tool(
            name="memory_store",
            func=self.memory_store,
            description="Store a memory",
        )

        agent.add_tool(
            name="memory_delete",
            func=self.memory_delete,
            description="Delete a memory",
        )