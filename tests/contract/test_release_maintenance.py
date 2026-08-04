"""Release metadata and subprocess-status regression coverage."""

from __future__ import annotations

import signal
import subprocess
import sys
from pathlib import Path

import hermes_tiered_memory

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_SMOKE = REPO_ROOT / "scripts" / "subprocess_status_smoke.py"


def test_source_version_matches_the_v101_release() -> None:
    assert hermes_tiered_memory.__version__ == "1.0.1"


def test_generated_plugin_metadata_matches_the_v101_release() -> None:
    installer = (REPO_ROOT / "scripts" / "install_plugin.sh").read_text(encoding="utf-8")
    assert 'version: "1.0.1"' in installer


def test_subprocess_status_smoke_preserves_child_status() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(STATUS_SMOKE),
            sys.executable,
            "-c",
            "import sys; sys.exit(37)",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert "CHILD_RETURN_CODE=37" in result.stdout
    assert result.returncode == 37
    assert result.returncode != 0


def test_subprocess_status_smoke_reports_success_separately() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(STATUS_SMOKE),
            sys.executable,
            "-c",
            "print('child completed')",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert "CHILD_RETURN_CODE=0" in result.stdout
    assert result.returncode == 0


def test_subprocess_status_smoke_preserves_signal_status() -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(STATUS_SMOKE),
            sys.executable,
            "-c",
            "import os, signal; os.kill(os.getpid(), signal.SIGTERM)",
        ],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert f"CHILD_RETURN_CODE={-signal.SIGTERM}" in result.stdout
    assert result.returncode == -signal.SIGTERM
