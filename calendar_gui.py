"""Main window: a persistent left-sidebar + tabbed (Calendar / Focus) window
opened by a single click on the tray icon, replacing the old "tray icon as a
standalone focus toggle" behavior.

Built as one Toplevel on the single shared GUI-thread root (gui_thread.py),
same as every other popup in this app (picker_gui's dialogs, enforcer's lock
overlays) — never a second Tk(). A module-level singleton reference means a
repeat tray click lifts the existing window instead of spawning another one.
"""
import calendar as calendar_module
import functools
import os
import tkinter as tk
import tkinter.font as tkfont
from datetime import date, datetime, timedelta
from tkinter import colorchooser, filedialog, messagebox, ttk

import calendar_recurrence as recurrence
import calendar_store as store
import checklist_widget
import config
import gui_thread
import history_gui
import installed_apps
import picker_gui
import rounded_widgets as rw
import session_history
import session_manager

COLOR_PALETTE = [
    "#2d8cff", "#e53935", "#43a047", "#fb8c00", "#8e24aa",
    "#00acc1", "#f4511e", "#3949ab", "#6d4c41", "#546e7a",
]

# Visual-only constants — a light, airy Google/macOS-Calendar-style theme for
# the Calendar tab (the Focus tab keeps its current look for now, pending a
# follow-up pass). Nothing below this point changes data flow or event
# handling; it's all bg/fg/font/spacing on the same widgets.
FONT = "Segoe UI"
THEME = {
    "bg": "#ffffff",
    "bg_soft": "#fafbfc",
    "sidebar_bg": "#262b36",
    "sidebar_hover": "#333a48",
    "sidebar_active": "#3a4256",
    "sidebar_text": "#e8eaed",
    "sidebar_text_muted": "#9aa0ac",
    "grid_line": "#eceef1",
    "border": "#e3e5e9",
    "text": "#1f2328",
    "text_muted": "#8a8f98",
    "text_faint": "#c7cad0",
    "today_bg": "#eaf2ff",
    "today_border": "#2d8cff",
    "selected_bg": "#f1f3f6",
    "selected_border": "#c9ccd3",
    "weekend_bg": "#fafbfc",
    "accent": "#2d8cff",
    "accent_soft": "#eef4ff",
    "button_secondary": "#f1f3f6",
    "button_secondary_hover": "#e6e9ee",
}


def _letter_spaced(text):
    """Cheap letter-spacing simulation — Tkinter fonts have no tracking
    property, so day-of-week headers get a hair-space between characters
    instead, matching the "uppercase, letter-spaced" look without it."""
    return " ".join(text.upper())

REMINDER_PRESETS = [
    ("At start time", 0),
    ("10 minutes before", 10),
    ("30 minutes before", 30),
    ("1 hour before", 60),
    ("1 day before", 1440),
]

_state = {
    "win": None,
    "selected_date": None,
    "search_query": "",
    "refresh_callbacks": [],
}


def open_main_window():
    gui_thread.run_on_gui_thread(_open_or_focus)


def _open_or_focus(root):
    win = _state["win"]
    if win is not None and win.winfo_exists():
        win.deiconify()
        win.lift()
        win.focus_force()
        return
    _build_main_window(root)


def _build_main_window(root):
    win = tk.Toplevel(root)
    _state["win"] = win
    _state["selected_date"] = date.today()
    win.title("Carmen Focus")
    win.geometry("900x700")
    win.minsize(680, 520)
    win.configure(bg=THEME["bg"])

    sidebar = tk.Frame(win, width=168, bg=THEME["sidebar_bg"])
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    content = tk.Frame(win, bg=THEME["bg"])
    content.pack(side="left", fill="both", expand=True)

    calendar_frame = tk.Frame(content, bg=THEME["bg"])
    focus_frame = tk.Frame(content, bg=THEME["bg"])
    finished_frame = tk.Frame(content, bg=THEME["bg"])
    tab_frames = {"calendar": calendar_frame, "focus": focus_frame, "finished": finished_frame}
    for frame in tab_frames.values():
        frame.place(x=0, y=0, relwidth=1, relheight=1)

    tk.Label(
        sidebar, text="Carmen Focus", bg=THEME["sidebar_bg"], fg=THEME["sidebar_text"],
        font=(FONT, 13, "bold"), anchor="w",
    ).pack(fill="x", padx=18, pady=(22, 20))

    tab_buttons = {}

    def show_tab(name):
        for n, btn in tab_buttons.items():
            btn.set_active(n == name, active_bg=THEME["sidebar_active"])
        tab_frames[name].tkraise()

    nav_items = [("calendar", "📅  Calendar"), ("focus", "🎯  Focus"), ("finished", "✅  Finished")]
    for key, label in nav_items:
        btn = rw.RoundedButton(
            sidebar, label, command=lambda k=key: show_tab(k),
            bg=THEME["sidebar_bg"], hover_bg=THEME["sidebar_hover"], fg=THEME["sidebar_text"],
            font=(FONT, 10), radius=8, width=144, height=38, anchor="w", padx=14,
            parent_bg=THEME["sidebar_bg"],
        )
        btn.pack(padx=12, pady=3)
        tab_buttons[key] = btn

    tk.Frame(sidebar, bg=THEME["sidebar_bg"]).pack(fill="both", expand=True)  # spacer

    rw.RoundedButton(
        sidebar, "Backup / Restore…", command=lambda: _open_backup_dialog(win),
        bg=THEME["sidebar_bg"], hover_bg=THEME["sidebar_hover"], fg=THEME["sidebar_text_muted"],
        font=(FONT, 8), radius=8, width=144, height=30, anchor="w", padx=14,
        parent_bg=THEME["sidebar_bg"],
    ).pack(padx=12, pady=(0, 16))

    _state["refresh_callbacks"] = []
    _build_calendar_tab(calendar_frame, win)
    _build_focus_tab(focus_frame, win)
    _build_finished_tab(finished_frame, win)

    show_tab("calendar")

    def on_close():
        _state["win"] = None
        win.destroy()

    win.protocol("WM_DELETE_WINDOW", on_close)


# ---------------------------------------------------------------------------
# Focus tab — hosts the existing whitelist picker / session controls rather
# than re-implementing them: this is composition over the same
# session_manager/picker_gui/history_gui functions the tray menu already
# calls, not a parallel duplicate of that logic.
# ---------------------------------------------------------------------------

def _build_focus_tab(parent, win):
    tk.Label(parent, text="Focus", font=("Segoe UI", 16, "bold")).pack(anchor="w", padx=20, pady=(20, 4))

    next_up = tk.Frame(parent)
    next_up.pack(fill="x", padx=20, pady=(0, 10))
    _register_next_up_widget(next_up)

    status_label = tk.Label(parent, font=("Segoe UI", 10), justify="left", anchor="w")
    status_label.pack(fill="x", padx=20, pady=(4, 12))

    def refresh_status():
        status = session_manager.get_status()
        if not status["isActive"]:
            status_label.config(text="No active focus session.")
        else:
            minutes, seconds = divmod(status["secondsRemaining"], 60)
            paused = " (paused)" if status["isPaused"] else ""
            source_note = ""
            if status.get("source") == "calendar-event" and status.get("eventTitle"):
                source_note = f"\nFrom calendar event: {status['eventTitle']}"
            status_label.config(
                text=(
                    f"Active session{paused} — {minutes}m {seconds}s remaining\n"
                    f"Lock mode: {status['lockMode']}   Violations: {status['violationCount']}"
                    f"{source_note}"
                )
            )
        if win.winfo_exists():
            win.after(1000, refresh_status)

    refresh_status()

    button_frame = tk.Frame(parent)
    button_frame.pack(fill="x", padx=20, pady=6)

    tk.Button(button_frame, text="Start Focus Session", width=24,
              command=picker_gui.open_timer_dialog).pack(anchor="w", pady=3)
    tk.Button(button_frame, text="Pick Apps to Whitelist", width=24,
              command=picker_gui.open_whitelist_picker).pack(anchor="w", pady=3)

    def pause_resume():
        if session_manager.get_status()["isPaused"]:
            session_manager.resume_session()
        else:
            session_manager.pause_session()

    tk.Button(button_frame, text="Pause / Resume Session", width=24,
              command=pause_resume).pack(anchor="w", pady=3)
    tk.Button(button_frame, text="Session History", width=24,
              command=history_gui.open_history_viewer).pack(anchor="w", pady=3)


