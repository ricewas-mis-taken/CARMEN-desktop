import pytest

toast = pytest.importorskip("calendar_toast")
pytest.importorskip("winsdk")


def test_control_characters_in_title_do_not_drop_the_toast(monkeypatch):
    shown = []

    class FakeNotifier:
        def show(self, t):
            shown.append(t)

    monkeypatch.setattr(toast, "_notifier", FakeNotifier())
    for title in ("Quarterly\x0creview", "Lab\x08report"):
        shown.clear()
        toast.show_toast(title, "is starting now.")
        assert len(shown) == 1, title
