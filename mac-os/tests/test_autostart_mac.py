import os
import sys
import plistlib

from mac_os import autostart_mac


def test_writes_plist_and_loads_it_when_missing(tmp_path, monkeypatch):
    plist_path = tmp_path / "com.carmenfocus.app.plist"
    monkeypatch.setattr(autostart_mac, "PLIST_PATH", str(plist_path))

    calls = []
    monkeypatch.setattr(
        autostart_mac.subprocess, "run", lambda *a, **k: calls.append((a, k))
    )

    autostart_mac.ensure_autostart_registered()

    assert plist_path.exists()
    with open(plist_path, "rb") as f:
        written = plistlib.load(f)
    assert written["Label"] == "com.carmenfocus.app"
    assert written["RunAtLoad"] is True
    assert written["KeepAlive"] is False
    expected_main_py = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(autostart_mac.__file__)))),
        "main.py",
    )
    assert written["ProgramArguments"] == [sys.executable, expected_main_py]

    assert len(calls) == 1
    assert calls[0][0][0] == ["launchctl", "load", str(plist_path)]


def test_second_call_with_no_changes_does_not_rewrite_or_reload(tmp_path, monkeypatch):
    plist_path = tmp_path / "com.carmenfocus.app.plist"
    monkeypatch.setattr(autostart_mac, "PLIST_PATH", str(plist_path))

    calls = []
    monkeypatch.setattr(
        autostart_mac.subprocess, "run", lambda *a, **k: calls.append((a, k))
    )

    autostart_mac.ensure_autostart_registered()
    assert len(calls) == 1
    mtime_after_first = plist_path.stat().st_mtime_ns

    autostart_mac.ensure_autostart_registered()

    assert len(calls) == 1  # no second launchctl call
    assert plist_path.stat().st_mtime_ns == mtime_after_first  # not rewritten


def test_rewrites_and_reloads_when_plist_is_stale(tmp_path, monkeypatch):
    plist_path = tmp_path / "com.carmenfocus.app.plist"
    monkeypatch.setattr(autostart_mac, "PLIST_PATH", str(plist_path))

    calls = []
    monkeypatch.setattr(
        autostart_mac.subprocess, "run", lambda *a, **k: calls.append((a, k))
    )

    plist_path.parent.mkdir(parents=True, exist_ok=True)
    with open(plist_path, "wb") as f:
        plistlib.dump({"Label": "stale"}, f)

    autostart_mac.ensure_autostart_registered()

    assert len(calls) == 1
    with open(plist_path, "rb") as f:
        written = plistlib.load(f)
    assert written["Label"] == "com.carmenfocus.app"
