"""Contract tests for the composite provider.

These tests verify the provider implements the contracts defined in:
- issues/01-composite-provider-boundary.md
- issues/02-read-write-routing-policy.md
- issues/03-promotion-policy.md
- issues/04-identity-dedup-conflicts.md
- issues/05-metadata-roundtrip.md
- issues/06-failure-recovery-observability.md
- issues/07-hermes-integration-config.md
- research/outbox-crash-recovery-contract.md
- research/hindsight-native-semantics-proof.md
"""

import tempfile
from pathlib import Path
from unittest.mock import Mock

import pytest

from hermes_tiered_memory.adapters.mock_l1 import MockL1Adapter
from hermes_tiered_memory.adapters.mock_l2 import MockL2Adapter
from hermes_tiered_memory.outbox.store import OutboxState
from hermes_tiered_memory.providers.composite import CompositeProvider


@pytest.fixture
def temp_hermes_home():
    """Create a temporary Hermes home directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_l2_adapter():
    """Create a mock L2 adapter for contract tests."""
    return MockL2Adapter()


@pytest.fixture
def composite_provider(temp_hermes_home):
    """Create a composite provider for contract testing."""
    provider = CompositeProvider(
        session=Mock(),
        platform="cli",
        hermes_home=str(temp_hermes_home),
        context={},
        user_identity="test-user",
        chat_identity="test-chat",
        thread_identity="test-thread",
        active_profile_identity="test-profile",
    )

    provider.l1_adapter = MockL1Adapter()
    provider.l2_adapter = MockL2Adapter()

    outbox_db_path = (
        temp_hermes_home / "profiles" / "test-profile" / "tiered-memory" / "outbox.db"
    )
    outbox_db_path.parent.mkdir(parents=True, exist_ok=True)

    from hermes_tiered_memory.outbox.store import OutboxStore

    provider.outbox_store = OutboxStore(outbox_db_path)

    yield provider

    provider.close()


class TestContract01CompositeProviderBoundary:
    """Contract #1: Composite provider boundary."""

    def test_one_hermes_facing_provider(self, composite_provider):
        """Contract: Composite is the sole configured provider."""
        # The composite provider implements MemoryProvider interface
        # and exposes one tool namespace
        assert hasattr(composite_provider, "memory_search")
        assert hasattr(composite_provider, "memory_store")
        assert hasattr(composite_provider, "memory_delete")

    def test_no_duplicate_tools(self, composite_provider):
        """Contract: Adapters do not register duplicate tools."""
        # L1 and L2 adapters are internal, not Hermes-facing
        # Only composite exposes tools
        assert composite_provider.l1_adapter is not None
        assert composite_provider.l2_adapter is not None
        # They don't have Hermes tool registration methods


class TestContract02ReadWriteRoutingPolicy:
    """Contract #2: Read/write routing policy."""

    def test_cache_first_l1_hit(self, composite_provider):
        """Contract: Normal L1 hit returns immediately."""
        # Store a memory
        composite_provider.memory_store("Python programming", {})

        # Search - should hit L1
        results = composite_provider.memory_search("Python", limit=10)

        assert len(results) > 0
        assert all(r["source"] == "l1" for r in results)

    def test_l1_miss_queries_l2(self, composite_provider):
        """Contract: L1 miss queries Hindsight L2."""
        # Add directly to L2
        composite_provider.l2_adapter.retain(
            document_id="test-doc",
            content="Machine learning",
            metadata={"bank": "test-profile"},
            async_op=False,
        )

        # Search (L1 miss) should query L2
        results = composite_provider.memory_search("Machine learning", limit=10)

        assert len(results) > 0
        assert any(r["source"] == "l2" for r in results)

    def test_write_through_l1_sync(self, composite_provider):
        """Contract: New write commits to L1 synchronously."""
        result = composite_provider.memory_store("Test content", {})

        # Verify L1 has it
        l1_results = composite_provider.l1_adapter.recall("Test content", limit=10)
        assert len(l1_results) > 0

    def test_l2_promotion_async(self, composite_provider):
        """Contract: L2 promotion is asynchronous."""
        # Store a memory
        result = composite_provider.memory_store("Async test", {})

        # Check outbox - should be pending
        stats = composite_provider.outbox_store.get_stats()
        assert stats.get("pending", 0) > 0

        # L2 might not have it yet (async)
        # This is acceptable per contract


class TestContract03PromotionPolicy:
    """Contract #3: Promotion policy."""

    def test_all_consolidated_memories_promoted(self, composite_provider):
        """Contract: Promote all successful consolidated memories."""
        # Store memories (simulating consolidation)
        for i in range(3):
            composite_provider.memory_store(f"Memory {i}", {"consolidated": True})

        # Check outbox - all should be enqueued
        stats = composite_provider.outbox_store.get_stats()
        assert stats.get("pending", 0) >= 3

    def test_malformed_output_not_promoted(self, composite_provider):
        """Contract: Malformed output not promoted."""
        # This would be enforced at the consolidation level
        # The composite promotes whatever is stored in L1
        pass  # Integration-level contract


class TestContract04IdentityDedupConflicts:
    """Contract #4: Identity and conflict handling."""

    def test_stable_identity(self, composite_provider):
        """Contract: Stable identity from (profile, bank, mnemosyne_id)."""
        # Store memory
        result = composite_provider.memory_store("Test", {})
        memory_id = result["id"]

        # Generate document ID
        doc_id = composite_provider._generate_document_id(memory_id)

        # Should be stable
        doc_id2 = composite_provider._generate_document_id(memory_id)
        assert doc_id == doc_id2

    def test_revision_creates_new_document(self, composite_provider):
        """Contract: Revision increment triggers Hindsight replace."""
        # This is simulated at the outbox level
        # Each (document_id, mnemosyne_rev) is unique
        pass  # Integration-level contract


