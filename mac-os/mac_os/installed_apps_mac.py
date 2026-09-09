"""Enumerates installed macOS applications by scanning /Applications and
~/Applications for .app bundles and reading each one's Contents/Info.plist --
the macOS analogue of installed_apps.py's Start Menu .lnk + MSIX scanning
(see that file's module docstring). Much simpler than the Windows version:
there's no shortcut/package-manager indirection to unwrap, just a bundle's
own declared executable name.
"""
import os
import plistlib
import sys

_repo_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

APP_DIRS = ["/Applications", os.path.expanduser("~/Applications")]


def _is_exempt(process_name):
    # Guarded the same way the rest of this module is defensive: a failure
    # to import/call session_manager must never take down the whole scan --
    # worst case, an exempt app just isn't filtered out.
    try:
        import session_manager
        return session_manager.is_exempt(process_name)
    except Exception:
        return False


def _read_bundle(app_path):
    info_plist = os.path.join(app_path, "Contents", "Info.plist")
    try:
        with open(info_plist, "rb") as f:
            info = plistlib.load(f)
    except (FileNotFoundError, plistlib.InvalidFileException, OSError):
        return None

    executable = info.get("CFBundleExecutable")
    if not executable:
        return None

    entry = os.path.basename(app_path)
    display_name = info.get("CFBundleName") or entry[: -len(".app")]
    return {"process_name": executable, "display_name": display_name}


def _iter_app_bundles(base, depth=2):
    """Yields .app bundle paths under base, up to `depth` levels deep
    (default 2: bundles directly in base, plus bundles one folder deeper --
    e.g. /Applications/Utilities/*.app). Real installs don't always put an
    app as a *direct* child of /Applications -- vendor/utility subfolders
    are common -- so a flat os.listdir(base) alone (this module's original
    approach) silently missed those.

    Never descends into a .app bundle itself (it's a bundle to read, not a
    container to search inside) and never follows symlinks (avoids a
    directory-loop risk a bounded, non-recursive-into-bundles walk would
    otherwise have no other guard against)."""
    try:
        entries = os.listdir(base)
    except OSError:
        return
    for entry in entries:
        path = os.path.join(base, entry)
        if entry.endswith(".app"):
            yield path
        elif depth > 1 and not os.path.islink(path) and os.path.isdir(path):
            yield from _iter_app_bundles(path, depth - 1)


def list_installed_apps():
    """Returns one entry per unique process_name:
    [{"process_name": <str>, "display_name": <str>}, ...], sorted by
    display_name.lower(), matching installed_apps.list_installed_apps()'s
    exact shape. Apps session_manager considers always-exempt are left out,
    same as the Windows version. A single bad .app/Info.plist is skipped,
    never aborting the whole scan."""
    apps = {}
    for base in APP_DIRS:
        if not os.path.isdir(base):
            continue

        for app_path in _iter_app_bundles(base):
            try:
                bundle = _read_bundle(app_path)
                if bundle is None:
                    continue

                process_name = bundle["process_name"]
                if _is_exempt(process_name):
                    continue

                key = process_name.lower()
                if key in apps:
                    continue
                apps[key] = bundle
            except Exception:
                continue

    return sorted(apps.values(), key=lambda a: a["display_name"].lower())
