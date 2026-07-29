#!/usr/bin/env python3
"""Independent evidence check: validates generated evidence, not implementation internals."""
import json, pathlib, sys
p = pathlib.Path(__file__).parent / "evidence.json"
e = json.loads(p.read_text())
required = {"L1 hit", "L2 miss/fallback + L2->L1 backfill", "synchronous L1 write + durable outbox enqueue",
            "consolidation promotion + reconciliation", "deterministic identity/idempotent replay",
            "changed payload revision/tombstone", "metadata provenance envelope",
            "L2 outage + stale-while-revalidate", "bounded retries/quarantine/replay",
            "profile/bank isolation", "one composite provider/tool namespace"}
actual = set(e["contract_cases"])
assert e["status"] == "PASS", e
assert e["release_gate_status"] == "BLOCKED", e
classification = e["evidence_classification"]
assert classification["catalog_status"] == "NON_CATALOG", classification
assert classification["boundary"] == "synthetic/fake-boundary", classification
assert classification["release_gate_valid"] is False, classification
assert e["test_exit_code"] == 0, e
assert required <= actual, required - actual
assert "stdlib fakes" in e["scope"] and "no live integration" in e["scope"]
print("INDEPENDENT_EVIDENCE_PASS")
print(f"validated {len(required)} contract cases; test_exit_code={e['test_exit_code']}")
