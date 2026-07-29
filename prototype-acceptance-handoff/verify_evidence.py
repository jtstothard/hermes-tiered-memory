#!/usr/bin/env python3
"""Independently verify evidence by rerunning the fixed harness.

No result field in evidence.json is trusted for the test outcome: the command,
cwd, outputs, source hashes, and exact case list are independently checked.
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
EVIDENCE_PATH = ROOT / "evidence.json"
FIXED_COMMAND = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-v"]
DISPLAY_COMMAND = "python3 -m unittest discover -s tests -v"
SOURCE_FILES = ("acceptance_harness.py", "tests/test_acceptance.py")
EXPECTED_CASES = [
    "L1 hit", "L2 miss/fallback + L2->L1 backfill",
    "synchronous L1 write + durable outbox enqueue",
    "consolidation promotion + reconciliation", "deterministic identity/idempotent replay",
    "changed payload revision/tombstone", "metadata provenance envelope",
    "L2 outage + stale-while-revalidate", "bounded retries/quarantine/replay",
    "profile/bank isolation", "one composite provider/tool namespace",
]


def fail(message: str) -> None:
    raise AssertionError(message)


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def source_hashes() -> dict[str, str]:
    return {name: sha256_bytes((ROOT / name).read_bytes()) for name in SOURCE_FILES}


def source_manifest_hash(hashes: dict[str, str]) -> str:
    payload = json.dumps(hashes, sort_keys=True, separators=(",", ":")).encode()
    return sha256_bytes(payload)


def main() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text())
    if evidence.get("harness") != "08-prototype-acceptance-handoff":
        fail("unexpected harness")
    if evidence.get("test_command") != DISPLAY_COMMAND:
        fail("evidence command is not the fixed command")
    if evidence.get("test_cwd") != ".":
        fail("evidence cwd is not the controlled harness cwd")
    if evidence.get("release_gate_status") != "BLOCKED":
        fail("prototype must remain BLOCKED for release")
    classification = evidence.get("evidence_classification", {})
    if classification.get("catalog_status") != "NON_CATALOG":
        fail("prototype is not catalog evidence")
    if classification.get("boundary") != "FAKE_BOUNDARY":
        fail("prototype boundary is not explicitly fake")
    if classification.get("release_gate_valid") is not False:
        fail("prototype must not claim release-gate validity")
    if evidence.get("contract_cases") != EXPECTED_CASES:
        fail("contract case IDs/order/cardinality do not exactly match the harness contract")
    if len(evidence["contract_cases"]) != len(EXPECTED_CASES):
        fail("unexpected contract case cardinality")

    actual_sources = source_hashes()
    if evidence.get("source_files") != actual_sources:
        fail("harness/test source hash binding mismatch")
    if evidence.get("source_manifest_sha256") != source_manifest_hash(actual_sources):
        fail("source manifest hash mismatch")

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    run = subprocess.run(FIXED_COMMAND, cwd=ROOT, env=env, text=True, capture_output=True)
    actual_stdout_hash = sha256_bytes(run.stdout.encode())
    actual_stderr_hash = sha256_bytes(run.stderr.encode())
    if run.returncode != evidence.get("test_exit_code"):
        fail(f"recorded exit code {evidence.get('test_exit_code')} != actual {run.returncode}")
    if run.stdout != evidence.get("test_stdout") or run.stderr != evidence.get("test_stderr"):
        fail("recorded test output does not equal freshly captured output")
    if actual_stdout_hash != evidence.get("test_stdout_sha256"):
        fail("stdout hash mismatch")
    if actual_stderr_hash != evidence.get("test_stderr_sha256"):
        fail("stderr hash mismatch")
    if run.returncode != 0:
        fail("fixed prototype test command failed")
    if evidence.get("status") != "PASS":
        fail("status is not bound to a passing fresh run")

    print("INDEPENDENT_EVIDENCE_PASS")
    print(f"validated {len(EXPECTED_CASES)} exact contract cases; fresh_test_exit_code={run.returncode}")


if __name__ == "__main__":
    main()
