"""`ferro.__version__` is the only place the version is written down.

It reaches the window title and every data-file header. These tests make sure
nothing grows a second copy that could drift out of step.
"""

import re
from pathlib import Path

import ferro

ROOT = Path(__file__).resolve().parent.parent


def test_version_is_a_version_number():
    assert re.fullmatch(r"\d+\.\d+\.\d+", ferro.__version__), ferro.__version__


def test_pyproject_takes_the_version_from_the_package():
    text = (ROOT / "pyproject.toml").read_text()
    assert 'dynamic = ["version"]' in text
    assert 'version = { attr = "ferro.__version__" }' in text
    assert not re.search(r'(?m)^version = "', text), "pyproject.toml has a second copy of the version"


def test_pixi_manifest_has_no_version():
    text = (ROOT / "pixi.toml").read_text()
    assert not re.search(r'(?m)^version = "', text), "pixi.toml has a second copy of the version"

