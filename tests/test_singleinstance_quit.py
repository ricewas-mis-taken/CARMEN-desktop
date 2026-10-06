"""singleinstance._request_graceful_quit() POSTed /internal/quit with no
X-Carmen-Token, but that route has been token-gated, so the graceful path
always got a 401 and every second launch fell back to a hard
TerminateProcess of the old instance (ghost tray icon)."""
import threading

from werkzeug.serving import make_server

import api_server
import singleinstance


def test_graceful_quit_reaches_the_old_instance(isolate_config, monkeypatch):
    called = threading.Event()
    api_server.register_quit_callback(called.set)
    server = make_server("127.0.0.1", 0, api_server.app)  # random free port, never 5847
    port = server.server_port
    assert port != 5847
    monkeypatch.setattr(singleinstance, "_QUIT_URL", f"http://127.0.0.1:{port}/internal/quit")
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    try:
        delivered = singleinstance._request_graceful_quit()
        assert called.wait(2), "quit callback never ran (request was rejected)"
        assert delivered is True
    finally:
        server.shutdown()
        api_server.register_quit_callback(None)
