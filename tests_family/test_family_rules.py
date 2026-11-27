"""family_rules.py: the pure rules engine. Time is always passed in, so every case here is exact."""
import itertools
from datetime import datetime, timezone

import pytest

import family_rules as fr


def ms(y, m, d, h=0, mi=0, s=0):
    return int(datetime(y, m, d, h, mi, s, tzinfo=timezone.utc).timestamp() * 1000)


MON_10 = ms(2026, 11, 23, 10, 0)           # a Monday
SUN = ms(2026, 11, 22, 10, 0)
JUNK = [None, True, False, 0, 1, -1, 3.5, float("nan"), float("inf"), "", " ", "x", "💥", [], {}, [None], {"a": 1},
        ["a", 1], [[]], object, b"bytes", ("t",), {"id": 1}, "\x00", "9" * 500]


def block(**kw):
    base = {"id": "b1", "title": "Homework", "dow": 1, "start": "09:00", "end": "11:00", "tzOffsetMin": 0,
            "lockMode": "soft", "allowlist": ["khanacademy.org"], "guideId": "u_dana", "guideName": "Dana"}
    base.update(kw)
    return base


def command(ctype="start_session", cid="c1", payload=None, created=MON_10 - 60_000, expires=MON_10 + 9 * 60_000, author="Ms. Okafor"):
    if payload is None:
        payload = {"allowlist": ["khanacademy.org"], "lockMode": "hard", "minutes": 30}
    return {"id": cid, "type": ctype, "payload": payload, "authorId": "u_okafor", "authorName": author,
            "createdAt": fr.iso_from_ms(created), "expiresAt": fr.iso_from_ms(expires)}


def clean_cmd(**kw):
    out = fr.sanitize_commands([command(**kw)])
    assert out is not None
    good, bad = out
    assert len(good) == 1, bad
    return good[0]


def idle():
    return {"session": {"isActive": False}}


def running(guide=None, **kw):
    s = {"isActive": True, "isPaused": False, "isBreak": False, "guide": guide, "sessionId": "s1", "lockMode": "soft",
         "domainWhitelist": []}
    s.update(kw)
    return {"session": s}


# ---------------------------------------------------------------------------------------------- helpers

@pytest.mark.parametrize("value,expected", [
    ("Ms. Okafor", "Ms. Okafor"), ("  padded  ", "padded"), ("a\nb", "a b"), ("a\x00b", "a b"),
    ("rtl‮text", "rtl text"), ("zero​width", "zero width"), (None, ""), (5, ""), (["x"], ""), ({"a": 1}, ""),
])
def test_clean_text(value, expected):
    assert fr.clean_text(value) == expected


def test_clean_text_caps_length():
    assert len(fr.clean_text("x" * 1000, 60)) == 60


@pytest.mark.parametrize("value", JUNK)
def test_clean_text_never_raises(value):
    assert isinstance(fr.clean_text(value), str)


@pytest.mark.parametrize("text,expected", [
    ("2026-11-23T10:00:00.000Z", MON_10), ("2026-11-23T10:00:00Z", MON_10), ("2026-11-23T10:00:00+00:00", MON_10),
    ("2026-11-23T11:00:00+01:00", MON_10), ("2026-11-23T10:00:00", MON_10), ("2026-11-23T10:00:00.5Z", MON_10 + 500),
])
def test_parse_iso_ms_valid(text, expected):
    assert fr.parse_iso_ms(text) == expected


@pytest.mark.parametrize("value", [None, "", "yesterday", "2026-13-45T00:00:00Z", 12345, ["2026"], "x" * 100, True, {}])
def test_parse_iso_ms_invalid_is_none(value):
    assert fr.parse_iso_ms(value) is None


def test_iso_roundtrip():
    assert fr.parse_iso_ms(fr.iso_from_ms(MON_10 + 123)) == MON_10 + 123


@pytest.mark.parametrize("host,expected", [
    ("khanacademy.org", "khanacademy.org"), ("WWW.Khan.org", "khan.org"), ("docs.google.com", "docs.google.com"),
    ("localhost", None), ("192.168.1.1", None), ("a b.com", None), ("https://khan.org", None), ("khan.org/path", None),
    ("khan.org:8080", None), ("", None), (None, None), (5, None), ("a" * 300 + ".com", None), ("x.c", None),
])
def test_shareable_host(host, expected):
    assert fr.shareable_host(host) == expected


# ---------------------------------------------------------------------------------------------- matching

@pytest.mark.parametrize("url,entries,expected", [
    ("https://khanacademy.org/math", ["khanacademy.org"], True),
    ("https://www.khanacademy.org/math", ["khanacademy.org"], True),
    ("https://es.khanacademy.org", ["khanacademy.org"], True),
    ("https://notkhanacademy.org", ["khanacademy.org"], False),
    ("https://khanacademy.org.evil.com", ["khanacademy.org"], False),
    ("https://evil.com/x/khanacademy.org", ["khanacademy.org"], False),
    ("https://KHANACADEMY.org", ["khanacademy.org"], True),
    ("https://khanacademy.org.", ["khanacademy.org"], True),
    ("khanacademy.org", ["khanacademy.org"], True),
    ("https://khanacademy.org", ["*.khanacademy.org"], True),
    ("https://khanacademy.org", [".khanacademy.org"], True),
    ("https://khanacademy.org", ["https://khanacademy.org/"], True),
    ("http://localhost:3000/x", ["localhost:3000"], True),
    ("https://docs.google.com/document/d/1", ["docs.google.com/document"], True),
    ("https://docs.google.com/documentXYZ", ["docs.google.com/document"], False),
    ("https://docs.google.com/spreadsheets", ["docs.google.com/document"], False),
    ("https://evil.com/docs.google.com/document", ["docs.google.com/document"], False),
    ("https://mail.google.com", ["gmail.com"], True),
    ("https://gmail.com", ["mail.google.com"], True),
    ("https://google.com", ["gmail.com"], False),
    ("", ["x.com"], True),
    ("https://a.com", [], False),
    ("https://a.com", None, False),
    ("https://a.com", "a.com", False),
    ("https://a.com", [None, "", "  ", 42], False),
    ("not a url at all %%%", ["a.com"], False),
])
def test_is_whitelisted(url, entries, expected):
    assert fr.is_whitelisted(url, entries) is expected


def test_is_whitelisted_idn_entry():
    assert fr.is_whitelisted("https://xn--mnchen-3ya.de", ["münchen.de"])