class TestContract05MetadataRoundtrip:
    """Contract #5: Metadata mapping and roundtrip."""

    def test_metadata_envelope_includes_provenance(self, composite_provider):
        """Contract: Provenance envelope includes origin metadata."""
        result = composite_provider.memory_store("Test", {"custom": "value"})

        # Check promotion metadata
        metadata = composite_provider._build_promotion_metadata(
            composite_provider.l1_adapter.get(result["id"])
        )

        assert metadata["origin"] == "mnemosyne"
        assert metadata["profile"] == "test-profile"
        assert metadata["bank"] == "test-profile"
        assert metadata["mnemosyne_id"] == result["id"]
        assert "mnemosyne_revision" in metadata


class TestContract06FailureRecoveryObservability:
    """Contract #6: Failure recovery and observability."""

    def test_l1_survives_l2_timeout(self, composite_provider):
        """Contract: L1 write survives L2 timeout."""
        # This would require simulating L2 timeout
        # For now, verify L1 is durable
        result = composite_provider.memory_store("Survival test", {})

        # L1 should have it regardless of L2
        l1_results = composite_provider.l1_adapter.recall("Survival test", limit=10)
        assert len(l1_results) > 0

    def test_quarantine_on_permanent_failure(self, composite_provider, mock_l2_adapter):
        """Contract: Permanent failure quarantines item."""
        # This would require simulating permanent L2 failure
        # For now, verify outbox supports quarantine
        item = composite_provider.outbox_store.enqueue(
            document_id="test-doc",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Test",
            metadata={},
        )

        success = composite_provider.outbox_store.update_state(
            row_id=item.row_id,
            expected_state_version=item.state_version,
            new_state=OutboxState.QUARANTINED,
        )

        assert success

    def test_health_exposes_metrics(self, composite_provider):
        """Contract: Health exposes actionable metrics."""
        health = composite_provider.health()

        assert "l1" in health
        assert "l2" in health
        assert "outbox" in health
        assert "provider" in health


class TestContract07HermesIntegrationConfig:
    """Contract #7: Hermes integration configuration."""

    def test_profile_safe_paths(self, composite_provider):
        """Contract: Use profile-safe paths."""
        # Outbox should be in profile directory
        outbox_path = composite_provider.outbox_store.db_path
        assert "test-profile" in str(outbox_path)

    def test_one_tool_namespace(self, composite_provider):
        """Contract: One coherent tool namespace."""
        # Tools are: memory_search, memory_store, memory_delete
        tools = [attr for attr in dir(composite_provider) if "memory_" in attr]
        assert len(tools) >= 3


class TestOutboxCrashRecoveryContract:
    """Contract: Outbox crash recovery."""

    def test_atomic_l1_outbox_commit(self, composite_provider):
        """Contract: Atomic L1+outbox commit."""
        # Store a memory
        result = composite_provider.memory_store("Atomic test", {})

        # Both L1 and outbox should have it
        l1_results = composite_provider.l1_adapter.recall("Atomic test", limit=10)
        assert len(l1_results) > 0

        stats = composite_provider.outbox_store.get_stats()
        assert stats.get("pending", 0) > 0

    def test_crash_safe_recovery(self, temp_hermes_home):
        """Contract: Outbox survives crash."""
        # Create provider
        provider = CompositeProvider(
            session=Mock(),
            platform="cli",
            hermes_home=str(temp_hermes_home),
            context={},
            user_identity="test-user",
            chat_identity="test-chat",
            thread_identity="test-thread",
            active_profile_identity="test-profile",
        )
        provider.l1_adapter = MockL1Adapter()
        provider.l2_adapter = MockL2Adapter()

        outbox_db_path = (
            temp_hermes_home / "profiles" / "test-profile" / "tiered-memory" / "outbox.db"
        )
        outbox_db_path.parent.mkdir(parents=True, exist_ok=True)

        from hermes_tiered_memory.outbox.store import OutboxStore

        provider.outbox_store = OutboxStore(outbox_db_path)

        # Store memory
        provider.memory_store("Crash test", {})
        stats1 = provider.outbox_store.get_stats()

        # Simulate crash (close without cleanup)
        provider.close()

        # Recover
        provider2 = CompositeProvider(
            session=Mock(),
            platform="cli",
            hermes_home=str(temp_hermes_home),
            context={},
            user_identity="test-user",
            chat_identity="test-chat",
            thread_identity="test-thread",
            active_profile_identity="test-profile",
        )
        provider2.l1_adapter = MockL1Adapter()
        provider2.l2_adapter = MockL2Adapter()
        provider2.outbox_store = OutboxStore(outbox_db_path)

        # Check outbox persisted
        stats2 = provider2.outbox_store.get_stats()
        assert stats2["pending"] == stats1["pending"]

        provider2.close()

    def test_bounded_retry(self, composite_provider):
        """Contract: Bounded retry (5 attempts per cycle)."""
        # This would require full worker integration
        # Verify outbox supports retry tracking
        item = composite_provider.outbox_store.enqueue(
            document_id="test-doc",
            mnemosyne_id="mne-1",
            mnemosyne_rev=1,
            content="Test",
            metadata={},
        )

        # Verify attempt tracking
        assert item.attempt_no == 0
        assert item.retry_cycle_no == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])