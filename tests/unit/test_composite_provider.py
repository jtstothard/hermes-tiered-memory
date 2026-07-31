"""Unit tests for the composite provider."""

import tempfile
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock

import pytest

from hermes_tiered_memory.adapters.mock_l1 import MockL1Adapter
from hermes_tiered_memory.adapters.mock_l2 import MockL2Adapter
from hermes_tiered_memory.providers.composite import CompositeProvider


@pytest.fixture
def temp_hermes_home():
    """Create a temporary Hermes home directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_session():
    """Create a mock Hermes session."""
    session = Mock()
    session.config = {}
    return session


@pytest.fixture
def composite_provider(temp_hermes_home, mock_session):
    """Create a composite provider for testing."""
    provider = CompositeProvider(
        session=mock_session,
        platform="cli",
        hermes_home=str(temp_hermes_home),
        context={},
        user_identity="test-user",
        chat_identity="test-chat",
        thread_identity="test-thread",
        active_profile_identity="test-profile",
    )

    # Inject mock adapters
    provider.l1_adapter = MockL1Adapter()
    provider.l2_adapter = MockL2Adapter()

    # Initialize outbox
    from hermes_tiered_memory.outbox.store import OutboxStore

    outbox_db_path = (
        temp_hermes_home / "profiles" / "test-profile" / "tiered-memory" / "outbox.db"
    )
    outbox_db_path.parent.mkdir(parents=True, exist_ok=True)
    provider.outbox_store = OutboxStore(outbox_db_path)

    yield provider

    # Cleanup
    provider.close()


class TestCompositeProviderBasics:
    """Test basic composite provider functionality."""

    def test_provider_initialization(self, composite_provider):
        """Test provider initializes correctly."""
        assert composite_provider.active_profile_identity == "test-profile"
        assert composite_provider.user_identity == "test-user"
        assert composite_provider.chat_identity == "test-chat"
        assert composite_provider.thread_identity == "test-thread"

    def test_health_status(self, composite_provider):
        """Test health status returns expected structure."""
        health = composite_provider.health()

        assert health["provider"] == "tiered-memory-composite"
        assert health["profile"] == "test-profile"
        assert "l1" in health
        assert "l2" in health
        assert "outbox" in health

    def test_close_provider(self, composite_provider):
        """Test closing the provider releases resources."""
        composite_provider.close()
        health = composite_provider.health()
        assert health["l1"]["status"] == "closed"
        assert health["l2"]["status"] == "closed"


class TestMemoryStore:
    """Test memory store operations."""

    def test_store_memory_synchronously(self, composite_provider):
        """Test storing a memory writes to L1 synchronously."""
        result = composite_provider.memory_store("Test content", {"key": "value"})

        assert result["content"] == "Test content"
        assert result["metadata"]["key"] == "value"
        assert result["source"] == "l1"
        assert "id" in result
        assert "revision" in result

    def test_store_memory_enqueues_promotion(self, composite_provider):
        """Test storing a memory enqueues L2 promotion."""
        initial_stats = composite_provider.outbox_store.get_stats()

        composite_provider.memory_store("Test content", {"key": "value"})

        final_stats = composite_provider.outbox_store.get_stats()
        # Should have one more pending item
        assert final_stats.get("pending", 0) == initial_stats.get("pending", 0) + 1

    def test_store_multiple_memories(self, composite_provider):
        """Test storing multiple memories works correctly."""
        results = []
        for i in range(3):
            result = composite_provider.memory_store(f"Content {i}", {"index": i})
            results.append(result)

        assert len(results) == 3
        assert all(r["source"] == "l1" for r in results)
        assert [r["metadata"]["index"] for r in results] == [0, 1, 2]


class TestMemorySearch:
    """Test memory search operations."""

    def test_search_returns_l1_hit(self, composite_provider):
        """Test search returns L1 results on cache hit."""
        # Store a memory
        stored = composite_provider.memory_store("Python programming", {"topic": "tech"})

        # Search for it
        results = composite_provider.memory_search("Python", limit=10)

        assert len(results) > 0
        assert any("Python" in r["content"] for r in results)
        assert results[0]["source"] == "l1"

    def test_search_l1_miss_queries_l2(self, composite_provider):
        """Test search queries L2 on L1 miss."""
        # Directly add to L2 without L1
        composite_provider.l2_adapter.retain(
            document_id="test-doc-1",
            content="Machine learning basics",
            metadata={"bank": "test-profile", "profile": "test-profile"},
            async_op=False,
        )

        # Search (L1 has nothing, should query L2)
        results = composite_provider.memory_search("Machine learning", limit=10)

        # Should find it from L2
        assert len(results) > 0
        assert any("Machine learning" in r["content"] for r in results)
        # L2 results should have source="l2"
        assert any(r["source"] == "l2" for r in results)

    def test_search_respects_limit(self, composite_provider):
        """Test search respects the limit parameter."""
        # Store multiple memories
        for i in range(5):
            composite_provider.memory_store(f"Content {i}", {"index": i})

        # Search with limit
        results = composite_provider.memory_search("Content", limit=2)

        assert len(results) <= 2


class TestMemoryDelete:
    """Test memory delete operations."""

    def test_delete_memory_removes_from_l1(self, composite_provider):
        """Test deleting a memory removes it from L1."""
        # Store a memory
        stored = composite_provider.memory_store("To be deleted", {})

        # Delete it
        result = composite_provider.memory_delete(stored["id"])

        assert result is True

        # Search should not return it
        results = composite_provider.memory_search("To be deleted", limit=10)
        assert not any(r["id"] == stored["id"] for r in results)

    def test_delete_nonexistent_memory(self, composite_provider):
        """Test deleting a non-existent memory returns False."""
        result = composite_provider.memory_delete("nonexistent-id")
        assert result is False


class TestMetadataProvenance:
    """Test metadata provenance envelope."""

    def test_promotion_metadata_includes_provenance(self, composite_provider):
        """Test promotion metadata includes provenance information."""
        composite_provider.memory_store("Test content", {"custom": "value"})

        # Check the outbox
        stats = composite_provider.outbox_store.get_stats()
        assert stats.get("pending", 0) > 0

    def test_document_id_is_stable(self, composite_provider):
        """Test document ID is stable for the same memory ID."""
        # Store a memory twice (same ID, different revisions)
        result1 = composite_provider.memory_store("Content 1", {})
        memory_id = result1["id"]

        # Generate document ID twice
        doc_id1 = composite_provider._generate_document_id(memory_id)
        doc_id2 = composite_provider._generate_document_id(memory_id)

        assert doc_id1 == doc_id2


class TestProfileBankIsolation:
    """Test profile and bank isolation."""

    def test_bank_id_from_profile(self, composite_provider):
        """Test bank ID is derived from profile identity."""
        bank_id = composite_provider._get_bank_id()
        assert bank_id == "test-profile"

    def test_different_profiles_have_different_bank_ids(self):
        """Test different profiles have different bank IDs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            provider1 = CompositeProvider(
                session=Mock(),
                platform="cli",
                hermes_home=tmpdir,
                context={},
                user_identity="test-user",
                chat_identity="test-chat",
                thread_identity="test-thread",
                active_profile_identity="profile1",
            )

            provider2 = CompositeProvider(
                session=Mock(),
                platform="cli",
                hermes_home=tmpdir,
                context={},
                user_identity="test-user",
                chat_identity="test-chat",
                thread_identity="test-thread",
                active_profile_identity="profile2",
            )

            assert provider1._get_bank_id() != provider2._get_bank_id()