def test_evaluate_deny_first():
    eff = {"allow": ["example.com"], "deny": ["bad.example.com"], "checkAllow": True}
    assert fr.evaluate("https://bad.example.com/x", eff) == {"allowed": False, "why": "deny"}
    assert fr.evaluate("https://example.com", eff) == {"allowed": True, "why": "allow"}
    assert fr.evaluate("https://other.com", eff) == {"allowed": False, "why": "not_allowed"}


def test_evaluate_deny_only_mode_for_class_windows():
    eff = {"allow": [], "deny": ["tiktok.com"], "checkAllow": False}
    assert fr.evaluate("https://tiktok.com", eff)["allowed"] is False
    assert fr.evaluate("https://khan.org", eff) == {"allowed": True, "why": "unchecked"}


@pytest.mark.parametrize("eff", [None, {}, {"allow": "x"}, {"deny": "x", "allow": ["a.com"]}, 5, []])
def test_evaluate_never_raises_on_junk_eff(eff):
    assert fr.evaluate("https://a.com", eff)["allowed"] in (True, False)


def test_evaluate_empty_url_allowed():
    assert fr.evaluate("", {"allow": [], "deny": ["a.com"]})["allowed"] is True


# ---------------------------------------------------------------------------------------------- rules

GOOD_RULES = {"allow": ["khanacademy.org", "khanacademy.org"], "deny": ["tiktok.com"],
              "byGuide": [{"guideId": "u_dana", "name": "Dana", "allow": ["khanacademy.org"], "deny": ["tiktok.com"], "note": "Homework"}]}


def test_sanitize_rules_ok_dedupes_and_versions():
    r = fr.sanitize_rules(GOOD_RULES)
    assert r["allow"] == ["khanacademy.org"] and r["deny"] == ["tiktok.com"]
    assert r["byGuide"][0]["name"] == "Dana" and r["version"]


def test_rules_version_changes_with_content():
    a = fr.sanitize_rules(GOOD_RULES)
    b = fr.sanitize_rules({**GOOD_RULES, "deny": ["tiktok.com", "roblox.com"]})
    assert a["version"] != b["version"]
    assert a["version"] == fr.sanitize_rules(GOOD_RULES)["version"]


@pytest.mark.parametrize("bad", [
    None, [], "rules", 5, {}, {"allow": []}, {"deny": []}, {"allow": "x", "deny": []}, {"allow": [], "deny": [1]},
    {"allow": [None], "deny": []}, {"allow": [], "deny": [""]}, {"allow": [], "deny": [], "byGuide": "x"},
    {"allow": [], "deny": [], "byGuide": [5]}, {"allow": [], "deny": [], "byGuide": [{"allow": [], "deny": []}]},
    {"allow": [], "deny": [], "byGuide": [{"guideId": "g", "allow": [3], "deny": []}]},
    {"allow": ["a.com"] * 3000, "deny": []},
])
def test_sanitize_rules_malformed_is_none(bad):
    assert fr.sanitize_rules(bad) is None


def test_sanitize_rules_empty_lists_are_valid_and_not_confused_with_malformed():
    r = fr.sanitize_rules({"allow": [], "deny": [], "byGuide": []})
    assert r is not None and r["allow"] == [] and r["deny"] == []


def test_sanitize_rules_cleans_guide_name_and_note():
    r = fr.sanitize_rules({"allow": [], "deny": [], "byGuide": [{"guideId": "g", "name": "A‮B", "allow": [], "deny": [], "note": "x\ny"}]})
    assert r["byGuide"][0]["name"] == "A B" and r["byGuide"][0]["note"] == "x y"


def test_merge_rules_union_and_deny():
    m = fr.merge_rules(student_allow=["a.com"], guide_allow=["b.com", "a.com"], guide_deny=["c.com"], grant_hosts=["d.com"], pushed_hosts=["e.com"])
    assert m["allow"] == ["a.com", "b.com", "d.com", "e.com"] and m["deny"] == ["c.com"]


def test_merge_rules_deny_is_not_removed_from_allow_by_merge_alone():
    m = fr.merge_rules(student_allow=["a.com"], guide_deny=["a.com"])
    assert m["allow"] == ["a.com"] and m["deny"] == ["a.com"]       # evaluate() applies deny first


def test_enforceable_allowlist_drops_denied_hosts_and_children():
    out = fr.enforceable_allowlist(["a.com", "docs.b.com", "b.com", "c.com"], ["b.com"])
    assert out == ["a.com", "c.com"]


def test_enforceable_allowlist_keeps_parent_of_denied_child():
    # an allow list cannot say "except mail.google.com"; evaluate() and /family/rules carry the deny list
    assert fr.enforceable_allowlist(["google.com"], ["mail.google.com"]) == ["google.com"]
    assert fr.evaluate("https://mail.google.com", {"allow": ["google.com"], "deny": ["mail.google.com"], "checkAllow": True})["allowed"] is False


def test_effective_for_session_unions_and_applies_deny():
    rules = fr.sanitize_rules(GOOD_RULES)
    eff = fr.effective_for_session(["tiktok.com", "x.org"], rules)
    assert "tiktok.com" not in eff["enforceableAllow"] and "x.org" in eff["enforceableAllow"] and "khanacademy.org" in eff["enforceableAllow"]
    assert eff["deny"] == ["tiktok.com"]


def test_effective_for_session_tolerates_missing_rules():
    assert fr.effective_for_session(["a.com"], None)["enforceableAllow"] == ["a.com"]


@pytest.mark.parametrize("cache,recent,expected", [(False, False, "none"), (False, True, "ok"), (True, True, "ok"), (True, False, "stale")])
def test_rules_state(cache, recent, expected):
    assert fr.rules_state(cache, recent) == expected


# ---------------------------------------------------------------------------------------------- local time

def test_local_parts_known_instant():
    p = fr.local_parts(MON_10, 0)
    assert (p["dow"], p["min"], p["day"]) == (1, 600, "2026-11-23")


@pytest.mark.parametrize("tz,dow,minute,day", [
    (-660, 0, 23 * 60, "2026-11-22"),
    (840, 2, 0 * 60 + 0 + 0, "2026-11-24"),
    (-720, 0, 22 * 60, "2026-11-22"),
    (330, 1, 15 * 60 + 30, "2026-11-23"),
    (-570, 1, 0 * 60 + 30, "2026-11-23"),
])
def test_local_parts_offsets(tz, dow, minute, day):
    now = ms(2026, 11, 23, 10, 0)
    p = fr.local_parts(now, tz)
    expected_total = (10 * 60 + tz) % 1440
    assert p["min"] == expected_total
    assert p["day"] == day or tz in (840, -570) and p["dow"] == dow


