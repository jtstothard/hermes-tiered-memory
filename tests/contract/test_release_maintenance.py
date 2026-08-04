"""Release metadata and subprocess-status regression coverage."""

from __future__ import annotations

import re
import signal
import subprocess
import sys
import tomllib
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
# Release-version references in prose use a "v" prefix (e.g. "v1.0.0"). Bare
# semver strings are ignored: dependency specs like "hermes-agent>=0.7.0" are
# not release-version claims about this package.
VERSION_REF_RE = re.compile(r"\bv(\d+\.\d+\.\d+)\b")


def _pyproject_version() -> str:
    """Read the PEP 621 project version via tomllib (handles all TOML shapes)."""
    with PYPROJECT.open("rb") as fh:
        data = tomllib.load(fh)
    version = data["project"]["version"]
    assert isinstance(version, str), "pyproject.toml [project].version must be a string"
    return version


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


def test_install_guide_has_no_stale_release_version_prose() -> None:
    """INSTALL must not name any release version other than the current one.

    Guards both the historical "installs the vX.Y.Z package" sentence and any
    v-prefixed version reference that disagrees with the source of truth.
    Bare semver (e.g. dependency specs) is intentionally ignored.
    """
    install = INSTALL.read_text(encoding="utf-8")

    for match in VERSION_REF_RE.finditer(install):
        found = match.group(1)
        assert found == EXPECTED_VERSION, (
            f"INSTALL.md references release version v{found} at offset "
            f"{match.start()}, expected v{EXPECTED_VERSION}"
        )


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


def test_built_wheel_metadata_version_matches_release(tmp_path: Path) -> None:
    """Build a wheel in an isolated tmp dir and verify its METADATA Version field.

    This is the end-to-end release artifact that ships to PyPI; its declared
    version must equal the source version. Skips cleanly when the PEP 517
    `build` frontend is unavailable rather than masking the failure.
    """
    try:
        import build.__main__  # noqa: F401
    except ModuleNotFoundError:
        import pytest

        pytest.skip("PEP 517 build frontend not installed in this environment")

    build_result = subprocess.run(
        [sys.executable, "-m", "build", "--wheel", "--outdir", str(tmp_path), str(REPO_ROOT)],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert (
        build_result.returncode == 0
    ), f"wheel build failed:\nstdout:\n{build_result.stdout}\nstderr:\n{build_result.stderr}"

    wheels = list(tmp_path.glob("hermes_tiered_memory-*.whl"))
    assert wheels, f"no wheel produced in {tmp_path}"
    assert len(wheels) == 1, f"ambiguous wheel set: {[w.name for w in wheels]}"
    wheel_path = wheels[0]

    import zipfile

    with zipfile.ZipFile(wheel_path) as zf:
        metadata_names = [n for n in zf.namelist() if n.endswith(".dist-info/METADATA")]
        assert metadata_names, f"no METADATA in {wheel_path.name}"
        metadata = zf.read(metadata_names[0]).decode("utf-8", errors="replace")

    version_lines = [line for line in metadata.splitlines() if line.lower().startswith("version:")]
    assert version_lines, f"no Version: header in {wheel_path.name} METADATA"
    assert len(version_lines) == 1, f"multiple Version headers: {version_lines}"
    declared = version_lines[0].split(":", 1)[1].strip()
    assert (
        declared == EXPECTED_VERSION
    ), f"wheel METADATA Version is {declared!r}, expected {EXPECTED_VERSION!r}"
