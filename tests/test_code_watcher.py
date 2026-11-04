import threading

import code_watcher


def _loop_once_ready(monkeypatch, fingerprints, imports_ok=True, busy=False, window_open=False, idle=None,
                     cycles=8):
    """Runs the watcher loop against a scripted series of fingerprints; returns how many times it asked to restart."""
    monkeypatch.setattr(code_watcher, "CHECK_SECONDS", 0.01)
    monkeypatch.setattr(code_watcher, "SETTLE_SECONDS", 0)
    series = iter(fingerprints + [fingerprints[-1]] * cycles)
    monkeypatch.setattr(code_watcher, "fingerprint", lambda: next(series))
    checks = []
    monkeypatch.setattr(code_watcher, "new_code_imports", lambda: checks.append(1) or imports_ok)
    stop, ready = threading.Event(), []
    t = threading.Thread(target=code_watcher._loop,
                         args=(stop, lambda: ready.append(1), lambda: busy, lambda: window_open, lambda: idle))
    t.start()
    t.join(1.5)
    stop.set()
    t.join(1)
    return ready, checks


def test_nothing_changed_nothing_happens(monkeypatch):
    ready, checks = _loop_once_ready(monkeypatch, ["a", "a", "a"])
    assert ready == [] and checks == []


def test_changed_code_is_checked_then_restarted_at_a_quiet_moment(monkeypatch):
    ready, checks = _loop_once_ready(monkeypatch, ["a", "b", "b", "b"])
    assert ready == [1] and checks == [1]
    assert code_watcher.restart_was_requested()
    code_watcher._restart_requested.clear()


def test_code_that_does_not_import_never_replaces_the_running_app(monkeypatch):
    ready, checks = _loop_once_ready(monkeypatch, ["a", "b", "b", "b"], imports_ok=False)
    assert ready == [] and checks == [1]                      # looked at once, not retried until it changes


def test_it_waits_while_a_session_is_running(monkeypatch):
    ready, _ = _loop_once_ready(monkeypatch, ["a", "b", "b", "b"], busy=True)
    assert ready == []


def test_an_edit_still_in_progress_waits_until_it_settles(monkeypatch):
    ready, checks = _loop_once_ready(monkeypatch, ["a", "b", "c", "d", "e", "e", "e"])
    assert ready == [1] and checks == [1]                     # one check, of the last version only
    code_watcher._restart_requested.clear()


def test_quiet_moment_rules():
    quiet = code_watcher.safe_to_restart
    assert not quiet(lambda: True, lambda: False, lambda: 999)                # something running
    assert quiet(lambda: False, lambda: False, lambda: 0)                     # window closed: any time
    assert not quiet(lambda: False, lambda: True, lambda: 3)                  # window open and in use
    assert quiet(lambda: False, lambda: True, lambda: code_watcher.IDLE_SECONDS)
    assert not quiet(lambda: False, lambda: True, lambda: None)               # can't tell: do not interrupt


def test_the_real_check_passes_for_this_code_and_fails_for_a_missing_module(monkeypatch):
    assert code_watcher.new_code_imports() is True
    monkeypatch.setattr(code_watcher, "SMOKE_MODULES", ("a_module_that_is_not_there",))
    assert code_watcher.new_code_imports() is False


def test_the_fingerprint_changes_when_a_file_does(tmp_path, monkeypatch):
    f = tmp_path / "a.py"
    f.write_text("x = 1\n")
    monkeypatch.setattr(code_watcher, "REPO_ROOT", str(tmp_path))
    monkeypatch.setattr(code_watcher, "_tracked_sources", lambda: ["a.py"])
    first = code_watcher.fingerprint()
    assert code_watcher.fingerprint() == first
    f.write_text("x = 22\n")
    assert code_watcher.fingerprint() != first
    f.unlink()
    assert code_watcher.fingerprint() not in (first,)


def test_the_window_comes_back_on_the_same_page_after_a_restart(qtbot, isolate_config, monkeypatch):
    import config
    import qt_ui.main_window as mw
    opened = []
    monkeypatch.setattr(mw, "open_main_window", lambda: opened.append(1))
    monkeypatch.setattr(mw, "_win", None)
    config.update_config(lambda cfg: cfg.update(restoreWindow={"open": True, "page": "review"}))
    mw.reopen_after_restart()
    assert opened == [1] and config.load_config()["restoreWindow"] == {}
    opened.clear()
    config.update_config(lambda cfg: cfg.update(restoreWindow={"open": False, "page": "review"}))
    mw.reopen_after_restart()
    assert opened == []                                       # it was in the tray: stays there