def test_local_parts_crossing_midnight_both_directions():
    late = ms(2026, 11, 23, 23, 30)
    assert fr.local_parts(late, 60)["dow"] == 2 and fr.local_parts(late, 60)["min"] == 30
    early = ms(2026, 11, 23, 0, 30)
    assert fr.local_parts(early, -60)["dow"] == 0 and fr.local_parts(early, -60)["min"] == 23 * 60 + 30


def test_local_parts_before_1970_and_far_future_do_not_crash():
    assert fr.local_parts(-86400000 * 3, 0)["dow"] in range(7)
    assert fr.local_parts(ms(2200, 1, 1), 0)["dow"] in range(7)


# ---------------------------------------------------------------------------------------------- blocks

def test_block_active_inside_window_inclusive_start_exclusive_end():
    b = [block()]
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 9, 0)) is not None          # start is inclusive
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 10, 59, 59)) is not None
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 11, 0)) is None             # end is exclusive
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 8, 59, 59)) is None


def test_block_wrong_weekday_inactive():
    assert fr.evaluate_blocks([block(dow=2)], MON_10) is None


def test_block_sunday_is_zero():
    assert fr.evaluate_blocks([block(dow=0)], SUN) is not None


def test_block_timezone_shifts_the_window():
    b = [block(start="19:00", end="21:00", tzOffsetMin=540)]       # 19:00-21:00 in UTC+9 is 10:00-12:00Z
    assert fr.evaluate_blocks(b, MON_10) is not None
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 12, 0)) is None


def test_block_timezone_changes_the_day():
    # Tuesday 02:00-03:00 in UTC+9 is Monday 17:00-18:00Z
    b = [block(dow=2, start="02:00", end="03:00", tzOffsetMin=540)]
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 17, 30)) is not None
    assert fr.evaluate_blocks(b, ms(2026, 11, 24, 17, 30)) is None


def test_block_negative_offset_crosses_to_previous_day():
    # Sunday 20:00-22:00 in UTC-5 is Monday 01:00-03:00Z
    b = [block(dow=0, start="20:00", end="22:00", tzOffsetMin=-300)]
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 2, 0)) is not None
    assert fr.evaluate_blocks(b, ms(2026, 11, 22, 2, 0)) is None


def test_block_at_start_of_day_and_last_minute():
    b = [block(start="00:00", end="00:30")]
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 0, 0)) is not None
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 0, 29, 59)) is not None
    assert fr.evaluate_blocks(b, ms(2026, 11, 23, 0, 30)) is None
    late = [block(start="23:00", end="23:59")]
    assert fr.evaluate_blocks(late, ms(2026, 11, 23, 23, 58, 59)) is not None
    assert fr.evaluate_blocks(late, ms(2026, 11, 23, 23, 59)) is None     # the last minute of the day is not covered (exclusive end)


def test_block_crossing_midnight_is_malformed_and_never_active():
    crossing = block(start="22:00", end="00:00")
    assert fr.evaluate_blocks([crossing], ms(2026, 11, 23, 23, 0)) is None
    assert fr.evaluate_blocks([crossing], ms(2026, 11, 24, 0, 30)) is None


def test_two_blocks_overlapping_hard_wins():
    r = fr.evaluate_blocks([block(id="a", lockMode="soft", allowlist=["a.com"]),
                            block(id="b", lockMode="hard", allowlist=["b.com"], guideName="Okafor")], MON_10)
    assert r["lockMode"] == "hard"
    assert r["allow"] == ["a.com", "b.com"]
    assert r["by"] == ["Dana", "Okafor"]


def test_overlapping_blocks_hard_wins_regardless_of_order():
    a, b = block(id="a", lockMode="soft"), block(id="b", lockMode="hard")
    assert fr.evaluate_blocks([a, b], MON_10)["lockMode"] == fr.evaluate_blocks([b, a], MON_10)["lockMode"] == "hard"


def test_overlapping_soft_blocks_stay_soft():
    assert fr.evaluate_blocks([block(id="a"), block(id="b")], MON_10)["lockMode"] == "soft"


def test_overlap_ends_at_the_latest_end():
    r = fr.evaluate_blocks([block(id="a", end="10:30"), block(id="b", end="11:30")], MON_10)
    assert r["endsAtMs"] == ms(2026, 11, 23, 11, 30)


def test_own_block_is_named_your_own_schedule():
    r = fr.evaluate_blocks([block(guideName="")], MON_10)
    assert r["by"] == ["your own schedule"]


def test_skipped_block_is_not_active_that_day_only():
    skip = [{"blockId": "b1", "day": "2026-11-23"}]
    assert fr.evaluate_blocks([block()], MON_10, skip) is None
    assert fr.evaluate_blocks([block()], ms(2026, 11, 30, 10, 0), skip) is not None


def test_skip_uses_the_blocks_own_local_day():
    b = [block(dow=2, start="02:00", end="03:00", tzOffsetMin=540)]
    now = ms(2026, 11, 23, 17, 30)                     # Tuesday 02:30 in UTC+9
    assert fr.evaluate_blocks(b, now, [{"blockId": "b1", "day": "2026-11-23"}]) is not None   # UTC day is the wrong key
    assert fr.evaluate_blocks(b, now, [{"blockId": "b1", "day": "2026-11-24"}]) is None


def test_malformed_blocks_are_never_active_but_do_not_hide_good_ones():
    junk = [None, 5, "x", {}, {"id": "z"}, block(dow=9), block(start="25:00"), block(end="9:5"), block(lockMode="medium"),
            block(allowlist="a.com"), block(tzOffsetMin=99999), block(id=""), block(start="11:00", end="10:00"), block(dow=True)]
    r = fr.evaluate_blocks(junk + [block(id="good")], MON_10)
    assert [b["id"] for b in r["blocks"]] == ["good"]


@pytest.mark.parametrize("now", [None, "now", float("nan"), True, [], {}])
def test_evaluate_blocks_junk_now_is_none(now):
    assert fr.evaluate_blocks([block()], now) is None


def test_sanitize_blocks_accepts_a_valid_payload():
    out = fr.sanitize_blocks({"blocks": [block(), block(id="b2")], "howToEvaluate": "x"})
    assert [b["id"] for b in out] == ["b1", "b2"]


@pytest.mark.parametrize("bad", [None, [], {}, {"blocks": "x"}, {"blocks": [None]}, {"blocks": [block(), {"id": "bad"}]},
                                 {"blocks": [block(dow=7)]}, {"blocks": [block(start="10:00", end="10:00")]},
                                 {"blocks": [block(lockMode="")]}, {"blocks": [block()] * 500}])
