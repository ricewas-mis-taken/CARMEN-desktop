import threading, time
import config


def test_concurrent_read_does_not_break_config_write(isolate_config, tmp_path):
    token = config.get_api_token()
    errors, stop = [], threading.Event()

    def reader():  # what every authenticated request does via _require_token
        while not stop.is_set():
            config.get_api_token()

    def writer():
        for i in range(300):
            try:
                config.update_config(lambda c: c.__setitem__("processBlocklist", ["a%d.exe" % i]))
            except Exception as exc:
                errors.append(repr(exc))

    ts = [threading.Thread(target=reader) for _ in range(4)]
    for t in ts: t.start()
    w = threading.Thread(target=writer); w.start(); w.join()
    stop.set()
    for t in ts: t.join()
    print("writer errors:", len(errors), errors[:2])
    print("token preserved:", config.get_api_token() == token)
    assert not errors


def test_transient_read_failure_does_not_rotate_api_token(isolate_config, monkeypatch):
    """Deterministic version: one failed read (what a reader sees while another
    thread's os.replace is in flight on Windows) must not make get_api_token()
    mint a new token over the existing one."""
    token = config.get_api_token()
    real_load = config.json.load
    calls = {"n": 0}

    def flaky(f, *a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PermissionError(13, "Access is denied")
        return real_load(f, *a, **k)

    monkeypatch.setattr(config.json, "load", flaky)
    got = config.get_api_token()
    monkeypatch.setattr(config.json, "load", real_load)
    print("before:", token[:8], "after:", got[:8], "on disk:", config.load_config()["apiToken"][:8])
    assert got == token
    assert config.load_config()["apiToken"] == token
