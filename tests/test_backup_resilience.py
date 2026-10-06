"""One unreadable file (Windows briefly locks a file another process is
replacing) must not abort the whole daily backup or skip pruning."""
import os
import shutil

import pytest

import backup


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "private"
    data.mkdir()
    for name in ("session_history.json", "tasks.json", "board.json", "config.json"):
        (data / name).write_text(name, encoding="utf-8")
    root = tmp_path / "CarmenBackups"
    root.mkdir()
    old = root / "2000-01-01"
    old.mkdir()
    monkeypatch.setattr(backup, "DATA_DIR", str(data))
    monkeypatch.setattr(backup, "BACKUP_ROOT", str(root))
    return data, root, old


def test_one_locked_file_does_not_abort_the_backup(env, monkeypatch):
    data, root, old = env
    real_copy2 = shutil.copy2

    def flaky_copy2(src, dst, *a, **kw):
        if os.path.basename(src) == "tasks.json":
            raise PermissionError("locked")
        return real_copy2(src, dst, *a, **kw)

    monkeypatch.setattr(backup.shutil, "copy2", flaky_copy2)
    backup.run_backup()  # must not raise

    snapshot = next(p for p in root.iterdir() if p.name != "2000-01-01")
    assert (snapshot / "board.json").exists()
    assert (snapshot / "config.json").exists()
    assert not old.exists()  # pruning still ran


def test_screentime_history_is_backed_up(env):
    data, root, old = env
    (data / "screentime.json").write_text("{}", encoding="utf-8")
    backup.run_backup()

    snapshot = next(p for p in root.iterdir() if p.name != "2000-01-01")
    assert (snapshot / "screentime.json").exists()
