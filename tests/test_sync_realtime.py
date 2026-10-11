"""The live connection: right messages out, a poke when another computer changes
something, and only held when it can help. Uses a scripted socket -- no network."""
import json
import threading
import time

import pytest

import auth_manager
import multi_device
import sync_live
import sync_realtime as rt

from test_sync_client import fake_logged_in  # noqa: F401  (shared fixture)
from test_sync_live_session import device, server  # noqa: F401  (shared fixtures)

TIMEOUT = "TIMEOUT"


class FakeSocket:
    def __init__(self, script):
        self.script = list(script)
        self.sent = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def send(self, text):
        self.sent.append(json.loads(text))

    def recv(self, timeout=None):
        if not self.script:
            raise OSError("connection closed")
        item = self.script.pop(0)
        if callable(item):
            item = item()
        if item == TIMEOUT:
            raise TimeoutError()
        return item


def _reply(status="ok", reason=None):
    response = {"postgres_changes": [{"id": 1}]} if status == "ok" else {"reason": reason}
    return json.dumps({"topic": rt.CHANNEL_TOPIC, "event": "phx_reply", "ref": "1", "join_ref": "1",
                       "payload": {"status": status, "response": response}})


def _change(table="session_history", device="other-computer", **extra):
    record = {"table_name": table, "sync_id": "x", "device_id": device, **extra}
    return json.dumps({"topic": rt.CHANNEL_TOPIC, "event": "postgres_changes",
                       "payload": {"ids": [1], "data": {"type": "UPDATE", "record": record}}})


@pytest.fixture
def live(monkeypatch, fake_logged_in, tmp_path):
    """A listener wired to a scripted socket, a clock we move by hand, and a log of pokes."""
    clock = {"now": 1000.0}
    pokes = []
    monkeypatch.setattr(rt, "_clock", lambda: clock["now"])
    monkeypatch.setattr(sync_live, "poke", lambda: pokes.append(1))
    monkeypatch.setattr(rt, "_stop", threading.Event())
    monkeypatch.setattr(rt, "_enabled", True)
    monkeypatch.setattr(rt, "_state", {"connected": False})
    monkeypatch.setattr(multi_device, "SEEN_PATH", str(tmp_path / "seen.txt"))
    monkeypatch.setattr(multi_device, "has_other_device", lambda: True)
    monkeypatch.setattr(rt, "_something_running", lambda: False)
    monkeypatch.setattr(rt, "_idle_seconds", lambda: 0.0)
    sockets = []

    def run(script, stop_after=None):
        sock = FakeSocket(script)
        sockets.append(sock)
        monkeypatch.setattr(rt, "_connect", lambda url, **kw: (sock.__setattr__("url", url), sock)[1])
        return sock

    return types_namespace(clock=clock, pokes=pokes, run=run, sockets=sockets)


def types_namespace(**kw):
    import types
    return types.SimpleNamespace(**kw)


def test_it_joins_the_sync_table_for_this_account_only(live):
    sock = live.run([_reply(), TIMEOUT])
    assert rt._session() == "dropped"            # script ran out: the line went away
    join = sock.sent[0]
    assert join["event"] == "phx_join" and join["topic"] == rt.CHANNEL_TOPIC and join["ref"] == "1"
    change = join["payload"]["config"]["postgres_changes"][0]
    assert change == {"event": "*", "schema": "public", "table": "sync_records", "filter": "user_id=eq.user-1"}
    assert join["payload"]["access_token"] == "fake-token"
    assert "vsn=1.0.0" in sock.url and sock.url.startswith("wss://test-project.supabase.co/realtime/v1/websocket")


def test_connecting_catches_up_at_once_and_slows_the_poll(live):
    live.run([_reply(), lambda: (_ for _ in ()).throw(OSError("bye"))])
    rt._session()
    assert live.pokes == [1]                      # catch-up pass right after joining
    assert rt.poll_seconds() == rt.RETRY_SECONDS  # the line dropped again: wanted but down
    rt._state["connected"] = True
    assert rt.poll_seconds() == rt.FALLBACK_SECONDS


