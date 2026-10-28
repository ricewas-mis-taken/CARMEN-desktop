"""Chrome/Edge per-profile identification from Accessibility window titles."""
import pytest

from mac_os import browser_profiles_mac as bp

PROFILES = {
    "Default": ["Rice"],
    "Profile 2": ["Lucas"],
    "Profile 3": ["Lucas (stu.powayusd.com)", "stu.powayusd.com"],
}


@pytest.mark.parametrize("title,folder", [
    ("Problem - B - Codeforces - Google Chrome - Rice", "Default"),
    ("Example Domain - Google Chrome - Lucas", "Profile 2"),
    ("Calendar - Google Chrome - Lucas (stu.powayusd.com)", "Profile 3"),
    ("Google Chrome - Lucas", "Profile 2"),
    ("Something - Google Chrome - Nobody", None),
    ("Untitled", None),
    (None, None),
])
def test_folder_for_title(title, folder):
    assert bp.folder_for_title("Google Chrome", title, PROFILES) == folder


def test_single_profile_title_without_suffix_is_default():
    assert bp.folder_for_title("Google Chrome", "Example - Google Chrome", {"Default": ["Rice"]}) == "Default"


def test_longest_name_wins_over_a_shorter_suffix():
    profiles = {"Profile 1": ["com"], "Profile 2": ["stu.x.com"]}
    assert bp.folder_for_title("Google Chrome", "T - Google Chrome - stu.x.com", profiles) == "Profile 2"


def test_aumi_strings_match_windows():
    assert bp.folder_to_aumi("Google Chrome", "Default") == "Chrome"
    assert bp.folder_to_aumi("Google Chrome", "Profile 4") == "Chrome.UserData.Profile4"
    assert bp.folder_to_aumi("Microsoft Edge", "Profile 1") == "MSEdge.UserData.Profile1"
    assert bp.folder_to_aumi("Safari", "Default") is None


def test_edge_uses_its_own_label():
    assert bp.aumi_for_title("Microsoft Edge", "T - Microsoft Edge - Work", {"Profile 1": ["Work"]}) == "MSEdge.UserData.Profile1"


def test_read_profiles_and_describe(tmp_path, monkeypatch):
    (tmp_path / "Local State").write_text(
        '{"profile": {"info_cache": {"Default": {"name": "Rice"},'
        ' "Profile 3": {"name": "stu", "shortcut_name": "Lucas (stu)"}}}}'
    )
    monkeypatch.setitem(bp.BROWSERS["google chrome"], "data_dir", str(tmp_path))
    assert bp.read_profiles("Google Chrome") == {"Default": ["Rice"], "Profile 3": ["Lucas (stu)", "stu"]}
    assert bp.list_known_aumis("Google Chrome") == ["Chrome", "Chrome.UserData.Profile3"]
    assert bp.describe_aumi("Google Chrome", "Chrome.UserData.Profile3") == "Google Chrome — Lucas (stu)"
    assert bp.describe_aumi("Google Chrome", "Chrome") == "Google Chrome — Rice"


def test_missing_or_broken_local_state_is_empty(tmp_path, monkeypatch):
    monkeypatch.setitem(bp.BROWSERS["google chrome"], "data_dir", str(tmp_path))
    assert bp.read_profiles("Google Chrome") == {}
    (tmp_path / "Local State").write_text("{nope")
    assert bp.read_profiles("Google Chrome") == {}
    assert bp.list_known_aumis("Google Chrome") == []