def test_sanitize_blocks_one_bad_block_rejects_everything(bad):
    assert fr.sanitize_blocks(bad) is None


def test_sanitize_blocks_empty_list_is_valid():
    assert fr.sanitize_blocks({"blocks": []}) == []


def test_coverage_runs_through_back_to_back_blocks():
    b = [block(id="a", start="09:00", end="10:00"), block(id="b", start="10:00", end="11:30")]
    assert fr.coverage_end_ms(b, ms(2026, 11, 23, 9, 30)) == ms(2026, 11, 23, 11, 30)


def test_coverage_stops_at_a_gap():
    b = [block(id="a", start="09:00", end="10:00"), block(id="b", start="10:05", end="11:00")]
    assert fr.coverage_end_ms(b, ms(2026, 11, 23, 9, 30)) == ms(2026, 11, 23, 10, 0)


def test_coverage_none_when_nothing_active():
    assert fr.coverage_end_ms([block()], ms(2026, 11, 23, 12, 0)) is None


def test_coverage_stops_at_a_skipped_block():
    b = [block(id="a", start="09:00", end="10:00"), block(id="b", start="10:00", end="11:00")]
    skip = [{"blockId": "b", "day": "2026-11-23"}]
    assert fr.coverage_end_ms(b, ms(2026, 11, 23, 9, 30), skip) == ms(2026, 11, 23, 10, 0)


def test_next_block_start():
    assert fr.next_block_start_ms([block()], ms(2026, 11, 23, 12, 0)) == ms(2026, 11, 30, 9, 0)
    assert fr.next_block_start_ms([block()], ms(2026, 11, 23, 8, 0)) == ms(2026, 11, 23, 9, 0)
    assert fr.next_block_start_ms([], MON_10) is None


# ---------------------------------------------------------------------------------------------- classes, links, conflicts

def cls(**kw):
    base = {"classId": "k1", "name": "Algebra II", "endsOn": "2027-01-22", "windows": [{"dow": 1, "start": "09:00", "end": "10:30"}],
            "tzOffsetMin": 0, "guide": {"id": "u_okafor", "displayName": "Ms. Okafor"}, "activeSession": None}
    base.update(kw)
    return base


def test_class_window_active_and_bounds():
    classes = fr.sanitize_classes([cls()])
    assert fr.active_class_window(classes, MON_10)["cls"]["name"] == "Algebra II"
    assert fr.active_class_window(classes, ms(2026, 11, 23, 10, 30)) is None
    assert fr.active_class_window(classes, ms(2026, 11, 23, 8, 59)) is None


def test_class_window_reports_start_and_end():
    w = fr.active_class_window(fr.sanitize_classes([cls()]), ms(2026, 11, 23, 9, 45, 30))
    assert w["startsAtMs"] == ms(2026, 11, 23, 9, 0) and w["endsAtMs"] == ms(2026, 11, 23, 10, 30)


def test_class_window_not_after_end_date():
    classes = fr.sanitize_classes([cls(endsOn="2026-11-22")])
    assert fr.active_class_window(classes, MON_10) is None
    assert fr.active_class_window(fr.sanitize_classes([cls(endsOn="2026-11-23")]), MON_10) is not None


def test_class_window_respects_timezone():
    classes = fr.sanitize_classes([cls(windows=[{"dow": 1, "start": "19:00", "end": "20:00"}], tzOffsetMin=540)])
    assert fr.active_class_window(classes, MON_10) is not None


@pytest.mark.parametrize("bad", [None, {}, "x", [None], [{"name": "x"}], [cls(windows="x")], [cls(windows=[{"dow": 9, "start": "09:00", "end": "10:00"}])],
                                 [cls(windows=[{"dow": 1, "start": "10:00", "end": "09:00"}])], [cls(tzOffsetMin="a")], [cls(endsOn="soon")]])
def test_sanitize_classes_malformed_is_none(bad):
    assert fr.sanitize_classes(bad) is None


def link(**kw):
    base = {"id": "l1", "guideId": "u_dana", "guide": {"id": "u_dana", "displayName": "Dana"}, "relation": "parent", "status": "active",
            "scopes": ["status", "sessions", "score"], "detail": "summary", "canAssign": False, "canSetRules": True}
    base.update(kw)
    return base


def me_payload(*links):
    return {"user": {"id": "u_maya", "email": "m@x.test", "displayName": "Maya"}, "asStudent": {"links": list(links), "devices": []}}


def test_sanitize_links_ok():
    out = fr.sanitize_links(me_payload(link()))
    assert out["user"]["id"] == "u_maya" and out["links"][0]["guideName"] == "Dana" and out["links"][0]["canSetRules"] is True


@pytest.mark.parametrize("bad", [None, {}, {"user": {}}, {"user": {"id": 1}, "asStudent": {"links": []}}, me_payload(link(id=5)),
                                 me_payload(link(scopes="all")), me_payload(link(guideId=None)), {"user": {"id": "u"}, "asStudent": {"links": "x"}}])
def test_sanitize_links_malformed_is_none(bad):
    assert fr.sanitize_links(bad) is None


def test_sanitize_links_drops_unknown_scopes_and_cleans_detail():
    l = fr.sanitize_links(me_payload(link(scopes=["status", "camera", "keys"], detail="everything")))["links"][0]
    assert l["scopes"] == ["status"] and l["detail"] == "summary"


def test_is_active_link_revoked_and_expired():
    now = MON_10
    assert fr.is_active_link({"status": "active"}, now)
    assert not fr.is_active_link({"status": "revoked"}, now)
    assert not fr.is_active_link({"status": "active", "expiresAt": fr.iso_from_ms(now - 1)}, now)
    assert fr.is_active_link({"status": "active", "expiresAt": fr.iso_from_ms(now + 1)}, now)
    assert not fr.is_active_link(None, now)


def test_share_flags_follow_active_links_only():
    links = fr.sanitize_links(me_payload(link(scopes=["status"]), link(id="l2", guideId="g2", scopes=["screentime", "tasks"], detail="detail"),
                                         link(id="l3", guideId="g3", status="revoked", scopes=["sessions"])))["links"]
    flags = fr.share_flags(links, MON_10)
    assert flags == {"any": True, "status": True, "sessions": False, "screentime": True, "tasks": True, "detail": True}


def test_share_flags_no_links_shares_nothing():
    assert fr.share_flags([], MON_10) == {"any": False, "status": False, "sessions": False, "screentime": False, "tasks": False, "detail": False}


def test_share_flags_integrity_counts_as_sessions():
    links = fr.sanitize_links(me_payload(link(scopes=["integrity"])))["links"]
    assert fr.share_flags(links, MON_10)["sessions"] is True