def test_a_change_from_another_computer_pokes_and_our_own_echo_does_not(live):
    import device_id
    me = device_id.get_device_id()
    live.run([_reply(), _change(), _change(device=me), _change(table="tasks"), TIMEOUT])
    rt._session()
    assert live.pokes == [1, 1]                   # catch-up + the one change from elsewhere


def test_a_change_it_cannot_read_still_triggers_a_look(live):
    live.run([_reply(), json.dumps({"event": "postgres_changes", "payload": {}}), TIMEOUT])
    rt._session()
    assert len(live.pokes) == 2


def test_a_refused_subscription_is_reported_not_swallowed(live):
    live.run([_reply("error", "Unable to subscribe to changes")])
    with pytest.raises(rt._SetupRefused, match="Unable to subscribe"):
        rt._session()
    assert not rt._state["connected"] and live.pokes == []


def test_it_keeps_the_line_alive_and_renews_the_login(live, monkeypatch):
    monkeypatch.setattr(auth_manager, "refresh_access_token", lambda: True)
    tokens = iter(["fake-token"])
    monkeypatch.setattr(auth_manager, "get_access_token", lambda: next(tokens, "renewed"))
    answer = json.dumps({"topic": "phoenix", "event": "phx_reply", "ref": "2", "payload": {"status": "ok"}})

    def later(seconds):
        # time passes, and the server answers (so the line is not "silent")
        return lambda: (live.clock.__setitem__("now", live.clock["now"] + seconds), answer)[1]

    sock = live.run([_reply(), later(rt.HEARTBEAT_SECONDS + 1), later(rt.TOKEN_REFRESH_SECONDS), TIMEOUT])
    rt._session()
    beat = next(m for m in sock.sent if m["event"] == "heartbeat")
    assert beat["topic"] == "phoenix" and beat["ref"]
    renewal = [m for m in sock.sent if m["event"] == "access_token"]
    assert renewal and renewal[0]["payload"]["access_token"] == "renewed" and renewal[0]["join_ref"] == "1"


def test_a_line_that_goes_silent_is_dropped(live):
    def silence():
        live.clock["now"] += rt.SILENCE_LIMIT_SECONDS + 1
        return TIMEOUT
    live.run([_reply(), silence, TIMEOUT])
    assert rt._session() == "dropped"


def test_it_lets_go_when_the_computer_goes_idle(live, monkeypatch):
    def go_idle():
        monkeypatch.setattr(rt, "_idle_seconds", lambda: rt.IDLE_AFTER_SECONDS + 1)
        return TIMEOUT
    live.run([_reply(), go_idle, TIMEOUT, TIMEOUT])
    assert rt._session() == "idle"


def test_it_does_not_let_go_while_a_session_is_running_even_if_idle(live, monkeypatch):
    monkeypatch.setattr(rt, "_idle_seconds", lambda: rt.IDLE_AFTER_SECONDS * 10)
    monkeypatch.setattr(rt, "_something_running", lambda: True)
    assert rt.wanted()


def test_a_single_computer_never_connects(live, monkeypatch):
    monkeypatch.setattr(multi_device, "has_other_device", lambda: False)
    assert not rt.wanted()
    assert rt.poll_seconds() == rt.FALLBACK_SECONDS     # and no reason to poll fast


def test_idle_with_nothing_running_does_not_connect(live, monkeypatch):
    monkeypatch.setattr(rt, "_idle_seconds", lambda: rt.IDLE_AFTER_SECONDS + 1)
    assert not rt.wanted()
    assert rt.poll_seconds() == rt.FALLBACK_SECONDS


def test_logged_out_or_disabled_never_connects(live, monkeypatch):
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: False)
    assert not rt.wanted()
    monkeypatch.setattr(auth_manager, "is_logged_in", lambda: True)
    rt.disable()
    assert not rt.wanted()
    rt.enable()
    assert rt.wanted()


