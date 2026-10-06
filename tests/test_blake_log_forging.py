import logging
import api_server


def test_request_path_cannot_forge_log_lines(tmp_path):
    lines = []

    class H(logging.Handler):
        def emit(self, record):
            lines.append(record.getMessage())

    h = H()
    api_server._request_logger.addHandler(h)
    try:
        client = api_server.app.test_client()
        client.get("/health%0A2030-01-01 00:00:00,000 END   POST /session/end -> 200 (0.001s) [thread=1]")
    finally:
        api_server._request_logger.removeHandler(h)
    text = "\n".join(lines)
    print(repr(text[:300]))
    # every log record must stay on a single physical line
    assert all("\n" not in m for m in lines)
