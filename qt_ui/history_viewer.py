"""Qt port of history_gui.py's session-history viewer.

format_session_html(session) is deliberately a standalone function
returning an HTML fragment (not tied to any particular QTextEdit) so it can
be reused wherever session details need to be shown -- both this viewer
(all sessions, newest first) and, from Stage 5 on, the Finished tab's
per-session detail popup, which previously had to reach into this module's
Tk-specific _write_session(text, session) to reuse the same formatting.
"""
import html
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTextEdit, QVBoxLayout, QWidget

import session_history

SEPARATOR = "─" * 72

_open_windows = set()


def open_history_viewer():
    win = _HistoryViewer()
    _open_windows.add(win)
    win.destroyed.connect(lambda: _open_windows.discard(win))
    win.show()


class _HistoryViewer(QWidget):
    def __init__(self):
        super().__init__()
        self.setObjectName("PopupBg")
        self.setWindowTitle("Carmen Focus — Session History")
        self.resize(680, 540)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        text = QTextEdit()
        text.setReadOnly(True)
        layout.addWidget(text)

        sessions = list(reversed(session_history.load_all()))  # newest first
        if not sessions:
            text.setHtml(_wrap_html("No completed sessions yet — this fills in once a session ends."))
        else:
            body = "".join(format_session_html(s) for s in sessions)
            text.setHtml(_wrap_html(body))


def _wrap_html(body):
    return f'<div style="font-family: Consolas, monospace; font-size: 10pt;">{body}</div>'


def format_session_html(session):
    """Returns an HTML fragment (a <div>...</div>) describing one completed
    session -- header line, blocked apps / allowed sites, violation
    timeline, and any mid-session exceptions. Same content/order as the
    original Tk _write_session(), just emitted as HTML instead of
    Text-widget tag inserts."""
    start = _parse(session.get("startTime"))
    end = _parse(session.get("endTime"))

    end_type = session.get("endType", "manual")
    end_label = "NUCLEAR" if end_type == "nuclear" else end_type
    header_color = "#c62828" if end_type == "nuclear" else None

    if start and end:
        duration = _format_duration(int((end - start).total_seconds()))
        header = (
            f"{_format_dt(start)}  →  {_format_dt(end)}   "
            f"({duration}, {_esc(session.get('lockMode', '?'))} lock, {end_label} end)"
        )
    else:
        header = f"(unknown start/end time, {_esc(session.get('lockMode', '?'))} lock, {end_label} end)"

    parts = []
    if header_color:
        parts.append(f'<b style="color:{header_color};">{_esc(header)}</b><br>')
    else:
        parts.append(f"<b>{_esc(header)}</b><br>")
    parts.append(f'<span style="color:#888888;">{"─" * len(header)}</span><br>')

    reason = session.get("reason")
    if end_type == "nuclear":
        parts.append(
            f'<span style="color:#c62828;">Nuclear end reason: '
            f'{_esc(reason or "(none given)")}</span><br>'
        )

    process_blocklist = session.get("processBlocklist") or []
    domain_whitelist = session.get("domainWhitelist") or []
    parts.append(f"Blocked apps:&nbsp; {_esc(', '.join(process_blocklist) or '(none)')}<br>")
    parts.append(f"Allowed sites: {_esc(', '.join(domain_whitelist) or '(none)')}<br>")

    violation_log = session.get("violationLog") or []
    violation_count = session.get(
        "violationCount", sum(1 for e in violation_log if e.get("kind") in ("process", "domain"))
    )
    parts.append(f'<span style="color:#888888;">Violations: {violation_count}</span><br>')

    for entry in violation_log:
        kind = entry.get("kind", "process")
        if kind in ("pause", "resume"):
            parts.append(f'<span style="color:#888888;">&nbsp;&nbsp;{_esc(_format_pause_event(entry))}</span><br>')
        else:
            line, color = _format_violation(entry)
            parts.append(f'<span style="color:{color};">&nbsp;&nbsp;{_esc(line)}</span><br>')

    process_exceptions = session.get("processBlocklistExceptions") or []
    domain_additions = session.get("domainWhitelistAdditions") or []
    if process_exceptions or domain_additions:
        parts.append('<span style="color:#888888;">Mid-session exceptions:</span><br>')
        for entry in process_exceptions:
            parts.append(f"&nbsp;&nbsp;{_esc(_format_exception('app unblocked', entry, 'process'))}<br>")
        for entry in domain_additions:
            parts.append(f"&nbsp;&nbsp;{_esc(_format_exception('site allowed', entry, 'domain'))}<br>")

    parts.append(f'<br><span style="color:#888888;">{SEPARATOR}</span><br><br>')
    return "".join(parts)


def _format_violation(entry):
    kind = entry.get("kind", "process")
    name = entry.get("process") if kind == "process" else entry.get("url")
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"

    duration_seconds = entry.get("durationSeconds")
    if duration_seconds is not None:
        resolution = f"back on track after {_format_duration(duration_seconds)}"
        color = "#2e7d32"
    else:
        resolution = "never corrected before session ended"
        color = "#c62828"

    return f"[{kind}] {name}  —  {time_text}  —  {resolution}", color


def _format_pause_event(entry):
    label = "Paused" if entry.get("kind") == "pause" else "Resumed"
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"
    return f"{label} at {time_text}"


def _format_exception(kind_label, entry, key):
    name = entry.get(key, "?")
    reason = entry.get("reason") or "(no reason given)"
    ts = _parse(entry.get("timestamp"))
    time_text = ts.strftime("%H:%M:%S") if ts else "?"
    return f"[{kind_label}] {name}  —  {time_text}  —  {reason}"


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


def _esc(text):
    return html.escape(str(text))