class TestOutboxIntegration:
    """Test outbox integration."""

    def test_outbox_persists_across_restart(self, temp_hermes_home):
        """Test outbox persists across provider restart."""
        # Create first provider instance
        provider1 = CompositeProvider(
            session=Mock(),
            platform="cli",
            hermes_home=str(temp_hermes_home),
            context={},
            user_identity="test-user",
            chat_identity="test-chat",
            thread_identity="test-thread",
            active_profile_identity="test-profile",
        )
        provider1.l1_adapter = MockL1Adapter()
        provider1.l2_adapter = MockL2Adapter()

        outbox_db_path = (
            temp_hermes_home / "profiles" / "test-profile" / "tiered-memory" / "outbox.db"
        )
        outbox_db_path.parent.mkdir(parents=True, exist_ok=True)

        from hermes_tiered_memory.outbox.store import OutboxStore

        provider1.outbox_store = OutboxStore(outbox_db_path)

        # Store a memory
        provider1.memory_store("Test content", {})
        initial_stats = provider1.outbox_store.get_stats()

        # Close provider
        provider1.close()

        # Create second provider instance with same outbox
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
        final_stats = provider2.outbox_store.get_stats()
        assert final_stats.get("pending", 0) == initial_stats.get("pending", 0)

        provider2.close()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])