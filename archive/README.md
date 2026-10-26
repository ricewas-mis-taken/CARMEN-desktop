# Archived

Code taken out of Carmen Focus but kept in the repo.

## Calendar and Finished tabs (2026-10-25)

Removed from the sidebar and the main window. Nothing here is imported by the
app, and `pytest` skips this folder (see `pytest.ini`).

| Archived file | What it was |
| --- | --- |
| `qt_ui/calendar_page.py` | Calendar tab: month grid on top, day schedule below |
| `qt_ui/month_view.py`, `qt_ui/day_view.py` | The two halves of that page |
| `qt_ui/event_editor.py` | New/Edit event dialog |
| `qt_ui/finished_tab.py` | Finished tab: past sessions laid out by day |
| `qt_ui/next_up_widget.py` | "Next up" line on the Focus tab |
| `tests/test_event_editor.py`, `tests/test_finished_tab.py` | Their tests |

Left in place on purpose:

- `calendar_store.py`, `calendar_recurrence.py`, `calendar_toast.py` and the
  calendar sync, backup and API code. Existing events are kept, not deleted.
  `calendar_toast` is also used for the "session complete" toast.
- `calendar_scheduler.py` still exists but `main.py` no longer starts it, so
  event reminders and auto-starting a session at an event's time are off.
- `qt_ui/day_layout.py` (the Tasks tab uses `contrasting_text_color`) and
  `qt_ui/history_viewer.py` (the Session History tab).

## Restoring

Move the files back to where they came from (`git mv archive/qt_ui/X.py qt_ui/`,
tests to `tests/`), restore the two `_add_page` calls and the two sidebar
entries in `qt_ui/main_window.py`, the `NextUpLabel` in `qt_ui/focus_tab.py`,
`refresh_calendar_views()` in `main_window.py`, and uncomment
`calendar_scheduler.start(stop_event)` in `main.py`. The commit that archived
them is the checklist: `git show <that commit>`.
