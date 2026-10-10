"""Stress tests for GET /events/wait against a real threaded HTTP server on a
random local port -- many clients, held connections, dropped clients, a bump
storm. Everything is isolated to temp paths; nothing here touches the real app.
"""
import http.client
import json
import socket
import threading
import time

import pytest
from werkzeug.serving import make_server

import api_server
import state_events


@pytest.fixture
def server(isolate_state, isolate_review_db, monkeypatch):
    monkeypatch.setattr(state_events, "_version", 0)
    monkeypatch.setattr(state_events, "_waiters", [])
    srv = make_server("127.0.0.1", 0, api_server.app, threaded=True)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    yield srv.server_port
    srv.shutdown()


def _get(port, path, timeout=15):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())
    finally:
        conn.close()


def _run_clients(n, target):
    results = [None] * n

    def work(i):
        results[i] = target(i)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    return threads, results


def test_forty_clients_only_the_newest_max_stay_waiting(server):
    """More clients than slots: the extras do not pile up threads. Each newcomer
    evicts the oldest waiter (which gets an immediate 'no change' and, in the
    extension, backs off), so exactly MAX_WAITERS are still parked when the
    bump lands and all of those wake at once."""
    def client(i):
        time.sleep(i * 0.01)               # arrive in order
        start = time.time()
        status, body = _get(server, "/events/wait?since=0&timeout=5")
        return status, body, time.time() - start

    threads, results = _run_clients(40, client)
    time.sleep(0.9)
    assert len(state_events._waiters) <= state_events.MAX_WAITERS
    bump_at = time.time()
    state_events.bump()
    for t in threads:
        t.join(10)
    assert all(r is not None for r in results)
    woken = [r for r in results if r[1]["changed"]]
    evicted = [r for r in results if not r[1]["changed"]]
    assert len(woken) == state_events.MAX_WAITERS
    assert len(evicted) == 40 - state_events.MAX_WAITERS
    assert all(r[2] < 2.0 for r in woken), "woken waiters took too long after the bump"
    assert time.time() - bump_at < 3
    assert state_events._waiters == []


def test_status_stays_fast_while_every_slot_is_held(server):
    holders, _ = _run_clients(
        state_events.MAX_WAITERS, lambda i: _get(server, "/events/wait?since=0&timeout=4")
    )
    time.sleep(0.4)
    latencies = []

    def hit(i):
        start = time.time()
        status, _ = _get(server, "/status")
        latencies.append((status, time.time() - start))

    threads, _ = _run_clients(150, hit)
    for t in threads:
        t.join(15)
    assert len(latencies) == 150 and all(s == 200 for s, _ in latencies)
    assert max(l for _, l in latencies) < 2.0
    state_events.bump()
    for t in holders:
        t.join(10)


def test_dropped_clients_do_not_lock_out_a_new_one(server):
    """A browser extension that restarts leaves its old request open on the
    server until the timeout. Fill every slot with sockets that connect, send
    the request and vanish -- a fresh client must still get a real wait (it
    evicts a dead one) and be woken by the next change."""
    dead = []
    for _ in range(state_events.MAX_WAITERS):
        s = socket.create_connection(("127.0.0.1", server), timeout=5)
        s.sendall(b"GET /events/wait?since=0&timeout=5 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        dead.append(s)
    time.sleep(0.4)
    for s in dead:
        s.close()
    result = {}

    def fresh():
        start = time.time()
        result["r"] = _get(server, "/events/wait?since=0&timeout=5")
        result["t"] = time.time() - start

    t = threading.Thread(target=fresh)
    t.start()
    time.sleep(0.4)
    assert "r" not in result, "the new client was turned away instead of waiting"
    state_events.bump()
    t.join(10)
    assert result["r"][1]["changed"] is True and result["t"] < 3


def test_bump_storm_wakes_everyone_and_leaks_nothing(server):
    stop = threading.Event()
    seen = [0] * 6

    def loop(i):
        since = None
        while not stop.is_set():
            _, body = _get(server, "/events/wait" + (f"?since={since}&timeout=1" if since is not None else "?timeout=1"))
            since = body["version"]
            seen[i] += 1 if body["changed"] else 0

    threads, _ = _run_clients(6, loop)
    time.sleep(0.3)

    def storm(_):
        for _ in range(500):
            state_events.bump()

    bumpers, _ = _run_clients(8, storm)
    for b in bumpers:
        b.join(10)
    time.sleep(0.5)
    stop.set()
    state_events.bump()
    for t in threads:
        t.join(10)
    assert state_events.current() >= 4000
    assert sum(seen) > 0
    time.sleep(0.3)
    assert state_events._waiters == []
