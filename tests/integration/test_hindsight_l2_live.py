"""Disposable-topology integration test for the concrete Hindsight L2 adapter."""

from __future__ import annotations

import os
import time
import uuid

import pytest

from hermes_tiered_memory.adapters.hindsight_l2 import HindsightL2Adapter


@pytest.mark.integration
def test_hindsight_l2_against_disposable_topology():
    base_url = os.environ.get("HINDSIGHT_E2E_URL")
    if not base_url:
        pytest.skip("HINDSIGHT_E2E_URL is set only by the disposable topology harness")

    bank = f"adapter-e2e-{uuid.uuid4().hex}"
    document_id = f"adapter-document-{uuid.uuid4().hex}"
    metadata = {
        "origin": "mnemosyne",
        "profile": "adapter-e2e",
        "bank": bank,
        "mnemosyne_id": "synthetic-001",
        "mnemosyne_revision": 2,
        "nested": {"preserved": ["synthetic", 2, None]},
    }
    adapter = HindsightL2Adapter(base_url, timeout_seconds=30)
    try:
        assert adapter.health()["status"] == "healthy"
        async_result = adapter.retain(
            document_id, "synthetic durable adapter record", metadata, async_op=True
        )
        assert async_result.success, async_result.error
        assert async_result.operation_id

        status = None
        for _ in range(30):
            status = adapter.get_operation_status(async_result.operation_id)
            if status["status"] in {"completed", "failed", "cancelled"}:
                break
            time.sleep(1)
        assert status is not None
        assert status["status"] == "completed", status

        sync_result = adapter.retain(
            document_id, "synthetic revised adapter record", metadata, async_op=False
        )
        assert sync_result.success, sync_result.error

        recalled = adapter.recall("synthetic revised adapter record", bank)
        matching = [item for item in recalled if item.document_id == document_id]
        assert len(matching) == 1
        assert matching[0].metadata == metadata
        assert adapter.delete_document(bank, document_id)
    finally:
        adapter.close()