# ---------------------------------------------------------------------------
# Calendar tab — month grid (~60% height) on top, selected day's hourly
# schedule (~40% height, scrollable) on bottom.
# ---------------------------------------------------------------------------

def _build_calendar_tab(parent, win):
    header = tk.Frame(parent, bg=THEME["bg"])
    header.pack(fill="x", padx=24, pady=(22, 6))

    next_up = tk.Frame(parent, bg=THEME["bg"])
    next_up.pack(fill="x", padx=24, pady=(0, 8))
    _register_next_up_widget(next_up)

    search_var = tk.StringVar(master=win)

    month_state = {"cursor": date.today().replace(day=1)}

    nav_frame = tk.Frame(header, bg=THEME["bg"])
    nav_frame.pack(side="left")
    month_label = tk.Label(nav_frame, font=(FONT, 17, "bold"), bg=THEME["bg"], fg=THEME["text"])
    month_label.pack(side="left", padx=(0, 16))

    search_frame = tk.Frame(header, bg=THEME["border"], padx=1, pady=1)
    search_frame.pack(side="right")
    search_inner = tk.Frame(search_frame, bg=THEME["bg_soft"])
    search_inner.pack()
    tk.Label(
        search_inner, text="🔍", bg=THEME["bg_soft"], fg=THEME["text_muted"], font=(FONT, 9),
    ).pack(side="left", padx=(10, 4), pady=6)
    search_entry = tk.Entry(
        search_inner, textvariable=search_var, width=18, bd=0, bg=THEME["bg_soft"],
        fg=THEME["text"], font=(FONT, 10), highlightthickness=0, insertbackground=THEME["text"],
    )
    search_entry.pack(side="left", padx=(0, 10), pady=7)

    body = tk.PanedWindow(
        parent, orient="vertical", sashrelief="flat", sashwidth=8,
        bg=THEME["bg"], bd=0,
    )
    body.pack(fill="both", expand=True, padx=24, pady=(4, 20))

    month_frame = tk.Frame(body, bg=THEME["bg"])
    day_frame = tk.Frame(body, bg=THEME["bg"])
    body.add(month_frame, height=380)
    body.add(day_frame, height=260)

    grid_cells = tk.Frame(month_frame, bg=THEME["grid_line"])

    def render_month():
        for child in grid_cells.winfo_children():
            child.destroy()
        cursor = month_state["cursor"]
        month_label.config(text=cursor.strftime("%B %Y"))

        weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for i, name in enumerate(weekday_names):
            tk.Label(
                grid_cells, text=_letter_spaced(name), font=(FONT, 8, "bold"),
                fg=THEME["text_muted"], bg=THEME["bg"], pady=8,
            ).grid(row=0, column=i, sticky="nsew", padx=1, pady=(0, 1))

        cal = calendar_module.Calendar(firstweekday=6)  # Sunday-start
        month_days = list(cal.itermonthdates(cursor.year, cursor.month))

        query = search_var.get().strip().lower()
        events = store.list_events()
        if query:
            events = [e for e in events if query in e["title"].lower()]

        range_start = datetime.combine(month_days[0], datetime.min.time())
        range_end = datetime.combine(month_days[-1] + timedelta(days=1), datetime.min.time())
        occurrences_by_day = {}
        for event in events:
            for occ_start, _occ_end in recurrence.expand_occurrences(event, range_start, range_end):
                occurrences_by_day.setdefault(occ_start.date(), []).append(event)

        for i in range(6):
            grid_cells.grid_rowconfigure(i + 1, weight=1)
        for i in range(7):
            grid_cells.grid_columnconfigure(i, weight=1)

        for idx, day in enumerate(month_days):
            row, col = divmod(idx, 7)
            in_month = day.month == cursor.month
            is_today = day == date.today()
            is_selected = day == _state["selected_date"]
            is_weekend = col in (0, 6)

            if is_today:
                cell_bg = THEME["today_bg"]
            elif is_selected:
                cell_bg = THEME["selected_bg"]
            elif in_month:
                cell_bg = THEME["weekend_bg"] if is_weekend else THEME["bg"]
            else:
                cell_bg = THEME["bg_soft"]

            border_color = THEME["today_border"] if is_today else (
                THEME["selected_border"] if is_selected else THEME["grid_line"]
            )
            cell = tk.Frame(
                grid_cells, bg=cell_bg,
                highlightbackground=border_color, highlightthickness=1,
            )
            cell.grid(row=row + 1, column=col, sticky="nsew", padx=1, pady=1)

            day_number = tk.Label(
                cell, text=str(day.day), anchor="ne",
                bg=cell_bg,
                fg=(THEME["accent"] if is_today else THEME["text"]) if in_month else THEME["text_faint"],
                font=(FONT, 10, "bold" if is_today else "normal"),
            )
            day_number.pack(fill="x", padx=6, pady=(5, 2))

            def on_click(d=day):
                _state["selected_date"] = d
                render_month()
                render_day()

            cell.bind("<Button-1>", lambda e, d=day: on_click(d))
            day_number.bind("<Button-1>", lambda e, d=day: on_click(d))

            day_events = occurrences_by_day.get(day, [])
            MAX_STRIPS = 3
            for event in day_events[:MAX_STRIPS]:
                # A small rounded pill per event, overlaid on the day cell —
                # same "block of color" language as the day view's hourly
                # event blocks, just collapsed to one compact chip per day
                # instead of being positioned by time.
                pill_holder = tk.Frame(cell, bg=cell_bg)
                pill_holder.pack(fill="x", padx=5, pady=1)
                pill_canvas = tk.Canvas(
                    # width=1: Canvas defaults to a ~200px requested width
                    # with none given, which — inside a grid column with no
                    # other wide content — was forcing that day's column to
                    # balloon out and push later weekday columns off the
                    # visible grid. pack(fill="x") still stretches it to the
                    # cell's actual width once the column size is settled;
                    # only the initial size *hint* to the grid changes.
                    pill_holder, height=16, width=1, highlightthickness=0, bg=cell_bg,
                )
                pill_canvas.pack(fill="x")

                def draw_pill(canvas=pill_canvas, ev=event):
                    canvas.delete("all")
                    canvas.update_idletasks()
                    w = max(canvas.winfo_width(), 40)
                    title = ev["title"]
                    # Rough char budget for an 8pt Segoe UI pill at this
                    # width — good enough for "truncated, not raw text".
                    max_chars = max(4, int(w / 6.2))
                    if len(title) > max_chars:
                        title = title[: max_chars - 1].rstrip() + "…"
                    rw.draw_pill(
                        canvas, 0, 0, w, 16, fill=ev["color"], text=title,
                        text_fill=_contrasting_text_color(ev["color"]), font=(FONT, 7),
                    )

                pill_canvas.bind("<Configure>", lambda e, fn=draw_pill: fn())
                pill_canvas.bind(
                    "<Button-1>",
                    lambda e, d=day, ev=event: (_state.update(selected_date=d), render_month(), render_day(), open_event_editor(win, event_id=ev["id"]))[-1],
                )

            if len(day_events) > MAX_STRIPS:
                more_label = tk.Label(
                    cell, text=f"+{len(day_events) - MAX_STRIPS} more", anchor="w",
                    bg=cell_bg, fg=THEME["text_muted"], font=(FONT, 7),
                )
                more_label.pack(fill="x", padx=6, pady=(0, 3))
                more_label.bind("<Button-1>", lambda e, d=day: on_click(d))

    grid_cells.pack(fill="both", expand=True)

    def prev_month():
        c = month_state["cursor"]
        month_state["cursor"] = (c.replace(day=1) - timedelta(days=1)).replace(day=1)
        render_month()

    def next_month():
        c = month_state["cursor"]
        days_in_month = calendar_module.monthrange(c.year, c.month)[1]
        month_state["cursor"] = (c + timedelta(days=days_in_month)).replace(day=1)
        render_month()

    rw.RoundedButton(
        nav_frame, "‹", command=prev_month, bg=THEME["button_secondary"],
        hover_bg=THEME["button_secondary_hover"], fg=THEME["text"], font=(FONT, 11),
        width=30, height=30, radius=8, parent_bg=THEME["bg"],
    ).pack(side="left", padx=(0, 4))
    rw.RoundedButton(
        nav_frame, "›", command=next_month, bg=THEME["button_secondary"],
        hover_bg=THEME["button_secondary_hover"], fg=THEME["text"], font=(FONT, 11),
        width=30, height=30, radius=8, parent_bg=THEME["bg"],
    ).pack(side="left")
    rw.RoundedButton(
        nav_frame, "Today",
        command=lambda: _jump_today(month_state, render_month_cb=render_month, render_day_cb=lambda: render_day()),
        bg=THEME["button_secondary"], hover_bg=THEME["button_secondary_hover"], fg=THEME["text"],
        font=(FONT, 9), radius=8, parent_bg=THEME["bg"],
    ).pack(side="left", padx=(10, 0))
    rw.RoundedButton(
        nav_frame, "+  New Event",
        command=lambda: open_event_editor(win, initial_date=_state["selected_date"]),
        bg=THEME["accent"], hover_bg=rw.shade(THEME["accent"], -10), fg="white",
        font=(FONT, 9, "bold"), radius=8, parent_bg=THEME["bg"],
    ).pack(side="left", padx=(14, 0))

    # --- day schedule (bottom pane) ---
    day_header = tk.Frame(day_frame, bg=THEME["bg"])
    day_header.pack(fill="x", pady=(4, 8))
    day_title = tk.Label(day_header, font=(FONT, 13, "bold"), bg=THEME["bg"], fg=THEME["text"])
    day_title.pack(side="left")

    day_canvas_container = tk.Frame(day_frame, bg=THEME["bg"])
    day_canvas_container.pack(fill="both", expand=True)
    day_canvas = tk.Canvas(day_canvas_container, highlightthickness=0, bg=THEME["bg"])
    day_scrollbar = tk.Scrollbar(day_canvas_container, orient="vertical", command=day_canvas.yview)
    day_canvas.configure(yscrollcommand=day_scrollbar.set)
    day_canvas.pack(side="left", fill="both", expand=True)
    day_scrollbar.pack(side="right", fill="y")

    HOUR_HEIGHT = 52
    LABEL_WIDTH = 60
    EVENT_LEFT_MARGIN = 10
    EVENT_RIGHT_EDGE = 560
    MIN_BLOCK_HEIGHT = 16
    MIN_BLOCK_DURATION = timedelta(minutes=(MIN_BLOCK_HEIGHT / HOUR_HEIGHT) * 60)
    day_view_state = {"last_date": None}

    def render_day():
        day_canvas.delete("all")
        selected = _state["selected_date"]
        day_title.config(text=selected.strftime("%A, %B %d, %Y"))

        total_height = HOUR_HEIGHT * 24
        day_canvas.configure(scrollregion=(0, 0, 600, total_height))

        for hour in range(24):
            y = hour * HOUR_HEIGHT
            label = datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")
            day_canvas.create_line(LABEL_WIDTH - 8, y, 2000, y, fill=THEME["grid_line"])
            day_canvas.create_text(
                LABEL_WIDTH - 14, y + 2, anchor="ne", text=label,
                font=(FONT, 8), fill=THEME["text_muted"],
            )

        range_start = datetime.combine(selected, datetime.min.time())
        range_end = range_start + timedelta(days=1)
        query = search_var.get().strip().lower()
        events = store.list_events()
        if query:
            events = [e for e in events if query in e["title"].lower()]

        day_events = []
        for event in events:
            for occ_start, occ_end in recurrence.expand_occurrences(event, range_start, range_end):
                day_events.append((occ_start, occ_end, event))

        block_x0 = LABEL_WIDTH + EVENT_LEFT_MARGIN
        for occ_start, occ_end, event, col, cols in _layout_day_blocks(day_events, min_duration=MIN_BLOCK_DURATION):
            start_minutes = max(0, (occ_start - range_start).total_seconds() / 60)
            end_minutes = min(24 * 60, (occ_end - range_start).total_seconds() / 60)
            y0 = (start_minutes / 60) * HOUR_HEIGHT
            y1 = (end_minutes / 60) * HOUR_HEIGHT
            y1 = max(y1, y0 + MIN_BLOCK_HEIGHT)
            col_width = (EVENT_RIGHT_EDGE - block_x0) / cols
            gap = 4 if cols > 1 else 0
            x0 = block_x0 + col * col_width
            x1 = x0 + col_width - gap
            rect = rw.draw_rounded_rect(
                day_canvas, x0, y0 + 1, x1, y1 - 1, radius=6,
                fill=event["color"], outline="",
            )
            full_title = event["title"]
            if event.get("focusProfile") and event["focusProfile"].get("enabled"):
                full_title = "🎯 " + full_title
            label_font = (FONT, 9)
            label_text = _fit_block_label(label_font, (x1 - x0) - 16, full_title)
            text = day_canvas.create_text(
                x0 + 8, (y0 + y1) / 2, anchor="w", text=label_text,
                fill=_contrasting_text_color(event["color"]), font=label_font,
            )
            for item in (rect, text):
                day_canvas.tag_bind(
                    item, "<Button-1>",
                    lambda e, ev=event: open_event_editor(win, event_id=ev["id"]),
                )

        if day_view_state["last_date"] != selected:
            day_view_state["last_date"] = selected
            _scroll_to_current_hour(day_canvas, HOUR_HEIGHT, total_height)

    def _jump_today(month_state, render_month_cb, render_day_cb):
        month_state["cursor"] = date.today().replace(day=1)
        _state["selected_date"] = date.today()
        render_month_cb()
        render_day_cb()

    def refresh_all():
        render_month()
        render_day()

    search_var.trace_add("write", lambda *_: refresh_all())
    _state["refresh_callbacks"].append(refresh_all)

    render_month()
    render_day()


