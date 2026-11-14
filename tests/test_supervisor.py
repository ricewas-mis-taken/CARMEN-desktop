import supervisor


class _Fake:
    def __init__(self, codes, launches):
        self._codes, self._launches = codes, launches

    def __call__(self, command, cwd=None, env=None):
        self._launches.append(dict(env))
        code = self._codes.pop(0)
        return type("P", (), {"wait": lambda self_: code})()


def test_a_crash_and_a_relaunch_start_carmen_again_and_a_quit_ends_it(monkeypatch):
    launches = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", _Fake([-1073741819, 75, 0], launches))
    monkeypatch.setattr(supervisor.time, "sleep", lambda s: None)
    monkeypatch.setattr(supervisor, "_note", lambda code: None)
    assert supervisor.run() == 0
    assert len(launches) == 3
    assert all(l["CARMEN_SUPERVISED"] == "1" for l in launches)
    assert "CARMEN_RESTARTED" not in launches[0]
    # after a crash the windows that were open come back too, from the saved state (CARMEN_CRASHED)
    assert launches[1].get("CARMEN_RESTARTED") == "1" and launches[1].get("CARMEN_CRASHED") == "1"
    assert launches[2].get("CARMEN_RESTARTED") == "1" and "CARMEN_CRASHED" not in launches[2]


def test_a_crash_loop_gives_up(monkeypatch):
    launches = []
    monkeypatch.setattr(supervisor.subprocess, "Popen", _Fake([1] * 20, launches))
    monkeypatch.setattr(supervisor.time, "sleep", lambda s: None)
    monkeypatch.setattr(supervisor, "_note", lambda code: None)
    assert supervisor.run() == 1
    assert len(launches) == supervisor.MAX_CRASHES + 1
