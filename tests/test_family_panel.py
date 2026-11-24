"""Options -> Linked guides, rendered offscreen: it must always say who is linked and what they see, and ending a link
is two steps."""
import pytest
from PySide6.QtWidgets import QApplication, QPushButton

import family_host
import family_store
import family_sync
import session_manager
from qt_ui import family_panel
import qt_ui.enforcer_overlay as overlay
from tests_family.fakes import DictSettings, FakeHost, FakeTransport, link, start_cmd


def _app():
    return QApplication.instance() or QApplication([])


def _sync(tmp_path, signed_in=True, **kw):
    t, h = FakeTransport(), FakeHost()
    t.links = [link(name="Ms. Okafor", guide="u_okafor", relation="instructor", scopes=["status", "sessions", "score"])] if signed_in else []
    s = DictSettings(familyEnabled=signed_in)
    sync = family_sync.FamilySync(t, family_store.FamilyStore(str(tmp_path / "f.json")), h, s)
    if signed_in:
        sync.tick(force=True)
    return sync, t, h


def _sync_runner(work, done):
    done(work())


def texts(widget):
    from PySide6.QtWidgets import QLabel
    return [l.text() for l in widget.findChildren(QLabel)]


def test_signed_out_shows_the_form_and_the_promise(tmp_path):
    _app()
    sync, t, h = _sync(tmp_path, signed_in=False)
    p = family_panel.FamilyPanel(sync, runner=_sync_runner, auto_refresh=False)
    joined = " ".join(texts(p))
    assert "No one is linked to you. Nothing is shared." in joined and "Sign in" in [b.text() for b in p.findChildren(QPushButton)]
    p.close()


def test_sign_in_through_the_panel(tmp_path):
    _app()
    sync, t, h = _sync(tmp_path, signed_in=False)
    p = family_panel.FamilyPanel(sync, runner=_sync_runner, auto_refresh=False)
    p._email.setText("a@b.c")
    p._password.setText("wrong")
    p._sign_in()
    assert "Incorrect email or password." in " ".join(texts(p))
    p._password.setText("pw")
    p._sign_in()
    assert sync.enabled()
    p.close()


def test_lists_guides_with_what_they_see_and_can_do(tmp_path):
    _app()
    sync, t, h = _sync(tmp_path)
    p = family_panel.FamilyPanel(sync, runner=_sync_runner, auto_refresh=False)
    joined = " ".join(texts(p))
    assert "Ms. Okafor" in joined and "They can see" in joined and "Carmen Score" in joined and "never see your screen" in joined
    p.close()


def test_ending_a_link_takes_two_steps(tmp_path):
    _app()
    sync, t, h = _sync(tmp_path)
    p = family_panel.FamilyPanel(sync, runner=_sync_runner, auto_refresh=False)
    p.findChild(QPushButton, "endLink").click()
    assert t.links[0]["status"] == "active"                       # one click only asks
    assert "End the link with Ms. Okafor?" in " ".join(texts(p))
    p.findChild(QPushButton, "confirmEndLink").click()
    assert t.links[0]["status"] == "revoked"
    assert "No one is linked to you" in " ".join(texts(p))
    p.close()


def test_keep_it_cancels(tmp_path):
    _app()
    sync, t, h = _sync(tmp_path)
    p = family_panel.FamilyPanel(sync, runner=_sync_runner, auto_refresh=False)
    p.findChild(QPushButton, "endLink").click()
    [b for b in p.findChildren(QPushButton) if b.text() == "Keep it"][0].click()
    assert t.links[0]["status"] == "active" and p.findChild(QPushButton, "endLink") is not None
    p.close()


def test_offline_is_said_plainly(tmp_path):
    _app()
    sync, t, h = _sync(tmp_path)
    sync.store.set(lastOkMs=1)
    p = family_panel.FamilyPanel(sync, runner=_sync_runner, auto_refresh=False)
    assert "Offline" in texts(p) or any("Offline" in x for x in texts(p))
    p.close()


def test_lock_overlay_says_who_set_the_rules(isolate_state):
    _app()
    session_manager.start_session(30, "hard", [], ["a.com"], source="guide", guide={"kind": "command", "name": "Ms. Okafor", "id": "u"})
    win = overlay._LockOverlay("Blocked", 3000)
    assert "Set by Ms. Okafor" in texts(win)
    win.close()
    session_manager.end_session()
    win2 = overlay._LockOverlay("Blocked", 3000)
    assert not any(t.startswith("Set by") for t in texts(win2))
    win2.close()


def test_options_dialog_has_the_linked_guides_section_off_by_default(isolate_config):
    _app()
    from qt_ui.options_dialog import OptionsDialog
    d = OptionsDialog()
    assert "Off. Sign in to link" in " ".join(texts(d)) and "Linked guides..." in [b.text() for b in d.findChildren(QPushButton)]
    d.close()
