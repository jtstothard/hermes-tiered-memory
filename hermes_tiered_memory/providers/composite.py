"""Main composite Hermes memory provider."""

from __future__ import annotations

import hashlib
import inspect
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from hermes_tiered_memory.config import ProtectedConfig, ProtectedConfigLoader

logger = logging.getLogger(__name__)


def _load_tiered_runtime_config() -> dict[str, Any] | None:
    """Read the active Hermes config through the provider-supported config seam.

    The pinned ``MemoryManager`` forwards runtime scope to ``initialize()``,
    but not the loaded configuration mapping. Memory-provider plugins own
    their config lookup (for example, pinned ByteRover and OpenViking providers
    read ``memory.<provider>`` with ``load_config_readonly()``). Keep the full
    effective mapping because this provider also needs the active profile's
    Hindsight credential, while promoting ``memory.tiered`` to the provider's
    internal ``tiered`` key.
    """
    try:
        from hermes_cli.config import load_config_readonly

        config = load_config_readonly()
    except Exception:
        logger.warning("Could not load Hermes configuration for tiered memory", exc_info=True)
        return None
    if not isinstance(config, dict):
        return None
    memory = config.get("memory")
    if not isinstance(memory, dict):
        return None
    tiered = memory.get("tiered")
    if not isinstance(tiered, dict):
        return None
    runtime_config = dict(config)
    runtime_config["tiered"] = dict(tiered)
    return runtime_config


try:
    # The real plugin process imports Hermes' pinned public ABC.  The fallback
    # only lets this library's standalone tests run without Hermes installed.
    from agent.memory_provider import MemoryProvider
except ModuleNotFoundError:  # pragma: no cover - standalone test environment
    class MemoryProvider(ABC):
        @property
        @abstractmethod
        def name(self) -> str:
            """Provider identifier."""

        @abstractmethod
        def is_available(self) -> bool:
            """Whether the provider can be activated."""

        @abstractmethod
        def initialize(self, session_id: str, **kwargs: Any) -> None:
            """Initialize provider state."""

        @abstractmethod
        def get_tool_schemas(self) -> list[dict[str, Any]]:
            """Return provider tool schemas."""


