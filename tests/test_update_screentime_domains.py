"""Unit tests for scripts/update_screentime_domains.py's merge logic --
never hits the real network (fetch_ut1_domains is monkeypatched), and never
touches the real screentime_domains.json/screentime_domains_curated.json
(CURATED_PATH/OUTPUT_PATH are redirected to tmp_path)."""
import importlib.util
import json
import os
import sys

MODULE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "scripts", "update_screentime_domains.py",
)
spec = importlib.util.spec_from_file_location("update_screentime_domains", MODULE_PATH)
update_screentime_domains = importlib.util.module_from_spec(spec)
sys.modules["update_screentime_domains"] = update_screentime_domains
spec.loader.exec_module(update_screentime_domains)


def _isolate(tmp_path, monkeypatch, curated):
    curated_path = tmp_path / "curated.json"
    output_path = tmp_path / "output.json"
    curated_path.write_text(json.dumps(curated))
    monkeypatch.setattr(update_screentime_domains, "CURATED_PATH", str(curated_path))
    monkeypatch.setattr(update_screentime_domains, "OUTPUT_PATH", str(output_path))
    return curated_path, output_path


def test_curated_entries_always_win_over_ut1(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch, {"example.com": "Tools"})
    monkeypatch.setattr(
        update_screentime_domains, "UT1_SOURCES", {"Games": ["games"]},
    )
    monkeypatch.setattr(
        update_screentime_domains, "fetch_ut1_domains", lambda name: ["example.com", "some-game.com"],
    )

    merged, added, skipped = update_screentime_domains.build()

    assert merged["example.com"] == "Tools"
    assert merged["some-game.com"] == "Games"
    assert added["Games"] == 1
    assert skipped["Games"] == 1


def test_main_writes_output_and_reports_no_changes_on_rerun(tmp_path, monkeypatch, capsys):
    _isolate(tmp_path, monkeypatch, {"example.com": "Tools"})
    monkeypatch.setattr(update_screentime_domains, "UT1_SOURCES", {"Games": ["games"]})
    monkeypatch.setattr(update_screentime_domains, "fetch_ut1_domains", lambda name: ["some-game.com"])

    update_screentime_domains.main()
    output = json.loads(open(update_screentime_domains.OUTPUT_PATH).read())
    assert output["example.com"] == "Tools"
    assert output["some-game.com"] == "Games"
    assert "_comment" in output

    capsys.readouterr()
    update_screentime_domains.main()
    out = capsys.readouterr().out
    assert "NO_CHANGES" in out


def test_main_reports_removed_and_recategorized_domains(tmp_path, monkeypatch, capsys):
    curated_path, output_path = _isolate(tmp_path, monkeypatch, {"example.com": "Tools"})
    monkeypatch.setattr(update_screentime_domains, "UT1_SOURCES", {"Games": ["games"]})

    monkeypatch.setattr(update_screentime_domains, "fetch_ut1_domains", lambda name: ["old-game.com"])
    update_screentime_domains.main()

    # Simulate the curated seed reclassifying a domain, and UT1 dropping one
    # it used to list.
    curated_path.write_text(json.dumps({"example.com": "Tools", "old-game.com": "Tools"}))
    monkeypatch.setattr(update_screentime_domains, "fetch_ut1_domains", lambda name: [])
    capsys.readouterr()
    update_screentime_domains.main()
    out = capsys.readouterr().out

    assert "Recategorized: 1" in out


def test_fetch_ut1_domains_filters_ips_and_comments(monkeypatch):
    class _FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"# comment\n192.168.1.1\nreal-domain.com\n\nnotadomain\n"

    monkeypatch.setattr(update_screentime_domains.urllib.request, "urlopen", lambda url, timeout=30: _FakeResponse())

    result = update_screentime_domains.fetch_ut1_domains("games")
    assert result == ["real-domain.com"]
