"""A checkout with local commits that origin does not have yet (the developer's
own machine, mid-work) is *ahead* of origin, not behind it. auto_updater must
not treat that as 'an update was pulled' and restart the whole app."""
import subprocess

import pytest

import auto_updater


def _git(cwd, *args):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


@pytest.fixture
def ahead_clone(tmp_path):
    origin = tmp_path / "origin.git"
    clone = tmp_path / "clone"
    _git(tmp_path, "init", "-q", "-b", "main", str(origin), "--bare")
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "a@b.com")
    _git(clone, "config", "user.name", "test")
    (clone / "f.txt").write_text("1\n")
    _git(clone, "add", "f.txt")
    _git(clone, "commit", "-q", "-m", "init")
    _git(clone, "push", "-q", "origin", "main")
    (clone / "f.txt").write_text("2\n")
    _git(clone, "commit", "-q", "-am", "local only, not pushed")
    return clone


def test_local_commits_ahead_of_origin_do_not_trigger_restart(ahead_clone, monkeypatch):
    monkeypatch.setattr(auto_updater, "REPO_ROOT", str(ahead_clone))
    assert auto_updater._check_and_pull() is False