# ---------------------------------------------------------------------------
# Finished tab — same month-grid/day-schedule layout as the Calendar tab, but
# reading from session_history.py (logged, completed focus sessions) instead
# of calendar_store.py (scheduled events). No create/edit affordances here:
# entries are appended by session_manager.end_session(), never authored by
# hand, so this tab is read-only.
# ---------------------------------------------------------------------------

SESSION_END_COLORS = {
    "manual": THEME["accent"],
    "nuclear": "#e53935",
    "timeout": "#fb8c00",
}


def _session_color(session):
    return SESSION_END_COLORS.get(session.get("endType", "manual"), THEME["accent"])


def _session_title(session):
    return session.get("eventTitle") or "Focus session"


def _parse_session_dt(iso_string):
    if not iso_string:
        return None
    try:
        return datetime.fromisoformat(iso_string)
    except ValueError:
        return None


def _build_finished_tab(parent, win):
    header = tk.Frame(parent, bg=THEME["bg"])
    header.pack(fill="x", padx=24, pady=(22, 6))

    last_session = tk.Frame(parent, bg=THEME["bg"])
    last_session.pack(fill="x", padx=24, pady=(0, 8))
    _register_last_session_widget(last_session)

    search_var = tk.StringVar(master=win)

    month_state = {"cursor": date.today().replace(day=1)}

    nav_frame = tk.Frame(header, bg=THEME["bg"])
    nav_frame.pack(side="left")
    month_label = tk.Label(nav_frame, font=(FONT, 17, "bold"), bg=THEME["bg"], fg=THEME["text"])
    month_label.pack(side="left", padx=(0, 16))

    search_frame = tk.Frame(header, bg=THEME["border"], padx=1, pady=1)
    search_frame.pack(side="right")
    search_inner = tk.Frame(search_frame, bg=THEME["bg_soft"])
    search_inner.pack()
    tk.Label(
        search_inner, text="🔍", bg=THEME["bg_soft"], fg=THEME["text_muted"], font=(FONT, 9),
    ).pack(side="left", padx=(10, 4), pady=6)
    search_entry = tk.Entry(
        search_inner, textvariable=search_var, width=18, bd=0, bg=THEME["bg_soft"],
        fg=THEME["text"], font=(FONT, 10), highlightthickness=0, insertbackground=THEME["text"],
    )
    search_entry.pack(side="left", padx=(0, 10), pady=7)

    body = tk.PanedWindow(
        parent, orient="vertical", sashrelief="flat", sashwidth=8,
        bg=THEME["bg"], bd=0,
    )
    body.pack(fill="both", expand=True, padx=24, pady=(4, 20))

    month_frame = tk.Frame(body, bg=THEME["bg"])
    day_frame = tk.Frame(body, bg=THEME["bg"])
    body.add(month_frame, height=380)
    body.add(day_frame, height=260)

    grid_cells = tk.Frame(month_frame, bg=THEME["grid_line"])

    def matching_sessions():
        query = search_var.get().strip().lower()
        sessions = session_history.load_all()
        if query:
            sessions = [
                s for s in sessions
                if query in (_session_title(s).lower()) or query in (s.get("reason") or "").lower()
            ]
        return sessions

    def render_month():
        for child in grid_cells.winfo_children():
            child.destroy()
        cursor = month_state["cursor"]
        month_label.config(text=cursor.strftime("%B %Y"))

        weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for i, name in enumerate(weekday_names):
            tk.Label(
                grid_cells, text=_letter_spaced(name), font=(FONT, 8, "bold"),
                fg=THEME["text_muted"], bg=THEME["bg"], pady=8,
            ).grid(row=0, column=i, sticky="nsew", padx=1, pady=(0, 1))

        cal = calendar_module.Calendar(firstweekday=6)  # Sunday-start
        month_days = list(cal.itermonthdates(cursor.year, cursor.month))

        sessions_by_day = {}
        for session in matching_sessions():
            start = _parse_session_dt(session.get("startTime"))
            if start:
                sessions_by_day.setdefault(start.date(), []).append(session)

        for i in range(6):
            grid_cells.grid_rowconfigure(i + 1, weight=1)
        for i in range(7):
            grid_cells.grid_columnconfigure(i, weight=1)

        for idx, day in enumerate(month_days):
            row, col = divmod(idx, 7)
            in_month = day.month == cursor.month
            is_today = day == date.today()
            is_selected = day == _state["selected_date"]
            is_weekend = col in (0, 6)

            if is_today:
                cell_bg = THEME["today_bg"]
            elif is_selected:
                cell_bg = THEME["selected_bg"]
            elif in_month:
                cell_bg = THEME["weekend_bg"] if is_weekend else THEME["bg"]
            else:
                cell_bg = THEME["bg_soft"]

            border_color = THEME["today_border"] if is_today else (
                THEME["selected_border"] if is_selected else THEME["grid_line"]
            )
            cell = tk.Frame(
                grid_cells, bg=cell_bg,
                highlightbackground=border_color, highlightthickness=1,
            )
            cell.grid(row=row + 1, column=col, sticky="nsew", padx=1, pady=1)

            day_number = tk.Label(
                cell, text=str(day.day), anchor="ne",
                bg=cell_bg,
                fg=(THEME["accent"] if is_today else THEME["text"]) if in_month else THEME["text_faint"],
                font=(FONT, 10, "bold" if is_today else "normal"),
            )
            day_number.pack(fill="x", padx=6, pady=(5, 2))

            def on_click(d=day):
                _state["selected_date"] = d
                render_month()
                render_day()

            cell.bind("<Button-1>", lambda e, d=day: on_click(d))
            day_number.bind("<Button-1>", lambda e, d=day: on_click(d))

            day_sessions = sorted(sessions_by_day.get(day, []), key=lambda s: s.get("startTime") or "")
            MAX_STRIPS = 3
            for session in day_sessions[:MAX_STRIPS]:
                pill_holder = tk.Frame(cell, bg=cell_bg)
                pill_holder.pack(fill="x", padx=5, pady=1)
                pill_canvas = tk.Canvas(
                    pill_holder, height=16, width=1, highlightthickness=0, bg=cell_bg,
                )
                pill_canvas.pack(fill="x")

                def draw_pill(canvas=pill_canvas, s=session):
                    canvas.delete("all")
                    canvas.update_idletasks()
                    w = max(canvas.winfo_width(), 40)
                    title = _session_title(s)
                    max_chars = max(4, int(w / 6.2))
                    if len(title) > max_chars:
                        title = title[: max_chars - 1].rstrip() + "…"
                    color = _session_color(s)
                    rw.draw_pill(
                        canvas, 0, 0, w, 16, fill=color, text=title,
                        text_fill=_contrasting_text_color(color), font=(FONT, 7),
                    )

                pill_canvas.bind("<Configure>", lambda e, fn=draw_pill: fn())
                pill_canvas.bind(
                    "<Button-1>",
                    lambda e, d=day, s=session: (_state.update(selected_date=d), render_month(), render_day(), _open_session_detail(win, s))[-1],
                )

            if len(day_sessions) > MAX_STRIPS:
                more_label = tk.Label(
                    cell, text=f"+{len(day_sessions) - MAX_STRIPS} more", anchor="w",
                    bg=cell_bg, fg=THEME["text_muted"], font=(FONT, 7),
                )
                more_label.pack(fill="x", padx=6, pady=(0, 3))
                more_label.bind("<Button-1>", lambda e, d=day: on_click(d))

    grid_cells.pack(fill="both", expand=True)

    def prev_month():
        c = month_state["cursor"]
        month_state["cursor"] = (c.replace(day=1) - timedelta(days=1)).replace(day=1)
        render_month()

    def next_month():
        c = month_state["cursor"]
        days_in_month = calendar_module.monthrange(c.year, c.month)[1]
        month_state["cursor"] = (c + timedelta(days=days_in_month)).replace(day=1)
        render_month()

    rw.RoundedButton(
        nav_frame, "‹", command=prev_month, bg=THEME["button_secondary"],
        hover_bg=THEME["button_secondary_hover"], fg=THEME["text"], font=(FONT, 11),
        width=30, height=30, radius=8, parent_bg=THEME["bg"],
    ).pack(side="left", padx=(0, 4))
    rw.RoundedButton(
        nav_frame, "›", command=next_month, bg=THEME["button_secondary"],
        hover_bg=THEME["button_secondary_hover"], fg=THEME["text"], font=(FONT, 11),
        width=30, height=30, radius=8, parent_bg=THEME["bg"],
    ).pack(side="left")
    rw.RoundedButton(
        nav_frame, "Today",
        command=lambda: _jump_today(month_state, render_month_cb=render_month, render_day_cb=lambda: render_day()),
        bg=THEME["button_secondary"], hover_bg=THEME["button_secondary_hover"], fg=THEME["text"],
        font=(FONT, 9), radius=8, parent_bg=THEME["bg"],
    ).pack(side="left", padx=(10, 0))
    rw.RoundedButton(
        nav_frame, "View Full Log…",
        command=history_gui.open_history_viewer,
        bg=THEME["button_secondary"], hover_bg=THEME["button_secondary_hover"], fg=THEME["text"],
        font=(FONT, 9), radius=8, parent_bg=THEME["bg"],
    ).pack(side="left", padx=(14, 0))

    # --- day schedule (bottom pane) ---
    day_header = tk.Frame(day_frame, bg=THEME["bg"])
    day_header.pack(fill="x", pady=(4, 8))
    day_title = tk.Label(day_header, font=(FONT, 13, "bold"), bg=THEME["bg"], fg=THEME["text"])
    day_title.pack(side="left")

    day_canvas_container = tk.Frame(day_frame, bg=THEME["bg"])
    day_canvas_container.pack(fill="both", expand=True)
    day_canvas = tk.Canvas(day_canvas_container, highlightthickness=0, bg=THEME["bg"])
    day_scrollbar = tk.Scrollbar(day_canvas_container, orient="vertical", command=day_canvas.yview)
    day_canvas.configure(yscrollcommand=day_scrollbar.set)
    day_canvas.pack(side="left", fill="both", expand=True)
    day_scrollbar.pack(side="right", fill="y")

    HOUR_HEIGHT = 52
    LABEL_WIDTH = 60
    EVENT_LEFT_MARGIN = 10
    EVENT_RIGHT_EDGE = 560
    MIN_BLOCK_HEIGHT = 16
    MIN_BLOCK_DURATION = timedelta(minutes=(MIN_BLOCK_HEIGHT / HOUR_HEIGHT) * 60)
    day_view_state = {"last_date": None}

    def render_day():
        day_canvas.delete("all")
        selected = _state["selected_date"]
        day_title.config(text=selected.strftime("%A, %B %d, %Y"))

        total_height = HOUR_HEIGHT * 24
        day_canvas.configure(scrollregion=(0, 0, 600, total_height))

        for hour in range(24):
            y = hour * HOUR_HEIGHT
            label = datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")
            day_canvas.create_line(LABEL_WIDTH - 8, y, 2000, y, fill=THEME["grid_line"])
            day_canvas.create_text(
                LABEL_WIDTH - 14, y + 2, anchor="ne", text=label,
                font=(FONT, 8), fill=THEME["text_muted"],
            )

        range_start = datetime.combine(selected, datetime.min.time())

        day_sessions = []
        for session in matching_sessions():
            start = _parse_session_dt(session.get("startTime"))
            if start and start.date() == selected:
                end = _parse_session_dt(session.get("endTime")) or start
                day_sessions.append((start, end, session))

        block_x0 = LABEL_WIDTH + EVENT_LEFT_MARGIN
        for occ_start, occ_end, session, col, cols in _layout_day_blocks(day_sessions, min_duration=MIN_BLOCK_DURATION):
            start_minutes = max(0, (occ_start - range_start).total_seconds() / 60)
            end_minutes = min(24 * 60, (occ_end - range_start).total_seconds() / 60)
            y0 = (start_minutes / 60) * HOUR_HEIGHT
            y1 = (end_minutes / 60) * HOUR_HEIGHT
            y1 = max(y1, y0 + MIN_BLOCK_HEIGHT)
            col_width = (EVENT_RIGHT_EDGE - block_x0) / cols
            gap = 4 if cols > 1 else 0
            x0 = block_x0 + col * col_width
            x1 = x0 + col_width - gap
            color = _session_color(session)
            rect = rw.draw_rounded_rect(
                day_canvas, x0, y0 + 1, x1, y1 - 1, radius=6,
                fill=color, outline="",
            )
            duration = _format_duration_short(int((occ_end - occ_start).total_seconds()))
            title = _session_title(session)
            start_time_only = occ_start.strftime("%I:%M%p").lstrip("0")
            label_font = (FONT, 9)
            label_text = _fit_block_label(
                label_font, (x1 - x0) - 16,
                f"{title}  ·  {duration}", title, start_time_only,
            )
            text = day_canvas.create_text(
                x0 + 8, (y0 + y1) / 2, anchor="w", text=label_text,
                fill=_contrasting_text_color(color), font=label_font,
            )
            for item in (rect, text):
                day_canvas.tag_bind(
                    item, "<Button-1>",
                    lambda e, s=session: _open_session_detail(win, s),
                )

        if day_view_state["last_date"] != selected:
            day_view_state["last_date"] = selected
            _scroll_to_current_hour(day_canvas, HOUR_HEIGHT, total_height)

    def _jump_today(month_state, render_month_cb, render_day_cb):
        month_state["cursor"] = date.today().replace(day=1)
        _state["selected_date"] = date.today()
        render_month_cb()
        render_day_cb()

    def refresh_all():
        render_month()
        render_day()

    search_var.trace_add("write", lambda *_: refresh_all())
    _state["refresh_callbacks"].append(refresh_all)

    render_month()
    render_day()