def test_wanted_but_not_connected_polls_fast_as_before(live):
    assert rt.wanted() and not rt._state["connected"]
    assert rt.poll_seconds() == rt.RETRY_SECONDS


def test_the_loop_backs_off_and_gives_up_quickly_on_a_refusal(live, monkeypatch):
    waits = []

    class Stop:
        calls = 0

        def is_set(self):
            Stop.calls += 1
            return Stop.calls > 6

        def wait(self, seconds):
            waits.append(seconds)

    monkeypatch.setattr(rt, "_stop", Stop())
    outcomes = iter([OSError("no network"), OSError("still no"), rt._SetupRefused("not enabled"), "idle"])

    def fake_session():
        outcome = next(outcomes)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(rt, "_session", fake_session)
    rt._run()
    assert waits[:4] == [2.0, 4.0, rt.SETUP_BACKOFF_SECONDS, 1.0]


# --- who counts as "uses more than one computer" ---

def test_records_from_our_own_computer_do_not_count(tmp_path, monkeypatch):
    import device_id
    monkeypatch.setattr(multi_device, "SEEN_PATH", str(tmp_path / "seen.txt"))
    assert not multi_device.note_records([{"device_id": device_id.get_device_id()}, {"device_id": None}, {}])
    assert not multi_device.has_other_device()


def test_one_record_from_another_computer_is_remembered_and_forgotten_after_a_month(tmp_path, monkeypatch):
    monkeypatch.setattr(multi_device, "SEEN_PATH", str(tmp_path / "seen.txt"))
    assert not multi_device.has_other_device()
    assert multi_device.note_records([{"device_id": "someone-else"}])
    assert multi_device.has_other_device()
    old = time.time() - (multi_device.MAX_AGE_DAYS + 1) * 24 * 3600
    (tmp_path / "seen.txt").write_text(str(old))
    assert not multi_device.has_other_device()


def test_a_sync_pass_that_hears_from_another_computer_remembers_it(device, fake_logged_in, server, tmp_path, monkeypatch):
    monkeypatch.setattr(multi_device, "SEEN_PATH", str(tmp_path / "seen.txt"))
    device("a")
    import session_history
    session_history.append_entry({"startTime": "2026-10-10T09:00:00", "endTime": "2026-10-10T09:30:00",
                                  "source": "manual", "sessionId": "s1"})
    sync_live.tick()
    assert not multi_device.has_other_device()
    device("b")
    monkeypatch.setattr(multi_device, "SEEN_PATH", str(tmp_path / "seen-b.txt"))
    sync_live.tick()
    assert multi_device.has_other_device()


def test_sync_live_waits_as_long_as_the_provider_says(monkeypatch):
    monkeypatch.setattr(sync_live, "_interval_provider", sync_live._interval_provider)   # restored afterwards
    sync_live.set_interval_provider(lambda: 30)
    assert sync_live._interval_provider() == 30


def test_against_a_real_websocket_server_speaking_the_same_protocol(live, monkeypatch):
    """The scripted socket proves our logic; this proves the real websockets
    library behaves the way that logic assumes (timeouts, closing, text frames)."""
    from websockets.sync.server import serve
    seen = []

    def handler(connection):
        join = json.loads(connection.recv(timeout=5))
        seen.append(join)
        connection.send(_reply())
        connection.send(_change())
        time.sleep(0.3)
        connection.close()

    with serve(handler, "127.0.0.1", 0) as server_:
        port = server_.socket.getsockname()[1]
        thread = threading.Thread(target=server_.serve_forever, daemon=True)
        thread.start()
        monkeypatch.setattr(rt, "_url", lambda: f"ws://127.0.0.1:{port}/realtime/v1/websocket?apikey=k&vsn=1.0.0")
        monkeypatch.setattr(rt, "_connect", rt._real_connect)
        monkeypatch.setattr(rt, "_clock", time.monotonic)
        assert rt._session() == "dropped"            # the server hung up
        server_.shutdown()
    assert seen and seen[0]["event"] == "phx_join"
    assert live.pokes == [1, 1]                      # joined (catch-up) + the change from another computer
