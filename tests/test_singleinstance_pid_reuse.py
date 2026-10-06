"""acquire() trusted any python process whose PID sits in carmen.lock, so a
stale lock file whose PID was recycled by an unrelated python process got
that process terminated."""
import subprocess
import sys

import psutil

import singleinstance


def test_unrelated_python_process_is_not_killed(tmp_path, monkeypatch):
    victim = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        lock_dir = tmp_path / "lock"
        monkeypatch.setattr(singleinstance, "LOCK_DIR", str(lock_dir))
        monkeypatch.setattr(singleinstance, "LOCK_PATH", str(lock_dir / "carmen.lock"))
        monkeypatch.setattr(singleinstance, "_QUIT_URL", "http://127.0.0.1:9/internal/quit")  # nothing listens
        lock_dir.mkdir()
        (lock_dir / "carmen.lock").write_text(str(victim.pid))
        singleinstance.acquire()
        assert psutil.Process(victim.pid).is_running() and victim.poll() is None, \
            "acquire() terminated an unrelated python process"
    finally:
        victim.kill()
        victim.wait()


def test_real_main_py_process_is_still_recognised(tmp_path):
    import os
    main_py = os.path.join(os.path.dirname(os.path.abspath(singleinstance.__file__)), "main.py")
    script = tmp_path / "fake.py"
    # a python process whose cmdline carries this repo's main.py path as an argument
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)", main_py])
    try:
        assert singleinstance._is_running_python_process(proc.pid)
    finally:
        proc.kill()
        proc.wait()
