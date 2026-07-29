#!/usr/bin/env python3
"""Reproducible acceptance/evidence command for the throwaway harness."""
import json, pathlib, subprocess, sys
root = pathlib.Path(__file__).parent
cmd = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
run = subprocess.run(cmd, cwd=root, text=True, capture_output=True)
summary = {
    "harness": "08-prototype-acceptance-handoff",
    "status": "PASS" if run.returncode == 0 else "FAIL",
    "test_exit_code": run.returncode,
    "test_command": "python3 -m unittest discover -s tests -v",
    "test_stdout": run.stdout,
    "test_stderr": run.stderr,
    "contract_cases": [
        "L1 hit", "L2 miss/fallback + L2->L1 backfill",
        "synchronous L1 write + durable outbox enqueue",
        "consolidation promotion + reconciliation", "deterministic identity/idempotent replay",
        "changed payload revision/tombstone", "metadata provenance envelope",
        "L2 outage + stale-while-revalidate", "bounded retries/quarantine/replay",
        "profile/bank isolation", "one composite provider/tool namespace",
    ],
    "scope": "stdlib fakes and local file-backed outbox only; no live integration or production changes",
}
(root / "evidence.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: summary[k] for k in ("harness", "status", "test_exit_code", "contract_cases", "scope")}, indent=2))
sys.exit(run.returncode)