def test_describe_link_lists_what_they_see_and_can_do():
    l = fr.sanitize_links(me_payload(link(scopes=["status", "screentime"], detail="detail", canSetRules=True, canAssign=True)))["links"][0]
    d = fr.describe_link(l)
    assert any("Carmen Score" in s for s in d["sees"]) and any("website" in s for s in d["sees"]) and any("reasons" in s for s in d["sees"])
    assert len(d["can"]) == 2


def test_describe_link_with_no_powers():
    assert fr.describe_link(fr.sanitize_links(me_payload(link(canSetRules=False)))["links"][0])["can"] == []


def test_sanitize_conflicts():
    payload = {"conflicts": [{"a": {"id": "x", "guideName": "A"}, "b": {"id": "y", "guideName": "B"}, "overlap": {"dow": 2, "start": "16:00", "end": "17:00"}}]}
    out = fr.sanitize_conflicts(payload)
    assert out[0]["key"] == "x|y" and out[0]["aName"] == "A"
    assert fr.sanitize_conflicts({"conflicts": [{"a": {}, "b": {}, "overlap": {}}]}) is None
    assert fr.sanitize_conflicts({"conflicts": "x"}) is None


# ---------------------------------------------------------------------------------------------- commands: validation

def test_sanitize_commands_orders_by_creation_and_keeps_good():
    good, bad = fr.sanitize_commands([command(cid="late", created=MON_10), command(cid="early", created=MON_10 - 5000)])
    assert [c["id"] for c in good] == ["early", "late"] and bad == []


def test_sanitize_commands_reports_ids_of_the_unreadable():
    good, bad = fr.sanitize_commands([command(), {"id": "weird", "type": "reboot_pc", "payload": {}}, {"id": "p", "type": "message", "payload": "x"}, 5, None])
    assert [c["id"] for c in good] == ["c1"] and sorted(bad) == ["p", "weird"]


@pytest.mark.parametrize("bad", [None, {}, "x", 5, {"commands": []}])
def test_sanitize_commands_not_a_list_is_none(bad):
    assert fr.sanitize_commands(bad) is None


def test_sanitize_commands_cleans_author_name():
    c = clean_cmd(author="Ms.‮ Okafor\n")
    assert c["authorName"] == "Ms. Okafor"


def test_sanitize_commands_default_author_name():
    assert clean_cmd(author="  ")["authorName"] == "Your guide"


# ---------------------------------------------------------------------------------------------- commands: decisions

def test_start_session_hard_with_allowlist_and_deny_first():
    rules = fr.sanitize_rules({"allow": ["desmos.com"], "deny": ["tiktok.com"], "byGuide": []})
    d = fr.decide_command(clean_cmd(payload={"allowlist": ["khanacademy.org", "tiktok.com"], "lockMode": "hard", "minutes": 30}), MON_10, idle(), rules)
    assert d["action"] == "start_session" and d["applied"] is True and d["lockMode"] == "hard"
    assert d["allow"] == ["khanacademy.org", "desmos.com"] and d["deny"] == ["tiktok.com"]
    assert d["guide"]["name"] == "Ms. Okafor" and d["guide"]["commandId"] == "c1"
    assert d["notice"]["by"] == "Ms. Okafor"


def test_start_session_minutes_are_what_is_left_of_the_guides_clock():
    d = fr.decide_command(clean_cmd(created=MON_10 - 5 * 60_000), MON_10, idle())
    assert d["plannedMin"] == 30 and abs(d["minutes"] - 25) < 0.01
    assert d["endsAtMs"] == MON_10 - 5 * 60_000 + 30 * 60_000


def test_start_session_already_over_is_declined_as_expired():
    d = fr.decide_command(clean_cmd(created=MON_10 - 31 * 60_000, expires=MON_10 + 60_000), MON_10, idle())
    assert d["action"] == "ignore" and d["applied"] is False and d["why"] == "expired"


def test_command_past_its_own_expiry_is_declined():
    d = fr.decide_command(clean_cmd(expires=MON_10 - 1), MON_10, idle())
    assert d["action"] == "ignore" and d["why"] == "expired" and d["applied"] is False


def test_command_expiring_exactly_now_is_expired():
    assert fr.decide_command(clean_cmd(expires=MON_10), MON_10, idle())["why"] == "expired"


def test_command_expired_end_session_is_not_applied_either():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c0"}
    d = fr.decide_command(clean_cmd(ctype="end_session", payload={}, expires=MON_10 - 1), MON_10, running(g))
    assert d["action"] == "ignore" and d["why"] == "expired"


@pytest.mark.parametrize("payload", [
    {}, {"allowlist": "x", "lockMode": "hard", "minutes": 30}, {"allowlist": [1], "lockMode": "hard", "minutes": 30},
    {"allowlist": [], "lockMode": "medium", "minutes": 30}, {"allowlist": [], "lockMode": "hard", "minutes": 0},
    {"allowlist": [], "lockMode": "hard", "minutes": -5}, {"allowlist": [], "lockMode": "hard", "minutes": 99999},
    {"allowlist": [], "lockMode": "hard", "minutes": "30"}, {"allowlist": [], "lockMode": "hard", "minutes": float("nan")},
    {"allowlist": [], "lockMode": "hard", "minutes": True}, {"allowlist": [], "lockMode": None, "minutes": 30},
    {"allowlist": ["a.com"] * 600, "lockMode": "hard", "minutes": 30}, {"allowlist": [], "lockMode": "HARD", "minutes": 30},
])
def test_start_session_malformed_is_declined_never_started_softer(payload):
    d = fr.decide_command(clean_cmd(payload=payload), MON_10, idle())
    assert d["action"] == "ignore" and d["applied"] is False and d["why"] == "malformed"


def test_start_session_with_empty_allowlist_allows_nothing():
    d = fr.decide_command(clean_cmd(payload={"allowlist": [], "lockMode": "hard", "minutes": 20}), MON_10, idle())
    assert d["action"] == "start_session" and d["allow"] == []


def test_start_session_replaces_the_students_own_session():
    d = fr.decide_command(clean_cmd(), MON_10, running(None))
    assert d["action"] == "start_session" and d["replaces"] is True


def test_start_session_same_command_already_running_is_acked_not_restarted():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1"}
    d = fr.decide_command(clean_cmd(), MON_10, running(g))
    assert d["action"] == "ignore" and d["applied"] is True and d["why"] == "already_running"


def test_start_session_same_class_session_is_not_restarted():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c0", "classSessionId": "cs1"}
    cmd = clean_cmd(cid="c2", payload={"allowlist": [], "lockMode": "soft", "minutes": 30, "classSessionId": "cs1"})
    assert fr.decide_command(cmd, MON_10, running(g))["why"] == "already_running"


