import ui_watchdog


def test_trace_lists_threads(tmp_path):
    path = tmp_path / "trace.txt"
    ui_watchdog.write_trace(str(path))
    text = path.read_text()
    assert "window thread stuck" in text and "thread 0x" in text
