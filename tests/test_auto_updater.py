"""auto_updater.py checks a git remote once a minute and self-restarts the
app when it's fallen behind -- these tests build real throwaway git repos
(a bare "origin" plus a clone) rather than mocking subprocess, since the
whole point is the actual fetch/rev-parse/status/pull sequence behaving
correctly together."""
import subprocess

import pytest

import auto_updater


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def repo_pair(tmp_path):
    """A bare 'origin' repo plus a clone (the one auto_updater operates on),
    both on 'main' with one initial commit already pushed."""
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "-q", "-b", "main", str(origin), "--bare")
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "a@b.com")
    _git(clone, "config", "user.name", "test")
    (clone / "f.txt").write_text("hello\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "init")
    _git(clone, "push", "-q", "origin", "main")
    return origin, clone


def _push_new_commit(origin, tmp_path, text):
    pusher = tmp_path / "pusher"
    _git(tmp_path, "clone", "-q", str(origin), str(pusher))
    _git(pusher, "config", "user.email", "a@b.com")
    _git(pusher, "config", "user.name", "test")
    (pusher / "f.txt").write_text(text)
    _git(pusher, "add", "f.txt")
    _git(pusher, "commit", "-q", "-m", "update")
    _git(pusher, "push", "-q", "origin", "main")


def test_check_and_pull_is_noop_when_already_up_to_date(repo_pair, monkeypatch):
    _origin, clone = repo_pair
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(clone))

    assert auto_updater._check_and_pull() is False


def test_check_and_pull_fast_forwards_a_clean_tree(repo_pair, tmp_path, monkeypatch):
    origin, clone = repo_pair
    _push_new_commit(origin, tmp_path, "hello\nworld\n")
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(clone))

    before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True
    ).stdout.strip()

    assert auto_updater._check_and_pull() is True

    after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=clone, capture_output=True, text=True
    ).stdout.strip()
    assert after != before
    assert (clone / "f.txt").read_text() == "hello\nworld\n"


def test_check_and_pull_skips_a_dirty_tree_without_touching_it(repo_pair, tmp_path, monkeypatch):
    origin, clone = repo_pair
    _push_new_commit(origin, tmp_path, "hello\nworld\n")
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(clone))

    (clone / "f.txt").write_text("hello\nlocal edit\n")

    assert auto_updater._check_and_pull() is False
    assert (clone / "f.txt").read_text() == "hello\nlocal edit\n"


def test_an_untracked_file_does_not_block_an_update(repo_pair, tmp_path, monkeypatch):
    origin, clone = repo_pair
    _push_new_commit(origin, tmp_path, "hello\nworld\n")
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(clone))

    (clone / "some-private.key").write_text("not part of the repo")

    assert auto_updater._check_and_pull() is True
    assert (clone / "f.txt").read_text() == "hello\nworld\n"
    assert (clone / "some-private.key").exists()


def test_loop_sets_restart_flag_and_calls_callback_once_then_stops(repo_pair, tmp_path, monkeypatch):
    import threading

    origin, clone = repo_pair
    _push_new_commit(origin, tmp_path, "hello\nworld\n")
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(clone))
    monkeypatch.setattr(auto_updater, "CHECK_INTERVAL_SECONDS", 0.05)
    auto_updater._restart_requested.clear()

    calls = []
    stop_event = threading.Event()
    thread = auto_updater.start(stop_event, lambda: calls.append(1))
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert calls == [1]
    assert auto_updater.restart_was_requested() is True


def test_repeated_fetch_failure_is_logged_once(repo_pair, caplog, monkeypatch):
    """A branch that no longer exists on the remote fails the same way every
    minute; that used to log an identical warning each time, forever."""
    origin, clone = repo_pair
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(clone))
    _git(clone, "checkout", "-q", "-b", "gone-branch")
    auto_updater._last_failure = None
    with caplog.at_level("WARNING", logger="carmen_calendar"):
        for _ in range(3):
            assert auto_updater._check_and_pull() is False
    warnings = [r for r in caplog.records if "fetch failed" in r.getMessage()]
    assert len(warnings) == 1


def test_a_changed_failure_is_logged_again(repo_pair, caplog):
    origin, clone = repo_pair
    auto_updater._last_failure = None
    with caplog.at_level("WARNING", logger="carmen_calendar"):
        auto_updater._log_failure_once("git fetch failed: a")
        auto_updater._log_failure_once("git fetch failed: a")
        auto_updater._log_failure_once("git fetch failed: b")
    assert len([r for r in caplog.records if "fetch failed" in r.getMessage()]) == 2
