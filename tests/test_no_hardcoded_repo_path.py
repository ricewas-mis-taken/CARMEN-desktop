"""No test may hardcode an absolute path into the owner's real checkout:
doing so makes a full `pytest` run in any clone import the real repo."""
import pathlib

ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_no_test_file_hardcodes_a_real_user_path():
    offenders = []
    for path in list(ROOT.glob("tests/test_*.py")) + list(ROOT.glob("mac-os/tests/*.py")):
        if path.name == pathlib.Path(__file__).name:
            continue
        text = path.read_text(encoding="utf-8")
        if "Users\Lucas" in text or "Users/Lucas" in text:
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []
