"""macOS platform implementation package for Carmen Focus.

Importable as `mac_os` (underscore -- hyphens aren't legal in a Python
package name), even though the container directory on disk is `mac-os`
(hyphen). Each dispatch shim in the repo root (enforcer.py, window_tracker.py,
autostart.py, installed_apps.py, calendar_toast.py) inserts this directory's
parent (`mac-os/`) onto sys.path itself, on first import, before doing
`from mac_os import <module>_mac` -- so this package resolves correctly
whether the process was started via main.py, a standalone script import, or
pytest collecting a test file directly. See mac-os/README.md for the full
rationale.
"""
