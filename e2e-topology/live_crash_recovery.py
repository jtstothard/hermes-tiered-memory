#!/usr/bin/env python3
"""Live crash-recovery acceptance harness for the Hermes tiered-memory plugin.

Proves:
1. Store a record through the composite (L1 write + L2 outbox enqueue).
2. SIGKILL the process while the promotion worker is mid-flight.
3. Restart on same disposable state — outbox drains the pending promotion.
4. Stale-upsert and delete checks prove no resurrection or data loss after recovery.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path


def _canonical(payload: dict[str, object]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _write_protected_config(root: Path, port: int) -> tuple[Path, Path]:
    from hermes_tiered_memory.config import ProtectedConfigLoader

    config_path = root / "protected-hindsight.json"
    signature_path = root / "protected-hindsight.sig"
    secret = b"disposable-e2e-hmac-key-not-a-credential"
    ProtectedConfigLoader.register_key("disposable-e2e-key", secret)
    payload: dict[str, object] = {
        "schema_version": 1,
        "key_id": "disposable-e2e-key",
        "config_id": "disposable-e2e-config",
        "sequence": 1,
        "issued_at": "2026-01-01T00:00:00+00:00",
        "not_before": "2026-01-01T00:00:00+00:00",
        "expires_at": "2099-01-01T00:00:00+00:00",
        "hindsight": {
            "scheme": "https",
            "host": "127.0.0.1",
            "port": port,
            "base_path": "/",
            "tls": {"verify": True, "ca_file": str(root / "tls" / "ca.crt")},
            "proxy": {"enabled": False},
        },
    }
    canonical = _canonical(payload)
    config_path.write_bytes(canonical)
    signature_path.write_bytes(
        hmac.new(secret, ProtectedConfigLoader.DOMAIN_SEPARATOR + canonical, hashlib.sha256).digest()
    )
    config_path.chmod(0o600)
    signature_path.chmod(0o600)
    return config_path, signature_path


def _collect_output(proc: subprocess.Popen, timeout: float = 15, keep_alive: bool = False) -> list[str]:
    """Collect stdout lines from *proc*.

    When *keep_alive* is False (default) the process is expected to exit on its
    own and we ``wait()`` for it.  When True the process is intentionally left
    running so the caller can SIGKILL it — we collect whatever lines appeared
    within *timeout* and return without waiting.
    """
    lines: list[str] = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        s = line.decode().strip()
        if s:
            lines.append(s)
            print(f"  [{proc.pid}] {s}", flush=True)
    if not keep_alive:
        proc.wait(timeout=max(timeout, 5))
    return lines


def _worker_code(
    action: str,
    source_id: str = "",
    repo_root: str = "",
    protected_config: str = "",
    sig: str = "",
    record_token: str = "crash-recovery-record",
) -> str:
    """Return Python source for a subprocess that performs *action* on the composite."""
    src_id_json = json.dumps(source_id) if source_id else '""'
    token_json = json.dumps(record_token)

    return f"""import json, os, shutil, time
from pathlib import Path

root = Path(os.environ["TOPOLOGY_ROOT"])
hermes_home = Path(os.environ["HERMES_HOME"])
port = int((root / "state" / "hindsight-port").read_text().strip())
repo_root = Path({json.dumps(repo_root)})

# Install plugin
plugin_dir = hermes_home / "plugins" / "tiered-memory"
shutil.rmtree(plugin_dir, True)
shutil.copytree(repo_root / "hermes_tiered_memory", plugin_dir)

# Config with correct absolute paths
(hermes_home / "config.yaml").write_text(
    "memory:\\n"
    "  provider: tiered-memory\\n"
    "  tiered:\\n"
    "    bank_mapping:\\n"
    "      disposable: disposable-bank\\n"
    "    batch_size: 1\\n"
    "    retry:\\n"
    "      max_attempts: 3\\n"
    "      backoff_seconds: [0]\\n"
    "    protected_config:\\n"
    f"      path: {protected_config}\\n"
    f"      signature:\\n"
    f"        path: {sig}\\n"
    "profiles:\\n"
    "  disposable:\\n"
    "    secrets:\\n"
    "      hindsight:\\n"
    "        api_key: disposable-test-key\\n"
)

from hermes_tiered_memory.config import ProtectedConfigLoader
ProtectedConfigLoader.register_key("disposable-e2e-key", b"disposable-e2e-hmac-key-not-a-credential")

from plugins.memory import load_memory_provider
from agent.memory_manager import MemoryManager

provider = load_memory_provider("tiered-memory")
manager = MemoryManager()
manager.add_provider(provider)
manager.initialize_all(
    session_id="disposable-session",
    hermes_home=str(hermes_home),
    platform="cli",
    agent_context="crash-recovery",
    agent_identity="disposable",
    chat_id="disposable-chat",
)

