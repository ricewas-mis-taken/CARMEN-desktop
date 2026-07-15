"""Tkinter window showing the persisted history of completed focus sessions
(session_history.json), launched from the tray's "Session History" menu
item. Built as a Toplevel on the shared GUI-thread root, same as
picker_gui.py — see gui_thread.py for why every popup shares one root/thread
instead of spinning up its own."""
import tkinter as tk
from datetime import datetime

import gui_thread
import session_history

SEPARATOR = "─" * 72


def open_history_viewer():
    gui_thread.run_on_gui_thread(_build_history_viewer)


def _build_history_viewer(root):
    win = tk.Toplevel(root)
    win.title("Carmen Focus — Session History")
    win.geometry("680x540")

    sessions = list(reversed(session_history.load_all()))  # newest first

    text_frame = tk.Frame(win)
    text_frame.pack(fill="both", expand=True)

    text = tk.Text(text_frame, wrap="word", font=("Consolas", 10), padx=12, pady=10)
    scrollbar = tk.Scrollbar(text_frame, orient="vertical", command=text.yview)
    text.configure(yscrollcommand=scrollbar.set)
    scrollbar.pack(side="right", fill="y")
    text.pack(side="left", fill="both", expand=True)

    text.tag_configure("header", font=("Consolas", 10, "bold"))
    text.tag_configure("dim", foreground="#888888")
    text.tag_configure("resolved", foreground="#2e7d32")
    text.tag_configure("unresolved", foreground="#c62828")

    if not sessions:
        text.insert("end", "No completed sessions yet — this fills in once a session ends.")
    else:
        for session in sessions:
            _write_session(text, session)

    text.config(state="disabled")


def _write_session(text, session):
    start = _parse(session.get("startTime"))
    end = _parse(session.get("endTime"))

    if start and end:
        duration = _format_duration(int((end - start).total_seconds()))
        header = f"{_format_dt(start)}  →  {_format_dt(end)}   ({duration}, {session.get('lockMode', '?')} lock)"
    else:
        header = f"(unknown start/end time, {session.get('lockMode', '?')} lock)"

    text.insert("end", header + "\n", "header")
    text.insert("end", ("─" * len(header)) + "\n", "dim")

    process_whitelist = session.get("processWhitelist") or []
    domain_whitelist = session.get("domainWhitelist") or []
    text.insert("end", f"Allowed apps:  {', '.join(process_whitelist) or '(none)'}\n")
    text.insert("end", f"Allowed sites: {', '.join(domain_whitelist) or '(none)'}\n")

    violation_log = session.get("violationLog") or []
    violation_count = session.get("violationCount", len(violation_log))
    text.insert("end", f"Violations: {violation_count}\n", "dim")

    for entry in violation_log:
        line, tag = _format_violation(entry)
        text.insert("end", "  " + line + "\n", tag)

    text.insert("end", "\n" + SEPARATOR + "\n\n", "dim")


def _format_violation(entry):
    kind = entry.get("kind", "process")
    name = entry.get("process") if kind == "process" else entry.get("url")
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"

    duration_seconds = entry.get("durationSeconds")
    if duration_seconds is not None:
        resolution = f"back on track after {_format_duration(duration_seconds)}"
        tag = "resolved"
    else:
        resolution = "never corrected before session ended"
        tag = "unresolved"

    return f"[{kind}] {name}  —  {time_text}  —  {resolution}", tag


def _format_dt(dt):
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_duration(total_seconds):
    minutes, seconds = divmod(max(0, total_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {seconds}s"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _parse(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string)
    except ValueError:
        return None
