#!/usr/bin/env python3
"""Independently verify evidence by rerunning the fixed harness.

No result field in evidence.json is trusted for the test outcome: the command,
cwd, outputs, source hashes, and exact case list are independently checked.
"""
import hashlib
import json
import os
import pathlib
import platform
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
REPO_ROOT = ROOT.parent
EVIDENCE_PATH = ROOT / "evidence.json"
FIXED_COMMAND = [sys.executable, "-m", "unittest", "discover", "-s", "prototype-acceptance-handoff/tests", "-v"]
DISPLAY_COMMAND = "python3 -m unittest discover -s prototype-acceptance-handoff/tests -v"
SOURCE_FILES = (
    "acceptance_harness.py",
    "tests/test_acceptance.py",
    "run_evidence.py",
    "verify_evidence.py",
)
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


def stable_test_output(value: str) -> str:
    """Remove unittest's nondeterministic elapsed-time rendering before hashing."""
    return re.sub(r"(Ran \d+ tests? in )\d+(?:\.\d+)?s", r"\g<1><elapsed>", value)


def git(*args: str) -> str:
    return subprocess.check_output(["git", "-C", str(REPO_ROOT), *args], text=True).strip()


def worktree_state() -> list[str]:
    evidence_rel = "prototype-acceptance-handoff/evidence.json"
    paths = []
    for line in git("status", "--porcelain=v1", "--untracked-files=all").splitlines():
        if not line:
            continue
        # Handle both normal porcelain (" M path") and compact test/mocked
        # output ("M path").
        path = line[3:] if len(line) > 2 and line[2] == " " else line[2:]
        path = path.lstrip()
        if path != evidence_rel:
            paths.append(line)
    return paths


def main() -> None:
    evidence = json.loads(EVIDENCE_PATH.read_text())
    if evidence.get("harness") != "08-prototype-acceptance-handoff":
        fail("unexpected harness")
    if evidence.get("test_command") != DISPLAY_COMMAND:
        fail("evidence command is not the fixed command")
    if evidence.get("test_cwd") != ".":
        fail("evidence cwd is not the controlled harness cwd")
    if evidence.get("test_argv") != FIXED_COMMAND:
        fail("evidence argv is not the fixed repository-relative command")
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

    provenance = evidence.get("provenance", {})
    if provenance.get("repository_root") != ".":
        fail("evidence is not rooted at the repository")
    source_revision = provenance.get("source_revision")
    if not isinstance(source_revision, str) or not source_revision:
        fail("missing evidence source revision")
    ancestor_check = subprocess.run(
        ["git", "-C", str(REPO_ROOT), "merge-base", "--is-ancestor", source_revision, "HEAD"],
        capture_output=True,
        text=True,
    )
    if ancestor_check.returncode != 0:
        fail("evidence source revision is not an ancestor of HEAD")
    actual_worktree = worktree_state()
    if provenance.get("worktree_status") != actual_worktree:
        fail("worktree state binding mismatch")
    if actual_worktree:
        fail("repository worktree is dirty outside generated evidence.json")
    if provenance.get("worktree_dirty") is not bool(actual_worktree):
        fail("worktree dirty flag mismatch")
    if provenance.get("worktree_status_sha256") != sha256_bytes("\n".join(actual_worktree).encode()):
        fail("worktree state hash mismatch")
    if provenance.get("runner_source_sha256") != sha256_bytes((ROOT / "run_evidence.py").read_bytes()):
        fail("runner source hash mismatch")
    if provenance.get("verifier_source_sha256") != sha256_bytes(pathlib.Path(__file__).read_bytes()):
        fail("verifier source hash mismatch")
    if evidence["source_files"].get("run_evidence.py") != provenance.get("runner_source_sha256"):
        fail("runner hash is inconsistent with source manifest")
    if evidence["source_files"].get("verify_evidence.py") != provenance.get("verifier_source_sha256"):
        fail("verifier hash is inconsistent with source manifest")
    if provenance.get("interpreter") != sys.version:
        fail("interpreter provenance mismatch")
    if provenance.get("interpreter_executable") != sys.executable:
        fail("interpreter executable provenance mismatch")
    if provenance.get("platform") != platform.platform():
        fail("platform provenance mismatch")
    if provenance.get("python_implementation") != platform.python_implementation():
        fail("python implementation provenance mismatch")

    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONHASHSEED"] = "0"
    run = subprocess.run(FIXED_COMMAND, cwd=REPO_ROOT, env=env, text=True, capture_output=True)
    actual_stdout_hash = sha256_bytes(stable_test_output(run.stdout).encode())
    actual_stderr_hash = sha256_bytes(stable_test_output(run.stderr).encode())
    if run.returncode != evidence.get("test_exit_code"):
        fail(f"recorded exit code {evidence.get('test_exit_code')} != actual {run.returncode}")
    if stable_test_output(run.stdout) != stable_test_output(evidence.get("test_stdout", "")) or stable_test_output(run.stderr) != stable_test_output(evidence.get("test_stderr", "")):
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