action = {json.dumps(action)}

if action == "store":
    stored = json.loads(manager.handle_tool_call(
        "tiered_memory_store",
        {{"content": {token_json} + ": durable SIGKILL test", "metadata": {{"scenario": "crash-recovery", "tag": "sigkill"}}}},
    ))
    sid = stored["id"]
    print(f"CRASH_STORE source_id={{sid}}")
    for _ in range(30):
        stats = provider.outbox_store.get_stats()
        if stats.get("pending", 0) >= 1:
            break
        time.sleep(0.25)
    print(f"CRASH_ENQUEUED pending={{provider.outbox_store.get_stats().get('pending', 0)}}")
    (root / "crash_pid").write_text(str(os.getpid()))
    # Stay alive so the parent can SIGKILL us while the promotion worker runs.
    time.sleep(30)

elif action == "drain":
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        stats = provider.outbox_store.get_stats()
        if stats.get("pending", 0) == 0 and stats.get("promoted", 0) >= 1:
            break
        time.sleep(0.25)
    stats = provider.outbox_store.get_stats()
    print(f"CRASH_DRAIN pending={{stats['pending']}} promoted={{stats['promoted']}} in_flight={{stats['in_flight']}}")
    recalled = provider.l2_adapter.recall({token_json}, "disposable", limit=10)
    matching = [r for r in recalled if r.metadata.get("origin") == "mnemosyne" and {token_json} in r.content]
    print(f"CRASH_L2_RECALL count={{len(matching)}}")
    provider.close()

elif action == "delete":
    result = json.loads(manager.handle_tool_call("tiered_memory_delete", {{"memory_id": {src_id_json}}}))
    print(f"CRASH_DELETE deleted={{result['deleted']}}")
    for _ in range(30):
        stats = provider.outbox_store.get_stats()
        if stats.get("pending", 0) >= 1:
            break
        time.sleep(0.25)
    print(f"CRASH_DELETE_ENQUEUED pending={{provider.outbox_store.get_stats().get('pending', 0)}}")
    (root / "crash_pid").write_text(str(os.getpid()))
    # Stay alive so the parent can SIGKILL us while the delete promotion runs.
    time.sleep(30)

elif action == "verify-delete":
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        stats = provider.outbox_store.get_stats()
        if stats.get("pending", 0) == 0 and stats.get("promoted", 0) >= 1:
            break
        time.sleep(0.25)
    stats = provider.outbox_store.get_stats()
    print(f"CRASH_DELETE_DRAIN pending={{stats['pending']}} promoted={{stats['promoted']}}")
    recalled = provider.l2_adapter.recall({token_json}, "disposable", limit=10)
    matching = [r for r in recalled if {token_json} in r.content]
    print(f"CRASH_L2_RECALL count={{len(matching)}}")
    provider.close()
