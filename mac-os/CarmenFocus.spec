# PyInstaller spec for the macOS build.
#
# Exists because a bare `pyinstaller --windowed main.py` crashes on launch
# with `ModuleNotFoundError: No module named 'mac_os'` -- the dispatch shims
# (installed_apps.py etc.) add mac-os/ to sys.path at RUNTIME, which
# PyInstaller's static import analysis never sees, so it silently drops the
# whole mac_os package from the bundle (see PORT_SPEC.md's Subsystem 8
# section for the exact repro). pathex + hiddenimports below are the fix,
# confirmed against a real Mac build. Run with:
#   pyinstaller mac-os/CarmenFocus.spec
# from the repo root (paths below are written relative to the repo root for
# that reason, not relative to this file's own directory).

import os

block_cipher = None
repo_root = os.getcwd()

a = Analysis(
    ["main.py"],
    pathex=[repo_root, os.path.join(repo_root, "mac-os")],
    binaries=[],
    datas=[],
    hiddenimports=[
        "mac_os",
        "mac_os.installed_apps_mac",
        "mac_os.enforcer_mac",
        "mac_os.window_tracker_mac",
        "mac_os.autostart_mac",
        "mac_os.calendar_toast_mac",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CarmenFocus",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,   # signing is handled by scripts/sign_and_notarize.sh, not here
    entitlements_file=None,
    icon=None,                # add mac-os/CarmenFocus.icns here once one exists
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="CarmenFocus",
)

app = BUNDLE(
    coll,
    name="CarmenFocus.app",
    icon=None,
    bundle_identifier="dev.lucastang.carmenfocus",
    info_plist={
        "CFBundleShortVersionString": "1.0.0",
        "NSHighResolutionCapable": True,
        # LSUIElement not set: the app has a normal dock icon/window, unlike
        # a pure menu-bar-only utility.
    },
)
