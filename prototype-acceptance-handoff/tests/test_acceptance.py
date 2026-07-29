import json
import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))
from acceptance_harness import (  # noqa: E402
    CompositeProvider, DurableOutbox, FakeL1, FakeL2, HermesLoader, Record,
)


class AcceptanceContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.l1, self.l2 = FakeL1(), FakeL2()
        self.outbox = DurableOutbox(Path(self.tmp.name) / "outbox.json")
        self.p = CompositeProvider(self.l1, self.l2, self.outbox)

    def tearDown(self):
        self.tmp.cleanup()

    def test_composite_single_provider_and_namespace(self):
        loader = HermesLoader(); loader.load(self.p)
        self.assertEqual(loader.providers, ["mnemosyne+hindsight"])
        self.assertEqual(loader.tools, ["memory_search", "memory_store", "memory_delete"])

    def test_l1_hit_skips_l2(self):
        self.p.store("p1", "bank-a", "k", "local")
        self.l2.events.clear()
        result = self.p.search("p1", "bank-a", "k")
        self.assertEqual(result["payload"], "local")
        self.assertEqual(result["source"], "l1")
        self.assertEqual(self.l2.events, [])

    def test_l2_miss_fallback_backfills_l1(self):
        remote = Record("p1", "bank-a", "remote", "durable", metadata={"origin": "p1/bank-a/remote"})
        self.l2.data[remote.origin] = remote
        result = self.p.search("p1", "bank-a", "remote")
        self.assertEqual((result["payload"], result["source"]), ("durable", "l2"))
        self.assertEqual(self.l1.data[remote.origin].payload, "durable")
        self.assertEqual(self.p.trace[-1], ("l2_backfill", remote.origin))

    def test_sync_l1_then_durable_enqueue_then_promotion_and_reconciliation(self):
        result = self.p.store("p1", "bank-a", "summary", "v1")
        self.assertEqual(self.l1.data[result["origin"]].payload, "v1")
        self.assertEqual(self.outbox.pending()[0]["status"], "pending")
        self.assertLess(self.p.trace.index(("l1_write_complete", result["origin"], 1)),
                        self.p.trace.index(("durable_outbox_enqueued", result["origin"], 1)))
        self.p.consolidate("p1", "bank-a", ["summary"])
        self.p.reconcile()
        self.p.drain()
        self.assertEqual(self.l2.data[result["origin"]].payload, "v1")
        self.assertFalse(self.l2.reflect_called)
        self.assertTrue(all(e["status"] == "complete" for e in self.outbox.entries))

    def test_deterministic_identity_and_replay_idempotency(self):
        first = self.p.store("p1", "bank-a", "same", "v1", consolidated=True)
        self.p.drain()
        before = len(self.l2.events)
        restarted = DurableOutbox(self.outbox.path)
        p2 = CompositeProvider(self.l1, self.l2, restarted)
        restarted.replay_completed(); p2.drain()
        self.assertEqual(first["origin"], "p1/bank-a/same")
        self.assertEqual(len(self.l2.data), 1)
        self.assertIn("retain_noop_duplicate", [e[0] for e in self.l2.events[before:]])
        self.assertEqual(restarted.entries[-1]["status"], "complete")

    def test_changed_payload_revision_tombstones_old_and_delete_tombstone(self):
        first = self.p.store("p1", "bank-a", "fact", "old", consolidated=True); self.p.drain()
        second = self.p.store("p1", "bank-a", "fact", "new", consolidated=True); self.p.drain()
        self.assertEqual(second["revision"], 2)
        self.assertEqual(self.l2.data[first["origin"]].payload, "new")
        self.assertIn(("tombstone", first["origin"], 1), self.l2.events)
        self.p.delete("p1", "bank-a", "fact"); self.p.drain()
        self.assertTrue(self.l2.data[first["origin"]].tombstone)
        self.assertEqual(self.l2.data[first["origin"]].revision, 3)

    def test_metadata_provenance_envelope(self):
        result = self.p.store("p1", "bank-a", "meta", "payload", consolidated=True,
                             source="session-7", fact_type="fact", confidence=0.42)
        self.p.drain()
        md = self.l2.data[result["origin"]].metadata
        self.assertEqual({md[k] for k in ("origin", "profile", "bank", "mnemosyne_id", "source")},
                         {"p1/bank-a/meta", "p1", "bank-a", "meta", "session-7"})
        self.assertEqual((md["fact_type"], md["confidence"], md["consolidated"]), ("fact", 0.42, True))

    def test_l2_outage_stale_while_revalidate_and_foreground_survives(self):
        self.p.store("p1", "bank-a", "stale", "cached")
        self.l1.data["p1/bank-a/stale"].metadata["stale"] = True
        self.l2.available = False
        result = self.p.search("p1", "bank-a", "stale")
        self.assertEqual((result["payload"], result["stale"]), ("cached", True))
        self.assertTrue(any(e["operation"] == "refresh" for e in self.outbox.pending()))
        write = self.p.store("p1", "bank-a", "during-outage", "safe")
        self.assertEqual(self.l1.data[write["origin"]].payload, "safe")

    def test_bounded_retry_quarantine_and_replay(self):
        self.p.store("p1", "bank-a", "retry", "payload")
        self.l2.available = False
        for _ in range(3): self.p.drain()
        entry = next(e for e in self.outbox.entries if e["record"]["source_id"] == "retry")
        self.assertEqual((entry["status"], entry["attempts"]), ("quarantined", 3))
        self.l2.available = True
        self.outbox.replay_quarantine(); self.p.drain()
        self.assertEqual(entry["status"], "complete")

    def test_profile_and_bank_isolation(self):
        for profile, bank, value in (("p1", "a", "one"), ("p2", "a", "two"), ("p1", "b", "three")):
            self.p.store(profile, bank, "same", value)
        self.assertEqual(self.p.search("p1", "a", "same")["payload"], "one")
        self.assertEqual(self.p.search("p2", "a", "same")["payload"], "two")
        self.assertEqual(self.p.search("p1", "b", "same")["payload"], "three")
        self.assertIsNone(self.p.search("p2", "b", "same"))
        self.p.drain()
        self.assertEqual(set(self.l2.data), {"p1/a/same", "p2/a/same", "p1/b/same"})

    def test_disconfirming_outage_on_cold_miss_returns_none_without_cross_talk(self):
        self.l2.available = False
        self.assertIsNone(self.p.search("p1", "a", "unknown"))
        self.assertEqual(self.l1.data, {})
        self.assertEqual(self.l2.data, {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