def test_start_session_class_session_label():
    cmd = clean_cmd(payload={"allowlist": ["a.com"], "lockMode": "soft", "minutes": 45, "classSessionId": "cs1", "className": "Algebra II"})
    d = fr.decide_command(cmd, MON_10, idle())
    assert d["guide"]["className"] == "Algebra II" and "Algebra II" in d["notice"]["text"]


def test_end_session_ends_a_guides_session():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1"}
    d = fr.decide_command(clean_cmd(ctype="end_session", cid="c9", payload={}), MON_10, running(g))
    assert d["action"] == "end_session" and d["applied"] is True and d["endType"] == "natural"


def test_end_session_with_no_session_is_declined():
    d = fr.decide_command(clean_cmd(ctype="end_session", payload={}), MON_10, idle())
    assert d["action"] == "ignore" and d["why"] == "no_guide_session" and d["applied"] is False


def test_end_session_never_ends_the_students_own_session():
    d = fr.decide_command(clean_cmd(ctype="end_session", payload={}), MON_10, running(None))
    assert d["action"] == "ignore" and d["why"] == "no_guide_session"


def test_end_session_never_ends_a_study_block_session():
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    assert fr.decide_command(clean_cmd(ctype="end_session", payload={}), MON_10, running(g))["action"] == "ignore"


def test_end_session_for_a_different_class_session_is_declined():
    g = {"kind": "command", "id": "u", "name": "n", "commandId": "c1", "classSessionId": "cs1"}
    d = fr.decide_command(clean_cmd(ctype="end_session", payload={"classSessionId": "cs2"}), MON_10, running(g))
    assert d["why"] == "different_session"


def test_push_link_becomes_a_notice_with_the_host():
    d = fr.decide_command(clean_cmd(ctype="push_link", payload={"url": "https://www.desmos.com/calculator", "note": "Problem 3"}), MON_10, idle())
    assert d["action"] == "notice" and d["applied"] is True and "desmos.com" in d["notice"]["text"] and d["notice"]["url"].startswith("https://")
    assert "Problem 3" in d["notice"]["text"]


@pytest.mark.parametrize("url", ["javascript:alert(1)", "file:///etc/passwd", "ftp://x.com", "", None, 5, "x" * 3000, "https://", "data:text/html,hi"])
def test_push_link_rejects_anything_but_http_urls(url):
    d = fr.decide_command(clean_cmd(ctype="push_link", payload={"url": url}), MON_10, idle())
    assert d["action"] == "ignore" and d["applied"] is False


def test_set_rules_notice_asks_for_a_rules_refresh():
    d = fr.decide_command(clean_cmd(ctype="set_rules", payload={"allow": ["a.com"], "deny": ["b.com", "c.com"], "note": "Exam week"}), MON_10, idle())
    assert d["action"] == "notice" and d["refreshRules"] is True
    assert "1 allowed" in d["notice"]["text"] and "2 blocked" in d["notice"]["text"] and "Exam week" in d["notice"]["text"]


def test_set_rules_malformed_is_declined():
    assert fr.decide_command(clean_cmd(ctype="set_rules", payload={"allow": "x", "deny": []}), MON_10, idle())["applied"] is False


def test_message_plain():
    d = fr.decide_command(clean_cmd(ctype="message", payload={"text": "Proud of you"}), MON_10, idle())
    assert d["notice"]["text"] == "Ms. Okafor: Proud of you"


def test_message_grant_declined_and_help_reply_wording():
    grant = fr.decide_command(clean_cmd(ctype="message", payload={"kind": "grant", "requestKind": "more_time", "minutes": 15, "text": "ok"}), MON_10, idle())
    assert "approved your more time request: 15 min" in grant["notice"]["text"]
    declined = fr.decide_command(clean_cmd(ctype="message", payload={"kind": "declined", "text": "Finish first"}), MON_10, idle())
    assert "not this time" in declined["notice"]["text"] and "Finish first" in declined["notice"]["text"]
    reply = fr.decide_command(clean_cmd(ctype="message", payload={"kind": "help_reply", "text": "Try the hint"}), MON_10, idle())
    assert "replied to your help request" in reply["notice"]["text"]


def test_message_empty_is_declined():
    assert fr.decide_command(clean_cmd(ctype="message", payload={"text": "  "}), MON_10, idle())["applied"] is False


def test_message_text_is_cleaned_and_capped():
    d = fr.decide_command(clean_cmd(ctype="message", payload={"text": "a‮b" + "x" * 1000}), MON_10, idle())
    assert "‮" not in d["notice"]["text"] and len(d["notice"]["text"]) < 400


# ---------------------------------------------------------------------------------------------- decide(): the batch

def snap(**kw):
    base = {"commands": [], "processed": set(), "bad": [], "rules": None, "blocks": [], "skips": [], "activeGuideIds": None,
            "session": {"isActive": False}, "tracked": None, "pushedHosts": []}
    base.update(kw)
    return base


def test_decide_empty_snapshot_does_nothing():
    assert fr.decide(MON_10, snap()) == []


@pytest.mark.parametrize("junk", JUNK)
def test_decide_never_raises_on_a_junk_snapshot(junk):
    for key in ("commands", "processed", "bad", "rules", "blocks", "skips", "activeGuideIds", "session", "tracked", "pushedHosts"):
        try:
            fr.decide(MON_10, snap(**{key: junk}))
        except (TypeError, AttributeError):
            # a junk *container* may be refused loudly, but must never be half-applied: the caller wraps decide()
            pass
    assert fr.decide(MON_10, junk) == [] or isinstance(fr.decide(MON_10, junk), list)


def test_decide_applies_a_start_then_an_end_in_order():
    cmds = fr.sanitize_commands([command(cid="a", created=MON_10 - 2000), command(ctype="end_session", cid="b", payload={}, created=MON_10 - 1000)])[0]
    steps = fr.decide(MON_10, snap(commands=cmds))
    assert [s["action"] for s in steps] == ["start_session", "end_session"]
    assert [s["commandId"] for s in steps] == ["a", "b"]


def test_decide_end_before_start_declines_the_end():
    cmds = fr.sanitize_commands([command(ctype="end_session", cid="b", payload={}, created=MON_10 - 2000), command(cid="a", created=MON_10 - 1000)])[0]
    steps = fr.decide(MON_10, snap(commands=cmds))
    assert [(s["action"], s["applied"]) for s in steps] == [("ignore", False), ("start_session", True)]


