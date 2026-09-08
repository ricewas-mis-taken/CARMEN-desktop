import sys
import types
import plistlib

from mac_os import installed_apps_mac


def _make_bundle(base, name, *, executable=None, bundle_name=None):
    app_dir = base / name
    contents = app_dir / "Contents"
    contents.mkdir(parents=True)
    info = {}
    if executable is not None:
        info["CFBundleExecutable"] = executable
    if bundle_name is not None:
        info["CFBundleName"] = bundle_name
    with open(contents / "Info.plist", "wb") as f:
        plistlib.dump(info, f)
    return app_dir


def test_lists_apps_from_app_dirs_sorted_by_display_name(tmp_path, monkeypatch):
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    _make_bundle(apps_dir, "Zeta.app", executable="Zeta", bundle_name="Zeta App")
    _make_bundle(apps_dir, "Alpha.app", executable="Alpha", bundle_name="Alpha App")

    monkeypatch.setattr(installed_apps_mac, "APP_DIRS", [str(apps_dir)])
    monkeypatch.setattr(installed_apps_mac, "_is_exempt", lambda name: False)

    result = installed_apps_mac.list_installed_apps()

    assert result == [
        {"process_name": "Alpha", "display_name": "Alpha App"},
        {"process_name": "Zeta", "display_name": "Zeta App"},
    ]


def test_skips_bundle_missing_cfbundleexecutable(tmp_path, monkeypatch):
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    _make_bundle(apps_dir, "NoExe.app", bundle_name="No Exe")
    _make_bundle(apps_dir, "Good.app", executable="Good", bundle_name="Good App")

    monkeypatch.setattr(installed_apps_mac, "APP_DIRS", [str(apps_dir)])
    monkeypatch.setattr(installed_apps_mac, "_is_exempt", lambda name: False)

    result = installed_apps_mac.list_installed_apps()

    assert result == [{"process_name": "Good", "display_name": "Good App"}]


def test_missing_cfbundlename_falls_back_to_folder_name_minus_dot_app(tmp_path, monkeypatch):
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    _make_bundle(apps_dir, "MyCoolApp.app", executable="MyCoolApp")

    monkeypatch.setattr(installed_apps_mac, "APP_DIRS", [str(apps_dir)])
    monkeypatch.setattr(installed_apps_mac, "_is_exempt", lambda name: False)

    result = installed_apps_mac.list_installed_apps()

    assert result == [{"process_name": "MyCoolApp", "display_name": "MyCoolApp"}]


def test_exempt_process_names_are_filtered_out(tmp_path, monkeypatch):
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    _make_bundle(apps_dir, "Exempt.app", executable="Exempt", bundle_name="Exempt App")
    _make_bundle(apps_dir, "Normal.app", executable="Normal", bundle_name="Normal App")

    monkeypatch.setattr(installed_apps_mac, "APP_DIRS", [str(apps_dir)])
    monkeypatch.setattr(installed_apps_mac, "_is_exempt", lambda name: name == "Exempt")

    result = installed_apps_mac.list_installed_apps()

    assert result == [{"process_name": "Normal", "display_name": "Normal App"}]


def test_returns_empty_list_when_no_app_dirs_exist(tmp_path, monkeypatch):
    monkeypatch.setattr(
        installed_apps_mac, "APP_DIRS", [str(tmp_path / "nope1"), str(tmp_path / "nope2")]
    )
    monkeypatch.setattr(installed_apps_mac, "_is_exempt", lambda name: False)

    assert installed_apps_mac.list_installed_apps() == []


def test_bad_info_plist_is_skipped_not_fatal(tmp_path, monkeypatch):
    apps_dir = tmp_path / "Applications"
    apps_dir.mkdir()
    bad_app = apps_dir / "Bad.app" / "Contents"
    bad_app.mkdir(parents=True)
    (bad_app / "Info.plist").write_bytes(b"not a real plist")
    _make_bundle(apps_dir, "Good.app", executable="Good", bundle_name="Good App")

    monkeypatch.setattr(installed_apps_mac, "APP_DIRS", [str(apps_dir)])
    monkeypatch.setattr(installed_apps_mac, "_is_exempt", lambda name: False)

    result = installed_apps_mac.list_installed_apps()

    assert result == [{"process_name": "Good", "display_name": "Good App"}]


def test_is_exempt_forwards_process_name_to_session_manager(monkeypatch):
    """_is_exempt() is the one line in this module that calls real repo code
    (session_manager.is_exempt). Never import the real session_manager here
    -- it writes to hardcoded repo paths outside any tmp_path isolation --
    instead inject a fake module via sys.modules and assert the wrapper
    forwards the exact process_name and returns its bool, matching
    session_manager.is_exempt(process_name, pid=None)'s call contract."""
    calls = []
    fake_session_manager = types.SimpleNamespace(
        is_exempt=lambda process_name: calls.append(process_name) or True
    )
    monkeypatch.setitem(sys.modules, "session_manager", fake_session_manager)

    assert installed_apps_mac._is_exempt("Finder") is True
    assert calls == ["Finder"]


def test_is_exempt_returns_false_if_session_manager_call_raises(monkeypatch):
    def _raise(process_name):
        raise RuntimeError("boom")

    fake_session_manager = types.SimpleNamespace(is_exempt=_raise)
    monkeypatch.setitem(sys.modules, "session_manager", fake_session_manager)

    assert installed_apps_mac._is_exempt("Anything") is False
