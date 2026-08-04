"""Release metadata and subprocess-status regression coverage."""

from __future__ import annotations

import re
import signal
import subprocess
import sys
from pathlib import Path

import hermes_tiered_memory

REPO_ROOT = Path(__file__).resolve().parents[2]
STATUS_SMOKE = REPO_ROOT / "scripts" / "subprocess_status_smoke.py"
INSTALLER = REPO_ROOT / "scripts" / "install_plugin.sh"
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"
INSTALL = REPO_ROOT / "INSTALL.md"

EXPECTED_VERSION = "1.0.1"
WHEEL_FILENAME = f"hermes_tiered_memory-{EXPECTED_VERSION}-py3-none-any.whl"
STALE_PROSE_RE = re.compile(r"installs the v\d+\.\d+\.\d+ package")
PYPROJECT_VERSION_RE = re.compile(r'^version\s*=\s*"([^"]+)"', re.MULTILINE)


def _pyproject_version() -> str:
    """Extract the PEP 621 version from pyproject.toml without tomllib extras."""
    text = PYPROJECT.read_text(encoding="utf-8")
    match = PYPROJECT_VERSION_RE.search(text)
    assert match, "pyproject.toml has no version declaration"
    return match.group(1)


def test_source_version_matches_the_release() -> None:
    assert hermes_tiered_memory.__version__ == EXPECTED_VERSION


def test_pyproject_version_matches_source() -> None:
    assert _pyproject_version() == hermes_tiered_memory.__version__


def test_install_plugin_generates_matching_metadata() -> None:
    installer = INSTALLER.read_text(encoding="utf-8")
    assert f'version: "{EXPECTED_VERSION}"' in installer


def test_readme_wheel_filename_matches_release() -> None:
    readme = README.read_text(encoding="utf-8")
    assert WHEEL_FILENAME in readme, f"README.md does not reference the {WHEEL_FILENAME} wheel"


def test_install_wheel_filename_matches_release() -> None:
    install = INSTALL.read_text(encoding="utf-8")
    assert WHEEL_FILENAME in install, f"INSTALL.md does not reference the {WHEEL_FILENAME} wheel"


def test_install_guide_does_not_reference_stale_release_version() -> None:
    """The install guide prose must not claim a different release than the source."""
    install = INSTALL.read_text(encoding="utf-8")
    stale_prose = STALE_PROSE_RE.search(install)
    assert stale_prose is None, f"INSTALL.md still pins prose to {stale_prose.group(0)!r}"


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


def test_subprocess_status_smoke_returns_usage_error_without_a_command() -> None:
    """No-arg invocation must surface the documented usage error code."""
    result = subprocess.run(
        [sys.executable, str(STATUS_SMOKE)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 2
    assert "usage:" in result.stderr.lower()
