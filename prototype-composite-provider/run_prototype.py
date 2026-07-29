#!/usr/bin/env python3
"""PROTOTYPE evidence runner; writes only local scratch evidence.json."""
import json
import pathlib
import subprocess
import sys

from composite_provider import CompositeMemoryProvider, FakeHindsight, FakeMnemosyne, HermesMemoryLoader

root = pathlib.Path(__file__).parent
cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
run = subprocess.run(cmd, cwd=root, text=True, capture_output=True)

l1, l2 = FakeMnemosyne(), FakeHindsight()
p = CompositeMemoryProvider(l1, l2)
loader = HermesMemoryLoader(); loader.load(p)
p.store("p1", "bank-a", "evidence", "value")
p.wait_for_l2(1.0)
summary = {
    "prototype": "01-composite-provider-boundary",
    "status": "PASS" if run.returncode == 0 else "FAIL",
    "command": "cd %s && %s" % (root, " ".join(cmd)),
    "test_exit_code": run.returncode,
    "provider_names": loader.provider_names,
    "tools": loader.tools,
    "l1_events": l1.events,
    "l2_events": l2.events,
    "trace": p.trace,
    "profile_bank_keys": sorted("%s/%s/%s" % k for k in l1.keys()),
    "stdout": run.stdout,
    "stderr": run.stderr,
    "scope": "stdlib fake Mnemosyne/Hindsight only; no production services touched",
}
(root / "evidence.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: summary[k] for k in ("status", "test_exit_code", "provider_names", "tools", "trace", "profile_bank_keys")}, indent=2))
sys.exit(run.returncode)
