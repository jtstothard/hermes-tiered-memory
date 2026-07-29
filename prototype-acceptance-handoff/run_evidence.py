#!/usr/bin/env python3
"""Run the fixed prototype harness and emit content-bound evidence.

This is deliberately not release evidence: it records a synthetic/fake boundary.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
FIXED_COMMAND = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
DISPLAY_COMMAND = "python3 -m unittest discover -s tests -v"
SOURCE_FILES = ("acceptance_harness.py", "tests/test_acceptance.py")
CONTRACT_CASES = [
    "L1 hit", "L2 miss/fallback + L2->L1 backfill",
    "synchronous L1 write + durable outbox enqueue",
    "consolidation promotion + reconciliation", "deterministic identity/idempotent replay",
    "changed payload revision/tombstone", "metadata provenance envelope",
    "L2 outage + stale-while-revalidate", "bounded retries/quarantine/replay",
    "profile/bank isolation", "one composite provider/tool namespace",
]


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha256_bytes((ROOT / name).read_bytes()) for name in SOURCE_FILES}


def source_manifest_hash(hashes: dict[str, str]) -> str:
    payload = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(payload)


# Keep the environment bounded and prevent an ambient PYTHONPATH from changing the harness.
env = dict(os.environ)
env.pop("PYTHONPATH", None)
env["PYTHONDONTWRITEBYTECODE"] = "1"
env["PYTHONHASHSEED"] = "0"
run = subprocess.run(FIXED_COMMAND, cwd=ROOT, env=env, text=True, capture_output=True)
hashes = source_hashes()
summary = {
    "harness": "08-prototype-acceptance-handoff",
    "status": "PASS" if run.returncode == 0 else "FAIL",
    "release_gate_status": "BLOCKED",
    "evidence_classification": {
        "catalog_status": "NON_CATALOG",
        "boundary": "FAKE_BOUNDARY",
        "release_gate_valid": False,
        "reason": "Prototype tests use stdlib fakes and a local file-backed outbox; they do not exercise the pinned real Hermes loader, a release artifact, a declared matrix cell, or release-gate provenance.",
    },
    "test_exit_code": run.returncode,
    "test_command": DISPLAY_COMMAND,
    "test_cwd": ".",
    "test_stdout": run.stdout,
    "test_stderr": run.stderr,
    "test_stdout_sha256": sha256_bytes(run.stdout.encode()),
    "test_stderr_sha256": sha256_bytes(run.stderr.encode()),
    "source_files": hashes,
    "source_manifest_sha256": source_manifest_hash(hashes),
    "contract_cases": CONTRACT_CASES,
    "scope": "stdlib fakes and local file-backed outbox only; no live integration or production changes",
}
(ROOT / "evidence.json").write_text(json.dumps(summary, indent=2) + "\n")
print(json.dumps({k: summary[k] for k in ("harness", "status", "test_exit_code", "contract_cases", "scope")}, indent=2))
sys.exit(run.returncode)