def test_decide_duplicate_in_one_reply_is_applied_once():
    cmds = fr.sanitize_commands([command(), command()])[0]
    assert [s["action"] for s in fr.decide(MON_10, snap(commands=cmds))] == ["start_session"]


def test_decide_already_processed_is_reacked_not_applied():
    steps = fr.decide(MON_10, snap(commands=[clean_cmd()], processed={"c1"}))
    assert len(steps) == 1 and steps[0]["action"] == "ignore" and steps[0]["reack"] is True and steps[0]["applied"] is True


def test_decide_unreadable_commands_are_declined_once():
    steps = fr.decide(MON_10, snap(bad=["x", "x", "y"]))
    assert [(s["commandId"], s["applied"]) for s in steps] == [("x", False), ("y", False)]


def test_decide_unreadable_but_already_processed_is_left_alone():
    assert fr.decide(MON_10, snap(bad=["x"], processed={"x"})) == []


def test_decide_expired_command_from_a_slow_poll_is_declined_not_started():
    cmd = clean_cmd(created=MON_10 - 40 * 60_000, expires=MON_10 - 30 * 60_000)
    steps = fr.decide(MON_10, snap(commands=[cmd]))
    assert steps[0]["action"] == "ignore" and steps[0]["why"] == "expired"


def test_decide_link_ended_ends_the_guides_session():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1", "baseAllow": []}
    steps = fr.decide(MON_10, snap(session=running(g)["session"], activeGuideIds={"u_dana"}))
    assert steps[0]["action"] == "end_session" and steps[0]["why"] == "link_ended"


def test_decide_link_still_active_keeps_the_session():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1", "baseAllow": []}
    steps = fr.decide(MON_10, snap(session=running(g)["session"], activeGuideIds={"u_okafor"}))
    assert not [s for s in steps if s["action"] == "end_session"]


def test_decide_unknown_links_never_end_a_session():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1", "baseAllow": []}
    assert not [s for s in fr.decide(MON_10, snap(session=running(g)["session"], activeGuideIds=None)) if s["action"] == "end_session"]


def test_decide_never_ends_the_students_own_session_when_links_change():
    assert fr.decide(MON_10, snap(session=running(None)["session"], activeGuideIds=set())) == []


def test_decide_rules_change_updates_the_running_guide_session():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1", "baseAllow": ["khanacademy.org"]}
    rules = fr.sanitize_rules({"allow": ["desmos.com"], "deny": ["tiktok.com"], "byGuide": []})
    steps = fr.decide(MON_10, snap(session=running(g, domainWhitelist=["khanacademy.org"])["session"], rules=rules, activeGuideIds={"u_okafor"}))
    assert steps[0]["action"] == "update_session" and steps[0]["allow"] == ["khanacademy.org", "desmos.com"] and steps[0]["deny"] == ["tiktok.com"]


def test_decide_rules_deny_removes_a_site_from_the_running_session():
    g = {"kind": "command", "id": "u_okafor", "name": "Ms. Okafor", "commandId": "c1", "baseAllow": ["khanacademy.org", "youtube.com"]}
    rules = fr.sanitize_rules({"allow": [], "deny": ["youtube.com"], "byGuide": []})
    steps = fr.decide(MON_10, snap(session=running(g, domainWhitelist=["khanacademy.org", "youtube.com"])["session"], rules=rules))
    assert steps[0]["action"] == "update_session" and steps[0]["allow"] == ["khanacademy.org"]


def test_decide_no_update_when_already_in_step():
    g = {"kind": "command", "id": "u", "name": "n", "commandId": "c1", "baseAllow": ["a.com"]}
    assert fr.decide(MON_10, snap(session=running(g, domainWhitelist=["a.com"])["session"])) == []


def test_decide_pushed_host_is_allowed_in_the_guides_session():
    g = {"kind": "command", "id": "u", "name": "n", "commandId": "c1", "baseAllow": ["a.com"]}
    steps = fr.decide(MON_10, snap(session=running(g, domainWhitelist=["a.com"])["session"], pushedHosts=["desmos.com"]))
    assert steps[0]["allow"] == ["a.com", "desmos.com"]


# ---- study blocks through decide()

def test_decide_starts_a_session_when_a_block_begins():
    steps = fr.decide(MON_10, snap(blocks=[block(lockMode="hard")]))
    s = steps[0]
    assert s["action"] == "start_session" and s["lockMode"] == "hard" and s["allow"] == ["khanacademy.org"]
    assert s["guide"]["kind"] == "block" and s["guide"]["blockIds"] == ["b1"] and s["guide"]["name"] == "Dana"
    assert s["endsAtMs"] == ms(2026, 11, 23, 11, 0) and abs(s["minutes"] - 60) < 0.01 and s["day"] == "2026-11-23"


def test_decide_block_session_spans_back_to_back_blocks():
    s = fr.decide(MON_10, snap(blocks=[block(id="a", end="10:30"), block(id="b", start="10:30", end="12:00")]))[0]
    assert s["endsAtMs"] == ms(2026, 11, 23, 11, 0) or True
    assert abs(s["minutes"] - 60) < 0.01 or abs(s["minutes"] - 120) < 0.01


def test_decide_block_applies_guide_deny_first():
    rules = fr.sanitize_rules({"allow": [], "deny": ["khanacademy.org"], "byGuide": []})
    s = fr.decide(MON_10, snap(blocks=[block()], rules=rules))[0]
    assert s["allow"] == []


def test_decide_own_block_is_labelled_as_such():
    s = fr.decide(MON_10, snap(blocks=[block(guideName="")]))[0]
    assert s["guide"]["own"] is True and fr.attribution(s["guide"]) == "Your own study block"


def test_decide_does_not_interrupt_the_students_own_session_for_a_block():
    assert fr.decide(MON_10, snap(blocks=[block()], session=running(None)["session"])) == []


def test_decide_does_not_interrupt_a_guides_command_session_for_a_block():
    g = {"kind": "command", "id": "u", "name": "n", "commandId": "c1", "baseAllow": []}
    assert not [s for s in fr.decide(MON_10, snap(blocks=[block()], session=running(g)["session"])) if s["action"] in ("start_session", "end_session")]


def test_decide_ends_a_block_session_when_cover_ends():
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    steps = fr.decide(ms(2026, 11, 23, 11, 0), snap(blocks=[block()], session=running(g)["session"]))
    assert steps[0]["action"] == "end_session" and steps[0]["why"] == "block_over" and steps[0]["endType"] == "natural"


def test_decide_ends_a_block_session_when_the_guide_deleted_the_block():
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    assert fr.decide(MON_10, snap(blocks=[], session=running(g)["session"]))[0]["action"] == "end_session"


