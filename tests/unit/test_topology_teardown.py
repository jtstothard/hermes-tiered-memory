"""Safety and cleanup behavior for the disposable HTTPS topology script."""

from __future__ import annotations

import os
import subprocess
import uuid
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT = REPO_ROOT / "e2e-topology" / "topology.sh"


def _run_teardown(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), "teardown"],
        cwd=REPO_ROOT,
        env=os.environ | {"TOPOLOGY_ROOT": str(root)},
        check=False,
        capture_output=True,
        text=True,
    )


def test_teardown_removes_disposable_https_ca_and_server_material() -> None:
    root = Path("/tmp") / f"hermes-tiered-memory-https-{uuid.uuid4().hex}"
    tls_dir = root / "tls"
    tls_dir.mkdir(parents=True)
    for filename in ("ca.crt", "ca.key", "hindsight.crt", "hindsight.key"):
        (tls_dir / filename).write_text("disposable only")

    result = _run_teardown(root)

    assert result.returncode == 0, result.stderr
    assert not root.exists()
    assert "TOPOLOGY_TORN_DOWN" in result.stdout


def test_teardown_refuses_unowned_root(tmp_path: Path) -> None:
    root = tmp_path / "not-disposable"
    root.mkdir()
    marker = root / "must-remain"
    marker.write_text("do not delete")

    result = _run_teardown(root)

    assert result.returncode != 0
    assert root.exists()
    assert marker.exists()
    assert "unsafe topology root" in result.stderr