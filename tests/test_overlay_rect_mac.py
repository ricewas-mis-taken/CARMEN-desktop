"""On macOS the Accessibility API reports points, which Qt also uses: the
blackout rect must not be divided by the Retina scale factor."""
import sys
import types

import qt_ui.enforcer_overlay as overlay


def _retina(monkeypatch, dpr):
    screen = types.SimpleNamespace(devicePixelRatio=lambda: dpr)
    monkeypatch.setattr(overlay.QApplication, "primaryScreen", staticmethod(lambda: screen))


def test_mac_rect_passes_through_on_retina(monkeypatch):
    monkeypatch.setattr(overlay, "sys", types.SimpleNamespace(platform="darwin"))
    _retina(monkeypatch, 2.0)
    assert overlay._to_logical_rect((181, 102, 603, 505)) == (181, 102, 603, 505)


def test_windows_rect_is_still_scaled(monkeypatch):
    monkeypatch.setattr(overlay, "sys", types.SimpleNamespace(platform="win32"))
    _retina(monkeypatch, 1.5)
    assert overlay._to_logical_rect((300, 150, 600, 300)) == (200, 100, 400, 200)
