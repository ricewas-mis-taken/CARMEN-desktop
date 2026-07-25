# Carmen Focus

A Windows desktop productivity app for scheduling events, running timed focus sessions, tracking recurring tasks, and doing spaced-repetition review. Built with Python and PySide6, running from the system tray.

## Tabs

### Calendar
Month grid + day schedule view. Create and edit timed events, see what is coming up next via the "Next Up" banner, and navigate days by clicking cells in the month grid.

### Focus
Start an ad-hoc focus session with a custom duration and a whitelist of allowed apps/sites. While a session is running, the app enforces the whitelist:
- **Soft lock** — a full-screen overlay warns you when you switch to a non-whitelisted app.
- **Hard lock** — the offending window is minimized and focus is returned to your last whitelisted app.

Pause, resume, or end the session at any time. A "Nuclear End" option is available for breaking out of a hard lock.

### Tasks
Dashboard of recurring tasks, each shown as a color-coded card. Each card displays today's logged time vs. the daily target, a progress bar, and banked vacation time. Click a card to start a timed session for that task. Vacation minutes can be cashed in to count toward a day's target.

### Review
Spaced-repetition tracker for problems and practice questions. Topics are organized as tabs; each topic holds subjects (color-coded) and problems. Each problem stores a description (text, image, or link), a star difficulty rating, review count, and fastest solve time. Clicking Start shows the problem description, then starts an inline timer. Topics can be linked to a task so review time counts toward that task's daily session.

### Finished
Read-only log of completed focus sessions displayed in the same month grid + day schedule layout as the Calendar tab. Click any session block to see its details.

## Setup

```
pip install -r requirements.txt
python main.py
```

Requires Windows. The app minimizes to the system tray on close.
