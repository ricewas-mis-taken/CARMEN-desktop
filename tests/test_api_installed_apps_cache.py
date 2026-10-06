"""GET /apps/installed is unauthenticated and every call runs a full Start
Menu walk plus a PowerShell Get-AppxPackage (seconds of CPU). Any web page
can fire cross-origin GETs at 127.0.0.1 (CORS only stops reading the
reply), so uncached this is a CPU-exhaustion lever."""
import threading
import time

import api_server
import installed_apps


def test_repeated_and_concurrent_requests_scan_once(monkeypatch):
    calls = []

    def slow_scan():
        calls.append(1)
        time.sleep(0.2)
        return [{"process_name": "a.exe", "display_name": "A"}]

    monkeypatch.setattr(installed_apps, "list_installed_apps", slow_scan)
    monkeypatch.setattr(api_server, "_installed_apps_cache", {"at": 0.0, "data": None}, raising=False)
    api_server.app.config["TESTING"] = True

    def hit():
        assert api_server.app.test_client().get("/apps/installed").status_code == 200

    threads = [threading.Thread(target=hit) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    for _ in range(5):
        hit()
    assert len(calls) == 1, f"{len(calls)} full scans for 13 requests"