def _format_duration_short(total_seconds):
    minutes, seconds = divmod(max(0, total_seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s"
    return f"{seconds}s"


def _register_last_session_widget(parent):
    label = tk.Label(
        parent, font=(FONT, 9), fg=THEME["text_muted"], bg=parent.cget("bg"),
        justify="left", anchor="w",
    )
    label.pack(fill="x")

    def refresh():
        if not label.winfo_exists():
            return
        sessions = session_history.load_all()
        if not sessions:
            label.config(text="No finished sessions yet.")
            return
        last = sessions[-1]
        start = _parse_session_dt(last.get("startTime"))
        end = _parse_session_dt(last.get("endTime"))
        when = start.strftime("%a %I:%M %p").replace(" 0", " ") if start else "?"
        duration = _format_duration_short(int((end - start).total_seconds())) if start and end else "?"
        label.config(text=f"Last session: {_session_title(last)} — {when} ({duration})")

    _state["refresh_callbacks"].append(refresh)


def _open_session_detail(win, session):
    detail = tk.Toplevel(win)
    detail.title("Focus Session Details")
    detail.geometry("560x480")

    text_frame = tk.Frame(detail)
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

    # Reuses history_gui's per-session formatting rather than re-implementing
    # violation/whitelist-addition rendering a second time here.
    history_gui._write_session(text, session)
    text.config(state="disabled")


def _layout_day_blocks(items, min_duration=timedelta(0)):
    """Assigns each (start, end, payload) interval a (column, column_count)
    pair so overlapping blocks in a day-schedule canvas are drawn side by
    side, narrowed to fit, instead of full-width and stacked directly on
    top of one another. Returns items in start-sorted order with the two
    extra fields appended.

    min_duration should match the caller's rendered minimum block height
    (converted to a time span) — a short event's *drawn* box is clamped to
    that minimum height, so two short events sitting close together (but not
    technically overlapping by their raw start/end) can still collide once
    rendered. Collision detection here uses each interval's end stretched
    out to at least min_duration so the column split matches what actually
    gets drawn; the true (start, end) is still what's returned."""
    items = sorted(items, key=lambda t: t[0])
    active = []  # (effective_end, column) for intervals still "open" at the current point
    columns = [0] * len(items)
    clusters = []
    cluster_indices = []

    for i, (start, end, _payload) in enumerate(items):
        effective_end = max(end, start + min_duration)
        active = [a for a in active if a[0] > start]
        if not active and cluster_indices:
            clusters.append(cluster_indices)
            cluster_indices = []
        used = {col for _end2, col in active}
        col = 0
        while col in used:
            col += 1
        columns[i] = col
        active.append((effective_end, col))
        cluster_indices.append(i)
    if cluster_indices:
        clusters.append(cluster_indices)

    column_counts = [1] * len(items)
    for cluster in clusters:
        count = max(columns[i] for i in cluster) + 1
        for i in cluster:
            column_counts[i] = count

    return [
        (items[i][0], items[i][1], items[i][2], columns[i], column_counts[i])
        for i in range(len(items))
    ]


def _scroll_to_current_hour(canvas, hour_height, total_height):
    canvas.update_idletasks()
    target_y = max(0, datetime.now().hour - 1) * hour_height
    fraction = min(1.0, max(0.0, target_y / total_height)) if total_height else 0
    canvas.yview_moveto(fraction)


@functools.lru_cache(maxsize=None)
def _get_font(font_spec):
    return tkfont.Font(font=font_spec)


def _truncate_to_width(text, font_obj, max_width):
    """Longest prefix of text + an ellipsis that still measures within
    max_width, found by binary search over prefix length (font metrics
    aren't monospace, so this can't be a cheap char-count estimate)."""
    ellipsis = "…"
    if font_obj.measure(ellipsis) > max_width:
        return ""
    lo, hi = 0, len(text)
    best = ellipsis
    while lo <= hi:
        mid = (lo + hi) // 2
        candidate = text[:mid].rstrip() + ellipsis
        if font_obj.measure(candidate) <= max_width:
            best = candidate
            lo = mid + 1
        else:
            hi = mid - 1
    return best


def _fit_block_label(font_spec, max_width, *candidates):
    """Picks the first (most-detailed) candidate string that fits max_width
    as measured by the actual font, falling back to shorter candidates and
    finally to a truncated-with-ellipsis version of the last one — so a day-
    view block's text is always fully contained within its own column,
    never wrapping into extra lines that could bleed into the block below
    or beside it."""
    if max_width <= 4:
        return ""
    font_obj = _get_font(font_spec)
    for text in candidates:
        if font_obj.measure(text) <= max_width:
            return text
    return _truncate_to_width(candidates[-1], font_obj, max_width)


def _contrasting_text_color(hex_color):
    """Plain-white or plain-black label text over an arbitrary event color
    swatch, picked by relative luminance so the month-grid strips and
    day-view blocks stay readable regardless of which palette color (or
    custom colorchooser pick) an event uses."""
    try:
        hex_color = hex_color.lstrip("#")
        r, g, b = (int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
        luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
        return "black" if luminance > 0.6 else "white"
    except Exception:
        return "white"


def _register_next_up_widget(parent):
    # bg matches whatever container it's dropped into (Calendar tab passes
    # THEME["bg"], Focus tab hasn't been restyled yet) so this never shows
    # up as a mismatched gray box against either.
    label = tk.Label(
        parent, font=(FONT, 9), fg=THEME["text_muted"], bg=parent.cget("bg"),
        justify="left", anchor="w",
    )
    label.pack(fill="x")

    def refresh():
        if not label.winfo_exists():
            return
        events = store.list_events()
        upcoming = recurrence.next_occurrences(events, datetime.now(), count=2)
        if not upcoming:
            label.config(text="No upcoming events.")
            return
        lines = []
        for occ_start, _occ_end, event in upcoming:
            lines.append(f"Next up: {event['title']} — {occ_start.strftime('%a %I:%M %p').replace(' 0', ' ')}")
        label.config(text="\n".join(lines))

    _state["refresh_callbacks"].append(refresh)
    refresh()


def _refresh_all_calendar_views():
    for cb in list(_state["refresh_callbacks"]):
        try:
            cb()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Event editor
# ---------------------------------------------------------------------------

def open_event_editor(root_win, event_id=None, initial_date=None):
    existing = store.get_event(event_id) if event_id else None
    _build_event_editor(root_win, existing, initial_date)


def _build_event_editor(root_win, existing, initial_date):
    win = tk.Toplevel(root_win)
    win.title("Edit Event" if existing else "New Event")
    win.geometry("460x720")
    win.attributes("-topmost", True)

    scroll_container = tk.Frame(win)
    scroll_container.pack(fill="both", expand=True)
    canvas = tk.Canvas(scroll_container, highlightthickness=0)
    scrollbar = tk.Scrollbar(scroll_container, orient="vertical", command=canvas.yview)
    form = tk.Frame(canvas)
    form.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=form, anchor="nw", width=440)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    default_start = datetime.combine(initial_date or date.today(), datetime.min.time()).replace(hour=9)
    default_end = default_start + timedelta(hours=1)
    if existing:
        default_start = datetime.fromisoformat(existing["start"])
        default_end = datetime.fromisoformat(existing["end"])

    tk.Label(form, text="Title", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(12, 0))
    title_var = tk.StringVar(master=win, value=existing["title"] if existing else "")
    tk.Entry(form, textvariable=title_var).pack(fill="x", padx=12)

    all_day_var = tk.BooleanVar(master=win, value=existing["allDay"] if existing else False)
    tk.Checkbutton(form, text="All day", variable=all_day_var).pack(anchor="w", padx=12, pady=(6, 0))

    tk.Label(form, text="Start (YYYY-MM-DD HH:MM)", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
    start_var = tk.StringVar(master=win, value=default_start.strftime("%Y-%m-%d %H:%M"))
    tk.Entry(form, textvariable=start_var).pack(fill="x", padx=12)

    tk.Label(form, text="End (YYYY-MM-DD HH:MM)", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
    end_var = tk.StringVar(master=win, value=default_end.strftime("%Y-%m-%d %H:%M"))
    tk.Entry(form, textvariable=end_var).pack(fill="x", padx=12)

    tk.Label(form, text="Color", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
    color_var = tk.StringVar(master=win, value=existing["color"] if existing else COLOR_PALETTE[0])
    palette_frame = tk.Frame(form)
    palette_frame.pack(fill="x", padx=12, pady=(2, 0))
    swatch_buttons = {}

    def set_color(c):
        color_var.set(c)
        for hexval, btn in swatch_buttons.items():
            btn.configure(relief="sunken" if hexval == c else "raised")

    for c in COLOR_PALETTE:
        b = tk.Button(palette_frame, bg=c, width=2, command=lambda c=c: set_color(c))
        b.pack(side="left", padx=2)
        swatch_buttons[c] = b
    set_color(color_var.get())

    def pick_custom_color():
        rgb, hexval = colorchooser.askcolor(title="Custom event color")
        if hexval:
            set_color(hexval)

    tk.Button(palette_frame, text="Custom…", command=pick_custom_color).pack(side="left", padx=(8, 0))

    tk.Label(form, text="Notes", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(8, 0))
    notes_text = tk.Text(form, height=3)
    notes_text.pack(fill="x", padx=12)
    if existing:
        notes_text.insert("1.0", existing.get("notes", ""))

    # --- recurrence ---
    tk.Label(form, text="Repeats", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
    recur_frame = tk.Frame(form)
    recur_frame.pack(fill="x", padx=12)

    RECUR_LABELS = {
        "none": "Does not repeat", "daily": "Daily", "weekly": "Weekly",
        "weekly_days": "Weekly on selected days", "monthly": "Monthly",
        "yearly": "Yearly", "custom": "Custom (every N days/weeks)",
    }
    recur_kind_var = tk.StringVar(master=win, value="none")
    recur_interval_var = tk.StringVar(master=win, value="1")
    recur_unit_var = tk.StringVar(master=win, value="weeks")
    weekday_vars = {code: tk.BooleanVar(master=win, value=False) for code in recurrence.WEEKDAY_CODES}

    if existing and existing.get("rrule"):
        _prefill_recurrence_from_rrule(existing["rrule"], recur_kind_var, recur_interval_var, recur_unit_var, weekday_vars)

    recur_menu = ttk.Combobox(
        recur_frame, textvariable=tk.StringVar(master=win, value=RECUR_LABELS[recur_kind_var.get()]),
        values=list(RECUR_LABELS.values()), state="readonly", width=28,
    )
    recur_menu.pack(anchor="w")

    label_to_kind = {v: k for k, v in RECUR_LABELS.items()}
    weekday_row = tk.Frame(form)
    interval_row = tk.Frame(form)

    def on_recur_change(*_):
        kind = label_to_kind[recur_menu.get()]
        recur_kind_var.set(kind)
        weekday_row.pack_forget()
        interval_row.pack_forget()
        if kind == "weekly_days":
            weekday_row.pack(fill="x", padx=12, pady=(4, 0))
        if kind in ("custom",):
            interval_row.pack(fill="x", padx=12, pady=(4, 0))

    recur_menu.bind("<<ComboboxSelected>>", on_recur_change)
    recur_menu.set(RECUR_LABELS[recur_kind_var.get()])

    for code in recurrence.WEEKDAY_CODES:
        tk.Checkbutton(weekday_row, text=code, variable=weekday_vars[code]).pack(side="left")

    tk.Label(interval_row, text="Every").pack(side="left")
    tk.Entry(interval_row, textvariable=recur_interval_var, width=4).pack(side="left", padx=4)
    tk.OptionMenu(interval_row, recur_unit_var, "days", "weeks").pack(side="left")

    on_recur_change()

    # --- reminders ---
    tk.Label(form, text="Reminders", font=("Segoe UI", 9, "bold")).pack(anchor="w", padx=12, pady=(10, 0))
    reminders_list = list(existing.get("reminderOffsets", [])) if existing else []
    reminders_frame = tk.Frame(form)
    reminders_frame.pack(fill="x", padx=12)
    reminders_listbox = tk.Listbox(reminders_frame, height=4)
    reminders_listbox.pack(side="left", fill="x", expand=True)

    def refresh_reminders_listbox():
        reminders_listbox.delete(0, "end")
        for offset in reminders_list:
            label = next((lbl for lbl, off in REMINDER_PRESETS if off == offset), f"{offset} min before")
            reminders_listbox.insert("end", label)

    refresh_reminders_listbox()

    def remove_selected_reminder():
        sel = reminders_listbox.curselection()
        if sel:
            del reminders_list[sel[0]]
            refresh_reminders_listbox()

    reminder_controls = tk.Frame(form)
    reminder_controls.pack(fill="x", padx=12, pady=(4, 0))
    reminder_preset_var = tk.StringVar(master=win, value=REMINDER_PRESETS[1][0])
    ttk.Combobox(
        reminder_controls, textvariable=reminder_preset_var,
        values=[lbl for lbl, _ in REMINDER_PRESETS], state="readonly", width=20,
    ).pack(side="left")

    def add_preset_reminder():
        offset = dict(REMINDER_PRESETS)[reminder_preset_var.get()]
        if offset not in reminders_list:
            reminders_list.append(offset)
            refresh_reminders_listbox()

    tk.Button(reminder_controls, text="Add", command=add_preset_reminder).pack(side="left", padx=4)
    tk.Button(reminder_controls, text="Remove selected", command=remove_selected_reminder).pack(side="left")

    custom_reminder_row = tk.Frame(form)
    custom_reminder_row.pack(fill="x", padx=12, pady=(4, 0))
    custom_minutes_var = tk.StringVar(master=win)
    tk.Entry(custom_reminder_row, textvariable=custom_minutes_var, width=8).pack(side="left")
    tk.Label(custom_reminder_row, text="custom minutes before").pack(side="left", padx=(4, 0))

    def add_custom_reminder():
        try:
            minutes = int(custom_minutes_var.get())
        except ValueError:
            return
        if minutes >= 0 and minutes not in reminders_list:
            reminders_list.append(minutes)
            refresh_reminders_listbox()
        custom_minutes_var.set("")

    tk.Button(custom_reminder_row, text="Add", command=add_custom_reminder).pack(side="left", padx=4)

    # --- focus integration ---
    tk.Frame(form, height=1, bg="#ccc").pack(fill="x", padx=12, pady=10)
    existing_focus = existing.get("focusProfile") if existing else None
    focus_enabled_var = tk.BooleanVar(master=win, value=bool(existing_focus and existing_focus.get("enabled")))
    tk.Checkbutton(
        form, text="Integrate with Focus Timer", font=("Segoe UI", 9, "bold"), variable=focus_enabled_var,
        command=lambda: toggle_focus_subscreen(),
    ).pack(anchor="w", padx=12)

    focus_subscreen = tk.Frame(form, highlightbackground="#ddd", highlightthickness=1)

    lock_mode_var = tk.StringVar(master=win, value=(existing_focus or {}).get("lockMode", "soft"))
    lock_frame = tk.Frame(focus_subscreen)
    lock_frame.pack(fill="x", padx=10, pady=(10, 4))
    tk.Label(lock_frame, text="Lock mode:").pack(side="left")
    tk.Radiobutton(lock_frame, text="Soft", variable=lock_mode_var, value="soft").pack(side="left", padx=6)
    tk.Radiobutton(lock_frame, text="Hard", variable=lock_mode_var, value="hard").pack(side="left")

    tk.Label(focus_subscreen, text="Process whitelist (for this event)", font=("Segoe UI", 8, "bold")).pack(
        anchor="w", padx=10, pady=(6, 0)
    )
    apps = installed_apps.list_installed_apps()
    # An event that already has its own focus profile keeps exactly what was
    # saved for it — but a brand-new "Integrate with Focus Timer" toggle
    # (no per-event profile saved yet) defaults to the same apps as the
    # global whitelist (config.json), so the common case of "whitelist my
    # usual apps" doesn't mean re-checking every app for every event.
    if existing_focus:
        default_processes = existing_focus.get("processWhitelist", [])
    else:
        default_processes = config.load_config().get("processWhitelist", [])
    existing_process_set = {p.lower() for p in default_processes}
    process_container, process_vars, process_add_row = checklist_widget.build_checklist(
        focus_subscreen, apps, existing_process_set,
        key_fn=lambda a: a["process_name"], label_fn=lambda a: f"{a['display_name']} ({a['process_name']})",
    )
    process_container.pack(fill="x", padx=10, pady=(2, 0))

    # build_checklist only creates a row per *item* it's handed (the
    # installed-apps scan) — a previously saved process that the scan never
    # finds (a manually-typed exe with no Start Menu shortcut, or one that's
    # since been uninstalled) would otherwise have no checkbox at all, so
    # get_checked() at Save time would silently drop it even though the
    # checkbox for it was never unchecked — it just never existed. Add it
    # back explicitly, pre-checked, so a saved whitelist entry can only ever
    # be removed by an explicit uncheck/manual action, never lost on reopen.
    scanned_lower = {a["process_name"].lower() for a in apps}
    for process_name in default_processes:
        if process_name.lower() not in scanned_lower:
            process_add_row(process_name, process_name, checked=True)

    process_manual_row = tk.Frame(focus_subscreen)
    process_manual_row.pack(fill="x", padx=10, pady=(2, 6))
    process_manual_var = tk.StringVar(master=win)
    tk.Entry(process_manual_row, textvariable=process_manual_var, width=22).pack(side="left")

    def add_manual_process():
        name = os.path.basename(process_manual_var.get().strip())
        if name.lower().endswith(".exe"):
            process_add_row(name, name, checked=True)
            process_manual_var.set("")

    tk.Button(process_manual_row, text="Add", command=add_manual_process).pack(side="left", padx=4)

    tk.Label(focus_subscreen, text="Domain whitelist (for this event, sent to the browser extension)",
             font=("Segoe UI", 8, "bold")).pack(anchor="w", padx=10, pady=(4, 0))
    # Domains offered here come from config.json's global domainWhitelist —
    # the same field the browser extension can now read/write directly via
    # GET/POST /whitelist/domains (api_server.py) — unioned with whatever's
    # already saved on this event, so a domain the extension knows about
    # shows up as a pickable option without retyping it, the same way the
    # process whitelist above defaults from config.json's processWhitelist.
    global_domains = config.load_config().get("domainWhitelist", [])
    existing_domains = list((existing_focus or {}).get("domainWhitelist", []))
    existing_domains_lower = {d.lower() for d in existing_domains}
    domain_items = list(existing_domains)
    for domain in global_domains:
        if domain.lower() not in existing_domains_lower:
            domain_items.append(domain)
    # New focus profile (no per-event domains saved yet) defaults to
    # whatever's globally whitelisted, same "reuse my usual picks" default as
    # the process whitelist; an event with its own saved profile keeps
    # exactly what was checked for it.
    domain_checked = existing_domains_lower if existing_focus else {d.lower() for d in global_domains}
    domain_container, domain_vars, domain_add_row = checklist_widget.build_checklist(
        focus_subscreen, domain_items, domain_checked,
    )
    domain_container.pack(fill="x", padx=10, pady=(2, 0))

    domain_manual_row = tk.Frame(focus_subscreen)
    domain_manual_row.pack(fill="x", padx=10, pady=(2, 6))
    domain_manual_var = tk.StringVar(master=win)
    tk.Entry(domain_manual_row, textvariable=domain_manual_var, width=22).pack(side="left")

    def add_manual_domain():
        domain = domain_manual_var.get().strip()
        if domain:
            domain_add_row(domain, domain, checked=True)
            domain_manual_var.set("")

    tk.Button(domain_manual_row, text="Add", command=add_manual_domain).pack(side="left", padx=4)

    warn_row = tk.Frame(focus_subscreen)
    warn_row.pack(fill="x", padx=10, pady=(4, 10))
    warn_enabled_var = tk.BooleanVar(
        master=win, value=(existing_focus or {}).get("warningMinutes") is not None if existing_focus else True
    )
    warn_minutes_var = tk.StringVar(
        master=win, value=str((existing_focus or {}).get("warningMinutes", 5) if existing_focus else 5)
    )
    tk.Checkbutton(warn_row, text="Warn", variable=warn_enabled_var).pack(side="left")
    tk.Entry(warn_row, textvariable=warn_minutes_var, width=4).pack(side="left", padx=4)
    tk.Label(warn_row, text="minute(s) before start").pack(side="left")

    def toggle_focus_subscreen():
        if focus_enabled_var.get():
            focus_subscreen.pack(fill="x", padx=12, pady=(4, 0))
        else:
            focus_subscreen.pack_forget()

    toggle_focus_subscreen()

    status_label = tk.Label(form, text="", fg="#c62828", font=("Segoe UI", 9), wraplength=420)
    status_label.pack(fill="x", padx=12, pady=(8, 0))

    button_row = tk.Frame(form)
    button_row.pack(fill="x", padx=12, pady=16)

    def save():
        title = title_var.get().strip()
        if not title:
            status_label.config(text="Title is required.")
            return
        try:
            start_dt = datetime.strptime(start_var.get().strip(), "%Y-%m-%d %H:%M")
            end_dt = datetime.strptime(end_var.get().strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            status_label.config(text="Start/End must be in YYYY-MM-DD HH:MM format.")
            return
        if end_dt <= start_dt:
            status_label.config(text="End must be after start.")
            return

        kind = recur_kind_var.get()
        if kind == "custom":
            try:
                interval = int(recur_interval_var.get())
            except ValueError:
                interval = 1
            base_kind = "daily" if recur_unit_var.get() == "days" else "weekly"
            rrule_str = recurrence.build_rrule(base_kind, interval=interval)
        elif kind == "weekly_days":
            selected_days = [code for code, var in weekday_vars.items() if var.get()]
            rrule_str = recurrence.build_rrule("weekly_days", interval=1, weekdays=selected_days)
        elif kind == "none":
            rrule_str = None
        else:
            rrule_str = recurrence.build_rrule(kind, interval=1)

        focus_profile = None
        if focus_enabled_var.get():
            try:
                warn_minutes = int(warn_minutes_var.get()) if warn_enabled_var.get() else None
            except ValueError:
                warn_minutes = None
            focus_profile = {
                "enabled": True,
                "lockMode": lock_mode_var.get(),
                "processWhitelist": checklist_widget.get_checked(process_vars),
                "domainWhitelist": checklist_widget.get_checked(domain_vars),
                "warningMinutes": warn_minutes,
            }

        event = {
            "id": existing["id"] if existing else None,
            "title": title,
            "start": start_dt.isoformat(),
            "end": end_dt.isoformat(),
            "allDay": all_day_var.get(),
            "color": color_var.get(),
            "notes": notes_text.get("1.0", "end").strip(),
            "rrule": rrule_str,
            "reminderOffsets": list(reminders_list),
            "focusProfile": focus_profile,
        }
        saved_id = store.save_event(event)
        if saved_id is None:
            status_label.config(text="Failed to save — see calendar_errors.log.")
            return
        win.destroy()
        _refresh_all_calendar_views()

    def delete():
        if not existing:
            win.destroy()
            return
        if not messagebox.askyesno("Delete event", f"Delete '{existing['title']}'?"):
            return
        store.soft_delete_event(existing["id"])
        win.destroy()
        _refresh_all_calendar_views()
        _show_undo_toast(root_win, existing["id"], existing["title"])

    tk.Button(button_row, text="Save", command=save, width=10).pack(side="left")
    if existing:
        tk.Button(button_row, text="Delete", command=delete, width=10, fg="#c62828").pack(side="left", padx=6)
    tk.Button(button_row, text="Cancel", command=win.destroy, width=10).pack(side="left")


def _prefill_recurrence_from_rrule(rrule_str, kind_var, interval_var, unit_var, weekday_vars):
    try:
        parts = dict(p.split("=") for p in rrule_str.split(";"))
        freq = parts.get("FREQ", "").lower()
        interval = int(parts.get("INTERVAL", 1))
        if "BYDAY" in parts:
            kind_var.set("weekly_days")
            for code in parts["BYDAY"].split(","):
                if code in weekday_vars:
                    weekday_vars[code].set(True)
        elif interval > 1 and freq in ("daily", "weekly"):
            kind_var.set("custom")
            interval_var.set(str(interval))
            unit_var.set("days" if freq == "daily" else "weeks")
        elif freq in ("daily", "weekly", "monthly", "yearly"):
            kind_var.set(freq)
    except Exception:
        pass


def _show_undo_toast(root_win, event_id, title):
    win = tk.Toplevel(root_win)
    win.overrideredirect(True)
    win.attributes("-topmost", True)
    win.configure(bg="#1e1e1e")
    width, height = 320, 60
    screen_width = win.winfo_screenwidth()
    x = screen_width - width - 24
    y = 24
    win.geometry(f"{width}x{height}+{x}+{y}")

    tk.Label(
        win, text=f"Deleted \"{title}\"", bg="#1e1e1e", fg="white", font=("Segoe UI", 9),
    ).pack(side="left", padx=12, pady=14)

    def undo():
        store.undo_delete_event(event_id)
        _refresh_all_calendar_views()
        win.destroy()

    tk.Button(win, text="Undo", command=undo, bg="#3a3a3a", fg="white", relief="flat").pack(
        side="left", padx=8
    )

    win.after(10000, lambda: win.destroy() if win.winfo_exists() else None)


# ---------------------------------------------------------------------------
# Backup / restore
# ---------------------------------------------------------------------------

def _open_backup_dialog(root_win):
    win = tk.Toplevel(root_win)
    win.title("Backup / Restore Calendar")
    win.geometry("320x140")
    win.attributes("-topmost", True)

    status_label = tk.Label(win, text="", font=("Segoe UI", 9))
    status_label.pack(pady=(6, 0))

    def do_export():
        path = filedialog.asksaveasfilename(
            title="Export calendar.db", defaultextension=".db",
            filetypes=[("SQLite database", "*.db")],
        )
        if not path:
            return
        ok = store.export_db(path)
        status_label.config(text="Exported." if ok else "Export failed — see calendar_errors.log.")

    def do_import():
        path = filedialog.askopenfilename(
            title="Import calendar.db", filetypes=[("SQLite database", "*.db"), ("All files", "*.*")],
        )
        if not path:
            return
        if not messagebox.askyesno("Import", "This replaces all current calendar data. Continue?"):
            return
        ok = store.import_db(path)
        status_label.config(text="Imported." if ok else "Import failed — see calendar_errors.log.")
        if ok:
            _refresh_all_calendar_views()

    tk.Button(win, text="Export calendar.db…", command=do_export, width=24).pack(pady=8)
    tk.Button(win, text="Import calendar.db…", command=do_import, width=24).pack(pady=4)
