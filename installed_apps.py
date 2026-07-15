"""Enumerates installed Windows applications from two sources:

1. Start Menu .lnk shortcuts (all-users + current-user) — traditional
   installers (Squirrel/Electron, NSIS, MSI, etc).
2. Installed MSIX/Store packages (Get-AppxPackage) — apps like Spotify and
   Claude for Desktop that ship as packages and never create a .lnk shortcut
   at all, so source #1 alone silently misses them.

Both feed the same deduplicated-by-process-name result, which is what the
whitelist picker draws from so you don't have to have an app open to
whitelist it."""
import json
import os
import re
import subprocess

import pythoncom
import win32com.client

import session_manager

START_MENU_DIRS = [
    os.path.join(
        os.environ.get("PROGRAMDATA", r"C:\ProgramData"),
        "Microsoft", "Windows", "Start Menu", "Programs",
    ),
    os.path.join(
        os.environ.get("APPDATA", ""),
        "Microsoft", "Windows", "Start Menu", "Programs",
    ),
]

# Standard Start Menu folder names Windows itself uses for its own bundled
# utilities (Character Map, Disk Cleanup, Steps Recorder, admin snap-ins,
# etc.). Anything filed under one of these is a native OS function, not a
# real distraction, so it's skipped wholesale rather than trying to name
# every individual utility exe.
SYSTEM_UTILITY_FOLDERS = {
    "accessibility",
    "accessories",
    "administrative tools",
    "windows administrative tools",
    "windows tools",
    "windows ease of access",
    "windows powershell",
    "maintenance",
    "system tools",
    "startup",
}

# Squirrel.Windows (used by Discord, Spotify, Slack, and many other
# Electron apps) installs a shortcut whose real Targetpath is a shared
# "Update.exe" stub, with the actual app launched via
# "--processStart <AppName>.exe" in Arguments. Resolving only Targetpath
# means every one of these apps collapses into a single meaningless
# "Update.exe" entry (and the checked name would never match the app's real
# running process anyway) — this pattern recovers the real exe name.
_PROCESS_START_RE = re.compile(r"--processStart(?:Arguments)?\s+\"?([^\s\"]+\.exe)\"?", re.IGNORECASE)

# Generic installer/uninstaller executables (Inno Setup's unins000.exe,
# MSI's msiexec.exe, NSIS/InstallShield's setup.exe/install.exe, etc.). These
# only ever run for a few seconds during an install and are never something
# you'd actually focus-switch to mid-session, so they're filtered out
# regardless of which app's Start Menu folder they're found in.
_GENERIC_INSTALLER_RE = re.compile(
    r"^(setup|install|installer|uninstall|msiexec)\.exe$|^unins\d*\.exe$", re.IGNORECASE
)

# MSIX packages that are pure codec/extension/runtime/system-internal
# plumbing rather than a real launchable app — matched against the
# package's Name (e.g. "Microsoft.AV1VideoExtension"). Everything else that
# declares at least one real .exe is left in as a normal pickable app,
# including plenty of first-party Microsoft ones (Calculator, Xbox App,
# Teams, ...) — those are genuine apps someone might want to whitelist (or
# deliberately leave off), not clutter to hide.
_MSIX_NON_APP_PACKAGE_KEYWORDS = (
    "videoextension", "imageextension", "mediaextensions", "webpimage",
    "languageexperiencepack", "applicationcompatibilityenhancements",
    "audioprocessing", "audiocontrol", "widgetsplatformruntime",
    "winappruntime", "storepurchaseapp", "startexperiencesapp",
    "crossdevice", "sechealthui",
)

# Helper/background-service exe names bundled alongside the real app inside
# an MSIX package's install folder (crash reporters, updaters, native
# messaging hosts, CLI tools, ...) — never the thing you'd actually
# focus-switch to.
_MSIX_HELPER_EXE_KEYWORDS = (
    "migrator", "launcher", "updater", "update", "crashpad", "helper",
    "service", "svc", "uninstall", "cli", "widget", "gamebar", "broker",
    "notification", "background", "startup", "sync", "native-host", "nativehost",
)

_NON_ALNUM_RE = re.compile(r"[^a-z0-9]")


def list_installed_apps():
    """Returns one entry per unique process name, combining Start Menu
    shortcuts and installed MSIX/Store packages:
    [{"process_name": "Code.exe", "display_name": "Visual Studio Code"}, ...],
    sorted by display name. Apps that are always-allowed (session_manager's
    exempt list — Settings, Explorer, Windows Terminal, NVIDIA/Git tools,
    etc.), native Windows utility folders, and generic installer executables
    are left out entirely, since they're never a meaningful whitelist choice."""
    apps = {}
    _scan_start_menu_shortcuts(apps)
    _scan_msix_packages(apps)
    return sorted(apps.values(), key=lambda a: a["display_name"].lower())


def _add_candidate(apps, process_name, display_name):
    if not process_name or not process_name.lower().endswith(".exe"):
        return
    if _GENERIC_INSTALLER_RE.match(process_name):
        return
    key = process_name.lower()
    if key in apps:
        return
    if session_manager.is_exempt(process_name):
        return
    apps[key] = {"process_name": process_name, "display_name": display_name}


