"""Package metadata invariants."""
import pathlib
import re

import py_coreDAQ


def test_version_matches_pyproject():
    root = pathlib.Path(__file__).resolve().parent.parent
    txt = (root / "pyproject.toml").read_text()
    m = re.search(r'^version = "([^"]+)"', txt, re.M)
    assert m and m.group(1) == py_coreDAQ.__version__