class CompositeProvider(MemoryProvider):
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
        session: Any = None,
        platform: str = "",
        hermes_home: str = "",
        context: Any = None,
        user_identity: str = "",
        chat_identity: str = "",
        thread_identity: str = "",
        active_profile_identity: str = "",
    ) -> None:
        """Initialize an inert instance; Hermes supplies runtime scope later."""
        self.session = session
        self.platform = platform
        self.hermes_home = Path(hermes_home) if hermes_home else Path()
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
        self.session_id = ""
        self.agent_context = ""
        self._prefetched: dict[str, list[dict[str, Any]]] = {}
        self._initialized = False
        self._disabled = False
        self._runtime_config: dict[str, Any] = {}

        logger.info(
            f"CompositeProvider initialized for profile={active_profile_identity}, "
            f"user={user_identity}, chat={chat_identity}, thread={thread_identity}"
        )

    @property
    def name(self) -> str:
        """The single external provider identifier visible to Hermes."""
        return "tiered-memory"

    def is_available(self) -> bool:
        """Return whether the real composite implementation is loadable.

        The optional Mnemosyne package is checked during initialization, when
        Hermes has supplied the profile and protected endpoint configuration.
        Registration must not instantiate any adapter or silently select a
        local/mock substitute.
        """
        if self._disabled:
            return False
        try:
            from hermes_tiered_memory.adapters.hindsight_l2 import HindsightL2Adapter
            from hermes_tiered_memory.adapters.mnemosyne_l1 import MnemosyneL1Adapter

            return bool(HindsightL2Adapter and MnemosyneL1Adapter)
        except (ImportError, ModuleNotFoundError):
            return False

    def _disable(self) -> None:
        """Unregister a failed provider before best-effort resource cleanup."""
        self._disabled = True
        self._initialized = False
        frame = inspect.currentframe()
        try:
            while frame is not None:
                manager = frame.f_locals.get("self")
                routes = getattr(manager, "_tool_to_provider", None)
                providers = getattr(manager, "_providers", None)
                if isinstance(routes, dict) and isinstance(providers, list) and self in providers:
                    providers.remove(self)
                    for tool_name, provider in tuple(routes.items()):
                        if provider is self:
                            del routes[tool_name]
                    setattr(
                        manager,
                        "_has_external",
                        any(provider.name != "builtin" for provider in providers),
                    )
                    break
                frame = frame.f_back
        finally:
            del frame
        try:
            self.close()
        except Exception:
            logger.exception("Failed to clean up disabled tiered memory provider")

    def initialize(self, session_id: str, **kwargs: Any) -> None:
        """Initialize the live, profile-scoped composite for one Hermes session."""
        if self._disabled:
            raise RuntimeError("Tiered provider is disabled after a previous initialization failure")
        hermes_home = kwargs.get("hermes_home")
        profile = kwargs.get("agent_identity")
        if not isinstance(hermes_home, str) or not hermes_home:
            self._disable()
            raise RuntimeError("Hermes hermes_home is required; composite provider startup aborted")
        if not isinstance(profile, str) or not profile:
            self._disable()
            raise RuntimeError("Hermes agent_identity is required; composite provider startup aborted")
        if not isinstance(session_id, str) or not session_id:
            self._disable()
            raise RuntimeError("Hermes session_id is required; composite provider startup aborted")

        config = kwargs.get("config")
        if config is None:
            config = _load_tiered_runtime_config()
        if config is None and self.session is not None:
            config = getattr(self.session, "config", None)
        # Pinned Hermes does not pass its loaded config through MemoryManager.
        # Its supported memory-provider configuration seam is the
        # profile-scoped, effective config loader. A config-less invocation
        # remains a valid lifecycle call, but it must fail closed before any
        # adapter/network side effect rather than choose the old SQLite/mock
        # topology.
        if config is None:
            self._disable()
            raise RuntimeError("Tiered provider requires live tiered configuration")
        if not isinstance(config, dict):
            self._disable()
            raise RuntimeError("Tiered provider configuration is required and must be a mapping")
        tiered = config.get("tiered")
        if not isinstance(tiered, dict):
            self._disable()
            raise RuntimeError("Tiered provider configuration requires a tiered mapping")

        self.session_id = session_id
        self.platform = str(kwargs.get("platform", self.platform or "cli"))
        self.hermes_home = Path(hermes_home)
        self.context = kwargs.get("agent_context", self.context)
        self.agent_context = str(kwargs.get("agent_context", ""))
        self.user_identity = str(kwargs.get("user_id", self.user_identity))
        self.chat_identity = str(kwargs.get("chat_id", self.chat_identity))
        self.thread_identity = str(kwargs.get("thread_id", self.thread_identity))
        self.active_profile_identity = profile
        self._runtime_config = config

        try:
            self.initialize_all()
        except Exception as exc:
            self._disable()
            raise RuntimeError("Tiered live adapters could not initialize; startup aborted") from exc
        self._initialized = True

    def get_tool_schemas(self) -> list[dict[str, Any]]:
        """Expose one non-reserved namespace through the Hermes manager."""
        if self._disabled:
            return []
        return [
            {
                "name": "tiered_memory_search",
                "description": "Search the local tiered memory cache.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                    },
                    "required": ["query"],
                },
            },
            {
                "name": "tiered_memory_store",
                "description": "Store a memory in the local tiered cache.",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}, "metadata": {"type": "object"}},
                    "required": ["content"],
                },
            },
            {
                "name": "tiered_memory_delete",
                "description": "Delete a memory from the local tiered cache.",
                "parameters": {
                    "type": "object",
                    "properties": {"memory_id": {"type": "string"}},
                    "required": ["memory_id"],
                },
            },
        ]

    def handle_tool_call(self, tool_name: str, args: dict[str, Any], **kwargs: Any) -> str:
        """Dispatch only advertised tools and always return JSON."""
        del kwargs
        if not isinstance(args, dict):
            raise ValueError("memory tool arguments must be an object")
        if tool_name == "tiered_memory_search":
            query, limit = args.get("query"), args.get("limit", 10)
            if not isinstance(query, str) or not query or isinstance(limit, bool) or not isinstance(limit, int):
                raise ValueError("tiered_memory_search requires string query and integer limit")
            return json.dumps(self.memory_search(query, limit=max(1, min(limit, 100))), sort_keys=True)
        if tool_name == "tiered_memory_store":
            content, metadata = args.get("content"), args.get("metadata", {})
            if not isinstance(content, str) or not content or not isinstance(metadata, dict):
                raise ValueError("tiered_memory_store requires string content and object metadata")
            return json.dumps(self.memory_store(content, metadata), sort_keys=True)
        if tool_name == "tiered_memory_delete":
            memory_id = args.get("memory_id")
            if not isinstance(memory_id, str) or not memory_id:
                raise ValueError("tiered_memory_delete requires string memory_id")
            return json.dumps({"deleted": self.memory_delete(memory_id)}, sort_keys=True)
        raise ValueError(f"Unknown tiered memory tool: {tool_name}")

    def prefetch(self, query: str, *, session_id: str = "") -> str:
        """Return cached recall if present, otherwise perform bounded local recall."""
        key = session_id or self.session_id
        results = self._prefetched.pop(key, None)
        if results is None and query:
            results = self.memory_search(query, limit=5)
        if not results:
            return ""
        return "Tiered memory recall:\n" + "\n".join(f"- {item['content']}" for item in results)

    def queue_prefetch(self, query: str, *, session_id: str = "") -> None:
        """Populate a local cache synchronously; no network thread is needed here."""
        if query:
            self._prefetched[session_id or self.session_id] = self.memory_search(query, limit=5)

    def sync_turn(
        self,
        user_content: str,
        assistant_content: str,
        *,
        session_id: str = "",
        messages: list[dict[str, Any]] | None = None,
    ) -> None:
        """Honor the turn hook without retaining complete transcripts yet."""
        del user_content, assistant_content, messages
        if session_id and session_id != self.session_id:
            self.on_session_switch(session_id)

    def on_session_switch(self, new_session_id: str, **kwargs: Any) -> None:
        """Invalidate session-local prefetch state when Hermes rotates sessions."""
        del kwargs
        self.session_id = new_session_id
        self._prefetched.clear()

    def shutdown(self) -> None:
        """Hermes lifecycle shutdown hook."""
        self.close()

    def initialize_all(self) -> None:
        """Initialize all adapters and the promotion worker."""
        logger.info("Initializing composite provider components")

        try:
            self._load_protected_config()
            self._initialize_l1()
            self._initialize_l2()
            self._initialize_outbox()
            self._initialize_worker()
            logger.info("Composite provider fully initialized")
        except Exception:
            self.close()
            raise

    def _load_protected_config(self) -> None:
        """Load and validate protected Hindsight endpoint configuration."""
        config = self._runtime_config or (self.session.config if self.session else {})
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
        from hermes_tiered_memory.adapters.mnemosyne_l1 import MnemosyneL1Adapter

        tiered = self._runtime_config["tiered"]
        mapping = tiered.get("bank_mapping", {})
        if not isinstance(mapping, dict):
            raise RuntimeError("tiered.bank_mapping must be a mapping")
        bank = mapping.get(self.active_profile_identity, self.active_profile_identity)
        if not isinstance(bank, str) or not bank:
            raise RuntimeError("tiered bank mapping must resolve to a non-empty string")
        channel_id = self.thread_identity or self.chat_identity or self.session_id
        if not channel_id:
            raise RuntimeError("Tiered provider requires a session, chat, or thread scope")
        data_dir = self.hermes_home / "profiles" / self.active_profile_identity / "tiered-memory"
        self.l1_adapter = MnemosyneL1Adapter(
            data_dir=data_dir / "mnemosyne",
            profile=self.active_profile_identity,
            bank=bank,
            session_id=self.session_id,
            channel_id=channel_id,
        )
        logger.info("L1 Mnemosyne adapter initialized for bank=%s", bank)

    def _initialize_l2(self) -> None:
        """Initialize the authenticated L2 (Hindsight) adapter."""
        if not self.protected_config:
            raise RuntimeError("protected Hindsight configuration is unavailable")

        api_key = self._resolve_hindsight_api_key()
        from hermes_tiered_memory.adapters.hindsight_l2 import HindsightL2Adapter

        self.l2_adapter = HindsightL2Adapter(
            self.protected_config.hindsight.to_url(), api_key=api_key
        )
        logger.info("L2 Hindsight adapter initialized")

    def _resolve_hindsight_api_key(self) -> str:
        """Return the active profile's required Hindsight API key without exposing it."""
        profiles = self._runtime_config.get("profiles")
        if not isinstance(profiles, dict):
            raise RuntimeError("Tiered provider requires an active profile Hindsight credential")
        profile = profiles.get(self.active_profile_identity)
        if not isinstance(profile, dict):
            raise RuntimeError("Tiered provider requires an active profile Hindsight credential")
        secrets = profile.get("secrets")
        if not isinstance(secrets, dict):
            raise RuntimeError("Tiered provider requires an active profile Hindsight credential")
        hindsight = secrets.get("hindsight")
        if not isinstance(hindsight, dict):
            raise RuntimeError("Tiered provider requires an active profile Hindsight credential")
        api_key = hindsight.get("api_key")
        if not isinstance(api_key, str) or not api_key or api_key != api_key.strip():
            raise RuntimeError("Tiered provider requires a valid active profile Hindsight credential")
        return api_key

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

        tiered = self._runtime_config["tiered"]
        retry = tiered.get("retry", {})
        if not isinstance(retry, dict):
            raise RuntimeError("tiered.retry must be a mapping")
        batch_size = tiered.get("batch_size", 10)
        max_attempts = retry.get("max_attempts", 5)
        retry_delays = retry.get("backoff_seconds", [10, 30, 90, 300])
        if (
            isinstance(batch_size, bool)
            or not isinstance(batch_size, int)
            or batch_size < 1
            or isinstance(max_attempts, bool)
            or not isinstance(max_attempts, int)
            or max_attempts < 1
            or not isinstance(retry_delays, list)
            or not retry_delays
            or any(isinstance(delay, bool) or not isinstance(delay, int) or delay < 0 for delay in retry_delays)
        ):
            raise RuntimeError("tiered worker settings are invalid")
        config = WorkerConfig(
            batch_size=batch_size,
            retry_delays_seconds=retry_delays,
            max_attempts_per_cycle=max_attempts,
        )
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
                raise RuntimeError("L1 write succeeded but durable L2 enqueue failed") from e

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
                raise RuntimeError("L1 tombstone succeeded but durable L2 delete enqueue failed") from e

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
        """Stop promotion within Hermes' cap and close every owned resource.

        The worker returns unstarted claimed rows to ``pending`` during stop.
        A request already in flight can retain its lease until restart recovery;
        it is never discarded merely because Hermes shutdown reached its cap.
        """
        logger.info("Closing composite provider")
        errors: list[BaseException] = []
        worker, l1, l2, outbox = (
            self.promotion_worker,
            self.l1_adapter,
            self.l2_adapter,
            self.outbox_store,
        )
        self.promotion_worker = None
        self._initialized = False
        worker_stopped = worker is None
        if worker:
            try:
                worker.stop(drain_timeout_seconds=5.0)
                worker_stopped = True
            except Exception as exc:
                errors.append(exc)
        # Never close shared adapters beneath a live worker. Its durable lease
        # stays recoverable by a subsequent process instead of turning a
        # bounded shutdown into a use-after-close race.
        if worker_stopped:
            for label, adapter in (("L1", l1), ("L2", l2), ("outbox", outbox)):
                if adapter is None:
                    continue
                try:
                    adapter.close()
                    logger.info("%s adapter closed", label)
                except Exception as exc:
                    errors.append(exc)
        if errors:
            raise RuntimeError(f"Composite provider close failed: {errors[0]}") from errors[0]
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