def _scan_start_menu_shortcuts(apps):
    # COM apartments are per-thread — this is called from whichever thread
    # the tray spawns for the picker (or Flask's server thread for
    # /apps/installed), neither of which has COM initialized by default.
    # Without this, win32com.client.Dispatch raises "CoInitialize has not
    # been called."
    pythoncom.CoInitialize()
    try:
        shell = win32com.client.Dispatch("WScript.Shell")

        for start_dir in START_MENU_DIRS:
            if not start_dir or not os.path.isdir(start_dir):
                continue
            for root, dirs, files in os.walk(start_dir):
                # Prune known system-utility subfolders so os.walk never
                # descends into them at all.
                dirs[:] = [d for d in dirs if d.lower() not in SYSTEM_UTILITY_FOLDERS]

                if os.path.basename(root).lower() in SYSTEM_UTILITY_FOLDERS:
                    continue

                for filename in files:
                    if not filename.lower().endswith(".lnk"):
                        continue
                    shortcut_path = os.path.join(root, filename)
                    try:
                        shortcut = shell.CreateShortcut(shortcut_path)
                        target = shortcut.Targetpath
                        arguments = shortcut.Arguments
                    except Exception:
                        continue

                    if not target:
                        continue

                    process_name = _resolve_real_process_name(target, arguments)
                    display_name = os.path.splitext(filename)[0]
                    _add_candidate(apps, process_name, display_name)
    finally:
        pythoncom.CoUninitialize()


def _resolve_real_process_name(target, arguments):
    """Returns the exe basename that will actually end up as the foreground
    window's process — unwrapping Squirrel-style updater stubs so apps like
    Discord resolve to their real running exe instead of the shared
    "Update.exe" launcher, which would never match during enforcement."""
    if os.path.basename(target).lower() == "update.exe" and arguments:
        match = _PROCESS_START_RE.search(arguments)
        if match:
            return match.group(1)

    if target.lower().endswith(".exe"):
        return os.path.basename(target)

    return None


def _scan_msix_packages(apps):
    """MSIX/Store-packaged apps (Spotify, Claude for Desktop, and plenty of
    built-in Windows apps) don't create Start Menu .lnk shortcuts at all, so
    _scan_start_menu_shortcuts never sees them. Shells out to PowerShell —
    there's no simple pywin32 binding for AppX package enumeration — to list
    installed packages plus their Start Menu-resolved display names, then
    inspects each package's install folder directly. This deliberately
    doesn't crash or block the whole picker if PowerShell is slow/missing/
    errors; it just contributes nothing in that case."""
    try:
        packages = _get_msix_packages()
    except Exception:
        return

    for package in packages:
        name = package.get("Name") or ""
        if any(keyword in name.lower() for keyword in _MSIX_NON_APP_PACKAGE_KEYWORDS):
            continue

        install_location = package.get("InstallLocation")
        if not install_location or not os.path.isdir(install_location):
            continue

        display_name = package.get("DisplayName") or name
        process_name = _find_best_exe(install_location, display_name)
        _add_candidate(apps, process_name, display_name)


def _get_msix_packages(timeout_seconds=20):
    """Runs one PowerShell call returning [{"Name", "DisplayName",
    "InstallLocation"}, ...] for non-framework, non-system-signed packages —
    DisplayName comes from Get-StartApps, which already resolves the
    manifest's "ms-resource:..." indirection the same way the real Start
    Menu does, instead of this module re-implementing PRI resource lookup."""
    script = (
        "$ErrorActionPreference = 'SilentlyContinue'; "
        "$startApps = Get-StartApps; "
        "$packages = Get-AppxPackage | Where-Object { -not $_.IsFramework -and $_.SignatureKind -ne 'System' }; "
        "$results = foreach ($pkg in $packages) { "
        "  $match = $startApps | Where-Object { $_.AppID -like ($pkg.PackageFamilyName + '!*') } | Select-Object -First 1; "
        "  if ($match) { "
        "    [PSCustomObject]@{ Name = $pkg.Name; DisplayName = $match.Name; InstallLocation = $pkg.InstallLocation } "
        "  } "
        "}; "
        "@($results) | ConvertTo-Json -Compress"
    )

    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True, text=True, timeout=timeout_seconds, creationflags=creationflags,
    )

    if not result.stdout or not result.stdout.strip():
        return []

    data = json.loads(result.stdout)
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    return []


def _find_best_exe(install_location, display_name):
    """Picks the most likely "real app" exe out of everything bundled in an
    MSIX package's install folder. The manifest's declared launch target
    isn't reliable on its own — e.g. Spotify's manifest points at
    "SpotifyMigrator.exe", but the process that actually ends up running day
    to day is "Spotify.exe", found by scanning the folder instead."""
    normalized_display = _normalize(display_name)

    candidates = []
    for root, _dirs, files in os.walk(install_location):
        # MSIX install folders can be large (bundled runtime/resource
        # trees) — cap recursion depth so this stays fast.
        depth = os.path.relpath(root, install_location).count(os.sep)
        if depth > 3:
            continue
        for filename in files:
            if not filename.lower().endswith(".exe"):
                continue
            if any(keyword in filename.lower() for keyword in _MSIX_HELPER_EXE_KEYWORDS):
                continue
            candidates.append(filename)

    if not candidates:
        return None

    exact_matches = [c for c in candidates if _normalize(os.path.splitext(c)[0]) == normalized_display]
    prefix_matches = [c for c in candidates if _normalize(os.path.splitext(c)[0]).startswith(normalized_display)]

    pool = exact_matches or prefix_matches or candidates
    pool.sort(key=len)
    return pool[0]


def _normalize(text):
    return _NON_ALNUM_RE.sub("", text.lower())
