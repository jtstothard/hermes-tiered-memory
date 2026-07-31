#!/usr/bin/env python3
"""Assertions for the live Mnemosyne L1 adapter in a started disposable topology."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    root = Path(os.environ["TOPOLOGY_ROOT"])
    repo_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(repo_root))

    from hermes_tiered_memory.adapters.mnemosyne_l1 import MnemosyneL1Adapter

    data_dir = root / "mnemosyne"
    alpha = MnemosyneL1Adapter(
        data_dir=data_dir,
        profile="adapter-alpha",
        bank="adapter-alpha",
        session_id="session-alpha",
        channel_id="channel-alpha",
    )
    beta = MnemosyneL1Adapter(
        data_dir=data_dir,
        profile="adapter-beta",
        bank="adapter-beta",
        session_id="session-beta",
        channel_id="channel-beta",
    )
    try:
        first = alpha.remember(
            "adapter-alpha-001 vegan recipe notebook",
            {"fixture": "alpha", "nested": {"value": 1}},
        )
        assert first.revision == 1
        assert first.metadata == {"fixture": "alpha", "nested": {"value": 1}}
        assert first.created_at.tzinfo is not None
        assert first.updated_at.tzinfo is not None
        assert alpha.get(first.source_id) == first
        assert alpha.get_revision(first.source_id) == 1
        assert [record.source_id for record in alpha.recall("vegan recipe", limit=5)] == [first.source_id]
        assert beta.recall("vegan recipe", limit=5) == []

        batch = alpha.remember_batch(
            [
                ("adapter-alpha-002 batch first", {"position": 1}),
                ("adapter-alpha-003 batch second", {"position": 2}),
            ]
        )
        assert [record.revision for record in batch] == [1, 1]
        assert [record.metadata["position"] for record in batch] == [1, 2]
        assert alpha.health()["status"] == "healthy"
        assert alpha.health()["record_count"] == 3

        assert alpha.forget(first.source_id) is True
        deleted = alpha.get(first.source_id)
        assert deleted is not None and deleted.is_deleted is True and deleted.revision == 2
        assert alpha.get_revision(first.source_id) == 2
        assert alpha.recall("vegan recipe", limit=5) == []
        assert alpha.forget(first.source_id) is True
        assert alpha.forget("unknown-id") is False

        alpha.close()
        assert alpha.health() == {"status": "closed"}
        try:
            alpha.recall("batch")
        except RuntimeError as exc:
            assert "closed" in str(exc)
        else:
            raise AssertionError("closed adapter accepted recall")
        print("ADAPTER_PASS records=3 profiles=2")
    finally:
        beta.close()
        alpha.close()


if __name__ == "__main__":
    main()
