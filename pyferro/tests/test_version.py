"""`ferro.__version__` is the only place the version is written down.

It reaches the window title and every data-file header, so a recorded measurement
can be traced back to the code that produced it. These tests make sure nothing
grows a second copy that could drift out of step.
"""

import re
import subprocess
from pathlib import Path

import pytest

import ferro

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_a_release_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", ferro.__version__), ferro.__version__


def test_pyproject_takes_the_version_from_the_package():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert 'version = { attr = "ferro.__version__" }' in text
    assert not re.search(r'(?m)^version = "', text), "pyproject.toml has a second copy of the version"


def test_pixi_manifest_has_no_version():
    text = (ROOT / "pixi.toml").read_text()
    assert not re.search(r'(?m)^version = "', text), "pixi.toml has a second copy of the version"


def test_build_script_reads_the_package_version():
    text = (ROOT / "packaging" / "build_offline.sh").read_text()
    assert "ferro/__init__.py" in text, "the bundle name must come from ferro/__init__.py"


def test_git_tag_matches_when_the_tree_is_on_a_tag():
    try:
        described = subprocess.run(
            ["git", "-C", str(ROOT), "describe", "--tags", "--exact-match"],
            capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        pytest.skip("git not available")
    if described.returncode != 0:
        pytest.skip("not on an exact tag (normal during development)")
    assert described.stdout.strip() == f"v{ferro.__version__}"


def test_windows_launchers_use_crlf():
    """cmd.exe mis-parses an LF-only .bat, and these are written on macOS."""
    import pathlib

    bats = sorted((pathlib.Path(__file__).parent.parent / "packaging" / "windows").glob("*.bat"))
    assert bats, "no launchers found"
    for bat in bats:
        data = bat.read_bytes()
        assert b"\n" in data
        assert data.replace(b"\r\n", b"").count(b"\n") == 0, f"{bat.name} has bare LF line endings"
