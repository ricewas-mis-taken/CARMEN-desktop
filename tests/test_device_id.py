"""Regression test for the unguarded first-run race in device_id.py:
get_device_id() used to check os.path.exists(), and if absent, generate a
new UUID, write it, and cache it, with no lock around that sequence. Two
threads calling it concurrently before the file exists could each generate
a different UUID and race on the final write, leaving at least one thread's
_cached_id permanently out of sync with what actually landed on disk."""
import os
import threading

import device_id


def test_get_device_id_is_race_free_across_two_threads(monkeypatch, tmp_path):
    path = str(tmp_path / "device_id.txt")
    monkeypatch.setattr(device_id, "DEVICE_ID_PATH", path)
    monkeypatch.setattr(device_id, "_cached_id", None)

    entered = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    real_exists = os.path.exists

    def fake_exists(p):
        if p != device_id.DEVICE_ID_PATH:
            return real_exists(p)
        call_count["n"] += 1
        if call_count["n"] == 1:
            # Thread A: stall right after concluding "no device id file
            # yet" (mirroring the real gap between the check and the
            # write), giving thread B a real window to race it.
            entered.set()
            proceed.wait(timeout=2)
        # Always report "missing" here (rather than delegating to the real
        # file state) -- pre-fix, this is what lets BOTH threads
        # independently decide they need to generate a fresh id, which is
        # the actual race being reproduced. Post-fix, thread B never
        # reaches this at all for its own call (see below).
        return False

    monkeypatch.setattr(device_id.os.path, "exists", fake_exists)

    results = {}

    def call_a():
        results["a"] = device_id.get_device_id()

    def call_b():
        results["b"] = device_id.get_device_id()

    t1 = threading.Thread(target=call_a)
    t1.start()
    assert entered.wait(timeout=2), "thread A never reached its stall point"

    t2 = threading.Thread(target=call_b)
    t2.start()
    # Give thread B a moment to reach its own blocking point: post-fix,
    # blocked acquiring device_id._lock (which thread A already holds);
    # pre-fix, racing straight through with no lock to stop it at all.
    t2.join(timeout=0.2)
    proceed.set()

    t1.join(timeout=2)
    t2.join(timeout=2)
    assert not t1.is_alive() and not t2.is_alive(), "threads deadlocked or hung"

    assert results["a"] == results["b"], (
        f"two threads got different device ids from a concurrent first run: {results!r}"
    )
    with open(path, "r", encoding="utf-8") as f:
        on_disk = f.read().strip()
    assert on_disk == results["a"], "the id actually persisted to disk doesn't match what get_device_id() returned"
