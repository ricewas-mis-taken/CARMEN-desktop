"""Keeps Carmen running. Windows starts this at sign-in instead of main.py; it runs main.py and starts it again if it
dies (a crash in Qt, the C library under the window). A normal quit (exit code 0) ends it too, and the code main.py
uses to ask for a fresh start (75: an update, the Relaunch button) starts it again straight away. After a crash
Carmen starts again with the windows that were open put back (CARMEN_CRASHED tells it to use the saved state).
If Carmen keeps dying (more than 5 times in 10 minutes) it stops trying instead of looping."""
import os
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
RESTART = 75
MAX_CRASHES, WINDOW_SECONDS = 5, 600


def run():
    command = [sys.executable, os.path.join(HERE, "main.py")] + sys.argv[1:]
    env = dict(os.environ, CARMEN_SUPERVISED="1")
    crashes = []
    while True:
        code = subprocess.Popen(command, cwd=HERE, env=env).wait()
        if code == 0:
            return 0                                  # quit on purpose
        if code != RESTART:
            now = time.time()
            crashes = [t for t in crashes if now - t < WINDOW_SECONDS] + [now]
            if len(crashes) > MAX_CRASHES:
                return code                           # a crash loop: give up rather than spin
            _note(code)
            time.sleep(2)
        env.pop("CARMEN_CRASHED", None)
        env["CARMEN_RESTARTED"] = "1"                 # the window comes back where it was, after a crash too
        if code != RESTART:
            env["CARMEN_CRASHED"] = "1"               # from the last saved state (see window_memory.py)


def _note(code):
    try:
        with open(os.path.join(HERE, "private", "crash.log"), "a", buffering=1) as log:
            log.write(time.strftime("--- Carmen exited with code %s, restarted %%Y-%%m-%%d %%H:%%M:%%S ---\n" % code))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(run())
