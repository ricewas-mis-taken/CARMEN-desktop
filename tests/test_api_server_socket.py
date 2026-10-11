"""The API's listening socket must not leak into a restarted process: on Windows
os.execv (used by the auto-updater) hands every inheritable handle to the new
process, and a leaked listener on the API port swallows the browser extension's
requests forever."""
import socket
import subprocess
import sys
import textwrap
import threading
import time

import pytest

import api_server


def _free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _wait_until_listening(port, seconds=5):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as probe:
            probe.settimeout(0.5)
            if probe.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.05)
    return False


@pytest.fixture
def running_server(monkeypatch):
    port = _free_port()
    monkeypatch.setattr(api_server, "API_PORT", port)
    monkeypatch.setattr(api_server, "_server", None)
    thread = threading.Thread(target=api_server.run_server, daemon=True)
    thread.start()
    assert _wait_until_listening(port)
    yield port
    api_server._server.shutdown()
    api_server._server.server_close()


def test_the_listening_socket_is_not_inheritable(running_server):
    assert api_server._server.socket.get_inheritable() is False


def test_the_api_answers(running_server):
    import urllib.request
    with urllib.request.urlopen(f"http://127.0.0.1:{running_server}/health", timeout=5) as response:
        assert response.status == 200


def test_a_restart_does_not_leave_a_second_listener_on_the_port(running_server):
    """The real failure: start a child the way os.execv does on Windows (handles
    inherited) and check nothing in it can keep the port alive after we stop."""
    script = textwrap.dedent("""
        import sys, time
        time.sleep(6)
    """)
    child = subprocess.Popen([sys.executable, "-c", script], close_fds=False)
    try:
        api_server._server.shutdown()
        api_server._server.server_close()
        time.sleep(0.5)
        with socket.socket() as probe:
            probe.settimeout(1)
            assert probe.connect_ex(("127.0.0.1", running_server)) != 0   # port is really free again
    finally:
        child.kill()


def test_binding_waits_for_a_port_that_is_still_being_released(monkeypatch):
    port = _free_port()
    monkeypatch.setattr(api_server, "API_PORT", port)
    monkeypatch.setattr(api_server, "_server", None)
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE if hasattr(socket, "SO_EXCLUSIVEADDRUSE") else socket.SO_REUSEADDR, 1)
    blocker.bind(("127.0.0.1", port))
    blocker.listen(1)
    thread = threading.Thread(target=api_server.run_server, daemon=True)
    thread.start()
    time.sleep(1.0)
    assert api_server._server is None            # still waiting, not crashed
    blocker.close()
    deadline = time.monotonic() + 5
    while api_server._server is None and time.monotonic() < deadline:
        time.sleep(0.1)
    assert api_server._server is not None
    api_server._server.shutdown()
    api_server._server.server_close()