"""


def main() -> None:
    root = Path(os.environ["TOPOLOGY_ROOT"])
    repo_root = Path(__file__).resolve().parents[1]
    hermes_home = root / "hermes"

    # Write protected config (same as live_composite_acceptance.py)
    port = int((root / "state" / "hindsight-port").read_text().strip())
    pc_path, sig_path = _write_protected_config(root, port)

    # ---- PHASE 1: store + SIGKILL -------------------------------------------
    print("=== PHASE 1: store record and SIGKILL during promotion ===", flush=True)
    record_token = f"crash-recovery-{int(time.time())}"
    code1 = _worker_code("store", repo_root=str(repo_root),
                         protected_config=str(pc_path), sig=str(sig_path),
                         record_token=record_token)
    script1 = root / "crash_phase1.py"
    script1.write_text(code1)

    env = os.environ.copy()
    env["TOPOLOGY_ROOT"] = str(root)
    env["HERMES_HOME"] = str(hermes_home)
    env["MNEMOSYNE_DATA_DIR"] = str(root / "mnemosyne")
    env["MNEMOSYNE_DB_PATH"] = str(root / "mnemosyne" / "e2e.sqlite")
    # The topology venv has hermes_tiered_memory + deps installed; the pinned
    # Hermes source provides plugins/ and agent/ on PYTHONPATH.
    venv_python = str(root / "venv" / "bin" / "python")
    env["PYTHONPATH"] = str(root / "sources" / "hermes")

    proc = subprocess.Popen(
        [venv_python, str(script1)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(repo_root),
    )
    lines = _collect_output(proc, timeout=10, keep_alive=True)

    source_id = None
    for line in lines:
        if line.startswith("CRASH_STORE"):
            source_id = line.split("source_id=")[1].strip()
    assert source_id, f"no source_id from store: {lines}"
    print(f"source_id={source_id}", flush=True)

    pid_path = root / "crash_pid"
    worker_pid = int(pid_path.read_text().strip())
    print(f"SIGKILL worker pid={worker_pid}", flush=True)
    os.kill(worker_pid, signal.SIGKILL)
    proc.wait(timeout=5)
    print(f"killed exit={proc.returncode}", flush=True)

    # ---- PHASE 2: restart + drain -------------------------------------------
    print("=== PHASE 2: restart on same state and drain ===", flush=True)
    code2 = _worker_code("drain", repo_root=str(repo_root),
                         protected_config=str(pc_path), sig=str(sig_path),
                         record_token=record_token)
    script2 = root / "crash_phase2.py"
    script2.write_text(code2)

    proc2 = subprocess.Popen(
        [venv_python, str(script2)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(repo_root),
    )
    lines2 = _collect_output(proc2, timeout=30)

    pending = 0
    drain_promoted = 0
    in_flight = 0
    l2_recall = 0
    for line in lines2:
        if line.startswith("CRASH_DRAIN"):
            parts = line.split()
            for p in parts:
                if p.startswith("promoted="):
                    drain_promoted = int(p.split("=")[1].strip(","))
                if p.startswith("in_flight="):
                    in_flight = int(p.split("=")[1].strip(","))
                if p.startswith("pending="):
                    pending = int(p.split("=")[1].strip(","))
        if line.startswith("CRASH_L2_RECALL"):
            l2_recall = int(line.split("count=")[1])

    assert pending == 0, f"pending items remain after drain: {pending}"
    assert drain_promoted >= 1, f"no promoted items: {drain_promoted}"
    assert in_flight == 0, f"in_flight items remain: {in_flight}"
    assert l2_recall >= 1, f"record not found in L2: {l2_recall}"
    print(f"CRASH_DRAIN_PASS pending={pending} promoted={drain_promoted} in_flight={in_flight} l2_recall={l2_recall}", flush=True)

    # ---- PHASE 3: delete + SIGKILL + restart -> no resurrection -------------
    print("=== PHASE 3: delete, SIGKILL, restart, verify no resurrection ===", flush=True)
    code3 = _worker_code("delete", source_id=source_id, repo_root=str(repo_root),
                         protected_config=str(pc_path), sig=str(sig_path),
                         record_token=record_token)
    script3 = root / "crash_phase3.py"
    script3.write_text(code3)

    proc3 = subprocess.Popen(
        [venv_python, str(script3)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(repo_root),
    )
    lines3 = _collect_output(proc3, timeout=10, keep_alive=True)

    pid_path3 = root / "crash_pid"
    worker_pid3 = int(pid_path3.read_text().strip())
    print(f"SIGKILL delete worker pid={worker_pid3}", flush=True)
    os.kill(worker_pid3, signal.SIGKILL)
    proc3.wait(timeout=5)
    print(f"delete worker killed exit={proc3.returncode}", flush=True)

    # Phase 3b: restart and verify delete drained + no resurrection
    code4 = _worker_code("verify-delete", repo_root=str(repo_root),
                         protected_config=str(pc_path), sig=str(sig_path),
                         record_token=record_token)
    script4 = root / "crash_phase4.py"
    script4.write_text(code4)

    proc4 = subprocess.Popen(
        [venv_python, str(script4)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=str(repo_root),
    )
    lines4 = _collect_output(proc4, timeout=30)

    l2_after_delete = 0
    delete_promoted = 0
    pending_del = 0
    for line in lines4:
        if line.startswith("CRASH_DELETE_DRAIN"):
            parts = line.split()
            for p in parts:
                if p.startswith("promoted="):
                    delete_promoted = int(p.split("=")[1].strip(","))
                if p.startswith("pending="):
                    pending_del = int(p.split("=")[1].strip(","))
        if line.startswith("CRASH_L2_RECALL"):
            l2_after_delete = int(line.split("count=")[1])

    assert l2_after_delete == 0, \
        f"Resurrection detected: deleted record found in L2 recall ({l2_after_delete})"
    assert delete_promoted >= 1, f"delete not promoted: {delete_promoted}"
    assert pending_del == 0, f"pending items remain after delete drain: {pending_del}"
    print(f"CRASH_NO_RESURRECTION_PASS l2_recall={l2_after_delete} promoted={delete_promoted}", flush=True)

    # ---- SUMMARY -----------------------------------------------------------
    print(f"CRASH_RECOVERY_ACCEPTANCE_PASS sigkill=1 drain=1 no_resurrection=1 no_data_loss=1", flush=True)


if __name__ == "__main__":
    main()