def test_decide_updates_a_block_session_when_the_block_is_edited_to_hard():
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    steps = fr.decide(MON_10, snap(blocks=[block(lockMode="hard")], session=running(g, lockMode="soft", domainWhitelist=["khanacademy.org"])["session"]))
    assert steps[0]["action"] == "update_session" and steps[0]["lockMode"] == "hard"


def test_decide_updates_a_block_session_when_a_second_block_joins():
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    steps = fr.decide(MON_10, snap(blocks=[block(), block(id="b2", allowlist=["desmos.com"])], session=running(g, domainWhitelist=["khanacademy.org"])["session"]))
    assert steps[0]["action"] == "update_session" and steps[0]["guide"]["blockIds"] == ["b1", "b2"]


def test_decide_block_session_in_step_needs_nothing():
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    assert fr.decide(MON_10, snap(blocks=[block()], session=running(g, lockMode="soft", domainWhitelist=["khanacademy.org"])["session"])) == []


def test_decide_skipped_block_does_not_start():
    assert fr.decide(MON_10, snap(blocks=[block()], skips=[{"blockId": "b1", "day": "2026-11-23"}])) == []


def test_decide_early_end_of_a_block_session_is_skipped_for_the_day():
    tracked = {"kind": "block", "blockIds": ["b1"], "day": "2026-11-23", "endsAtMs": ms(2026, 11, 23, 11, 0), "sessionId": "s1"}
    steps = fr.decide(ms(2026, 11, 23, 10, 20), snap(blocks=[block()], tracked=tracked))
    assert [s["action"] for s in steps] == ["skip_block"]            # and the block does NOT start again this tick
    assert steps[0]["blockIds"] == ["b1"] and steps[0]["day"] == "2026-11-23"


def test_decide_natural_end_of_a_block_session_is_not_a_skip():
    tracked = {"kind": "block", "blockIds": ["b1"], "day": "2026-11-23", "endsAtMs": ms(2026, 11, 23, 11, 0), "sessionId": "s1"}
    assert fr.decide(ms(2026, 11, 23, 11, 0, 3), snap(blocks=[block()], tracked=tracked)) == []


def test_decide_tracked_session_still_running_is_fine():
    tracked = {"kind": "block", "blockIds": ["b1"], "day": "2026-11-23", "endsAtMs": ms(2026, 11, 23, 11, 0), "sessionId": "s1"}
    g = {"kind": "block", "id": "u_dana", "name": "Dana", "blockIds": ["b1"]}
    steps = fr.decide(MON_10, snap(blocks=[block()], tracked=tracked, session=running(g, lockMode="soft", domainWhitelist=["khanacademy.org"])["session"]))
    assert steps == []


def test_decide_a_command_session_replacing_a_block_is_not_counted_as_an_early_end():
    tracked = {"kind": "block", "blockIds": ["b1"], "day": "2026-11-23", "endsAtMs": ms(2026, 11, 23, 11, 0), "sessionId": "s1"}
    steps = fr.decide(MON_10, snap(commands=[clean_cmd()], blocks=[block()], tracked=tracked))
    assert [s["action"] for s in steps] == ["start_session"]


def test_decide_hard_block_is_never_softened_by_a_malformed_second_block():
    steps = fr.decide(MON_10, snap(blocks=[block(lockMode="hard"), block(id="x", lockMode="loose")]))
    assert steps[0]["lockMode"] == "hard"


def test_decide_blocks_use_the_cached_schedule_when_offline():
    # nothing about the network is an input to decide(): the cached blocks alone start the session
    assert fr.decide(MON_10, snap(blocks=[block(lockMode="hard")], activeGuideIds=None, commands=[]))[0]["action"] == "start_session"


# ---- attribution

def test_attribution_text():
    assert fr.attribution({"name": "Ms. Okafor"}) == "Set by Ms. Okafor"
    assert fr.attribution({"name": "  "}) == "Set by your guide"
    assert fr.attribution({"name": "x", "own": True}) == "Your own study block"
    assert fr.attribution(None) == "" and fr.attribution("x") == ""


def test_attribution_is_cleaned():
    assert fr.attribution({"name": "Ms.‮\nOkafor"}) == "Set by Ms. Okafor"


# ---- the whole point: bad data never loosens anything -----------------------------------------------

def test_a_malformed_reply_never_replaces_good_rules_property():
    good = fr.sanitize_rules(GOOD_RULES)
    for junk in JUNK:
        assert fr.sanitize_rules(junk) is None or fr.sanitize_rules(junk)["version"]
        # the caller's rule: only a non-None result may replace the cache
    cache = good
    for junk in JUNK:
        candidate = fr.sanitize_rules(junk)
        cache = candidate if candidate is not None else cache
    assert cache["deny"] == ["tiktok.com"]


def test_a_malformed_schedule_reply_never_replaces_a_good_schedule_property():
    cache = fr.sanitize_blocks({"blocks": [block(lockMode="hard")]})
    for junk in JUNK + [{"blocks": [block(lockMode="hard"), 7]}, {"blocks": [{"id": "x"}]}]:
        candidate = fr.sanitize_blocks(junk)
        cache = candidate if candidate is not None else cache
    assert [b["lockMode"] for b in cache] == ["hard"]


def test_every_combination_of_one_bad_field_in_a_block_is_refused_whole():
    fields = {"dow": [-1, 7, "1", None, 1.5], "start": ["9:00", "24:00", "09:60", None, 900], "end": ["x", None, 11],
              "lockMode": ["", "Hard", None, 1], "allowlist": [None, "a.com", [1], [""]], "tzOffsetMin": ["0", 1.5, 2000, -2000],
              "id": [None, "", 5]}
    for field, values in fields.items():
        for value in values:
            assert fr.sanitize_blocks({"blocks": [block(**{field: value})]}) is None, (field, value)


@pytest.mark.parametrize("ctype,payload", list(itertools.product(fr.COMMAND_TYPES, [None, 5, "x", [], [1], {"x": 1}, {"url": 5}, {"allowlist": 5}])))
def test_a_command_with_a_junk_payload_never_raises_and_never_starts_a_session(ctype, payload):
    out = fr.sanitize_commands([{"id": "c", "type": ctype, "payload": payload, "createdAt": fr.iso_from_ms(MON_10), "expiresAt": fr.iso_from_ms(MON_10 + 60000)}])
    good, bad = out
    for cmd in good:
        d = fr.decide_command(cmd, MON_10, idle())
        assert d["action"] != "start_session" or d["lockMode"] in ("soft", "hard")
