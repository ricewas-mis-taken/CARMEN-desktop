# Carmen Focus

**Work in progress.** A Windows desktop productivity app for scheduling events, running timed focus sessions, tracking recurring tasks, and doing spaced-repetition review. Built with Python and PySide6, running from the system tray.

## Tabs

- **Calendar** — month grid + day schedule, create/edit timed events, "Next Up" banner.
- **Focus** — ad-hoc timed sessions with a blocklist. Soft lock warns you off a blocked app; hard lock minimizes it and refocuses your last acceptable app. Pause/resume/end anytime, plus a Nuclear End to break out of hard lock.
- **Tasks** — recurring, color-coded tasks with a daily time target, progress bar, and bankable vacation minutes. Click a card to start a timed session for that task.
- **Review** — spaced-repetition tracker: topics → subjects → problems, each with a difficulty rating, review count, and fastest solve time. Can link a topic to a task so review time counts toward it.
- **Finished** — read-only log of completed sessions in the same calendar layout.

## Setup

```
pip install -r requirements.txt
python main.py
```

Requires Windows. Minimizes to the system tray on close.
