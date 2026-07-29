import json
import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

from composite_provider import (  # noqa: E402
    CompositeMemoryProvider,
    FakeHindsight,
    FakeMnemosyne,
    HermesMemoryLoader,
    DuplicateToolError,
)


class CompositeBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.l1 = FakeMnemosyne()
        self.l2 = FakeHindsight()
        self.provider = CompositeMemoryProvider(self.l1, self.l2)
        self.loader = HermesMemoryLoader()
        self.loader.load(self.provider)

    def test_loads_as_one_provider_and_one_tool_namespace(self):
        self.assertEqual(self.loader.provider_names, ["mnemosyne+hindsight"])
        self.assertEqual(self.loader.tools, ["memory_search", "memory_store"])
        self.assertEqual(self.l1.exposed_tools, [])
        self.assertEqual(self.l2.exposed_tools, [])

    def test_l1_hit_does_not_query_l2(self):
        self.l1.write("p1", "bank-a", "project", "from l1")
        result = self.provider.search("p1", "bank-a", "project")
        self.assertEqual(result, "from l1")
        self.assertEqual(self.l1.events[-1][0], "read")
        self.assertNotIn(("search", "p1", "bank-a", "project"), self.l2.events)

    def test_l1_miss_falls_back_to_l2(self):
        self.l2.seed("p1", "bank-a", "remote", "from l2")
        result = self.provider.search("p1", "bank-a", "remote")
        self.assertEqual(result, "from l2")
        self.assertEqual(self.l1.events[-1][0], "read")
        self.assertEqual(self.l2.events[-1], ("search", "p1", "bank-a", "remote"))

    def test_store_returns_after_sync_l1_and_queues_async_l2_retain(self):
        result = self.provider.store("p1", "bank-a", "note", "hello")
        self.assertEqual(result, {"l1": "written", "l2": "queued"})
        self.assertEqual(self.l1.read("p1", "bank-a", "note"), "hello")
        self.assertTrue(self.provider.wait_for_l2(1.0))
        self.assertIn(("retain", "p1", "bank-a", "note", "hello"), self.l2.events)
        self.assertLess(self.provider.trace.index(("l1_write_complete", "p1", "bank-a", "note")),
                        self.provider.trace.index(("l2_queued", "p1", "bank-a", "note")))

    def test_profile_and_bank_isolation(self):
        self.provider.store("p1", "bank-a", "same", "one")
        self.provider.store("p2", "bank-a", "same", "two")
        self.provider.store("p1", "bank-b", "same", "three")
        self.assertEqual(self.provider.search("p1", "bank-a", "same"), "one")
        self.assertEqual(self.provider.search("p2", "bank-a", "same"), "two")
        self.assertEqual(self.provider.search("p1", "bank-b", "same"), "three")
        self.assertIsNone(self.provider.search("p2", "bank-b", "same"))
        self.assertEqual(set(self.l1.keys()), {("p1", "bank-a", "same"), ("p2", "bank-a", "same"), ("p1", "bank-b", "same")})

    def test_disconfirming_duplicate_registration_is_rejected(self):
        with self.assertRaises(DuplicateToolError):
            self.loader.load(FakeMnemosyne(tools=["memory_search"]))
        self.assertEqual(self.loader.tools, ["memory_search", "memory_store"])

    def test_disconfirming_l2_timeout_does_not_lose_l1_write(self):
        failing_l2 = FakeHindsight(fail=True)
        provider = CompositeMemoryProvider(FakeMnemosyne(), failing_l2)
        provider.store("p1", "bank-a", "timeout", "still local")
        self.assertEqual(provider.l1.read("p1", "bank-a", "timeout"), "still local")
        self.assertTrue(provider.wait_for_l2(1.0))
        self.assertEqual(provider.failures[0]["kind"], "l2_retain")
        self.assertEqual(provider.failures[0]["error"], "TimeoutError")

    @classmethod
    def tearDownClass(cls):
        # Evidence is written by the runner after the suite, not by production code.
        pass


if __name__ == "__main__":
    unittest.main()
