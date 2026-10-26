"""Every source file must compile without a SyntaxWarning. An invalid backslash escape
in a plain string (a Windows path in a docstring, say) only warns today, but Python is
turning those into errors."""
import os
import subprocess
import warnings

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _tracked_python_files():
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO, capture_output=True, text=True)
    if out.returncode != 0:
        pytest.skip("not a git checkout")
    return [f for f in out.stdout.split() if os.path.exists(os.path.join(REPO, f))]


def test_no_source_file_raises_a_syntax_warning():
    bad = []
    for name in _tracked_python_files():
        with open(os.path.join(REPO, name), "rb") as f:
            source = f.read()
        with warnings.catch_warnings():
            warnings.simplefilter("error", SyntaxWarning)
            try:
                compile(source, name, "exec")
            except SyntaxWarning as exc:
                bad.append(f"{name}: {exc}")
    assert not bad, bad
