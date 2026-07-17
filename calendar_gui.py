"""Main window: a persistent left-sidebar + tabbed (Calendar / Focus) window
opened by a single click on the tray icon, replacing the old "tray icon as a
standalone focus toggle" behavior.

Built as one Toplevel on the single shared GUI-thread root (gui_thread.py),
same as every other popup in this app (picker_gui's dialogs, enforcer's lock
overlays) — never a second Tk(). A module-level singleton reference means a
repeat tray click lifts the existing window instead of spawning another one.
"""
import calendar as calendar_module
import os
import tkinter as tk
from datetime import date, datetime, timedelta
from tkinter import colorchooser, filedialog, messagebox, ttk

import calendar_recurrence as recurrence
import calendar_store as store
import checklist_widget
import gui_thread
import history_gui
import installed_apps
import picker_gui
import session_manager

COLOR_PALETTE = [
    "#2d8cff", "#e53935", "#43a047", "#fb8c00", "#8e24aa",
    "#00acc1", "#f4511e", "#3949ab", "#6d4c41", "#546e7a",
]

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
    win.geometry("880x680")
    win.minsize(680, 520)

    sidebar = tk.Frame(win, width=140, bg="#20242c")
    sidebar.pack(side="left", fill="y")
    sidebar.pack_propagate(False)

    content = tk.Frame(win)
    content.pack(side="left", fill="both", expand=True)

    calendar_frame = tk.Frame(content)
    focus_frame = tk.Frame(content)
    for frame in (calendar_frame, focus_frame):
        frame.place(x=0, y=0, relwidth=1, relheight=1)

    tk.Label(
        sidebar, text="Carmen Focus", bg="#20242c", fg="white",
        font=("Segoe UI", 12, "bold"), pady=16,
    ).pack(fill="x")

    tab_buttons = {}

    def show_tab(name):
        for n, btn in tab_buttons.items():
            btn.configure(bg="#2d3340" if n == name else "#20242c")
        (calendar_frame if name == "calendar" else focus_frame).tkraise()

    tab_buttons["calendar"] = tk.Button(
        sidebar, text="📅 Calendar", anchor="w", bd=0, fg="white", bg="#20242c",
        activebackground="#2d3340", activeforeground="white", padx=14, pady=10,
        command=lambda: show_tab("calendar"),
    )
    tab_buttons["focus"] = tk.Button(
        sidebar, text="🎯 Focus", anchor="w", bd=0, fg="white", bg="#20242c",
        activebackground="#2d3340", activeforeground="white", padx=14, pady=10,
        command=lambda: show_tab("focus"),
    )
    tab_buttons["calendar"].pack(fill="x")
    tab_buttons["focus"].pack(fill="x")

    tk.Frame(sidebar, bg="#20242c").pack(fill="both", expand=True)  # spacer

    tk.Button(
        sidebar, text="Backup / Restore…", anchor="w", bd=0, fg="#cccccc", bg="#20242c",
        activebackground="#2d3340", activeforeground="white", padx=14, pady=8,
        font=("Segoe UI", 8), command=lambda: _open_backup_dialog(win),
    ).pack(fill="x", side="bottom")

    _build_calendar_tab(calendar_frame, win)
    _build_focus_tab(focus_frame, win)

    _state["refresh_callbacks"] = []
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
    header = tk.Frame(parent)
    header.pack(fill="x", padx=16, pady=(16, 4))

    next_up = tk.Frame(parent)
    next_up.pack(fill="x", padx=16, pady=(0, 6))
    _register_next_up_widget(next_up)

    search_var = tk.StringVar(master=win)

    month_state = {"cursor": date.today().replace(day=1)}

    nav_frame = tk.Frame(header)
    nav_frame.pack(side="left")
    month_label = tk.Label(nav_frame, font=("Segoe UI", 13, "bold"))
    month_label.pack(side="left", padx=8)

    search_frame = tk.Frame(header)
    search_frame.pack(side="right")
    tk.Label(search_frame, text="Search:").pack(side="left")
    search_entry = tk.Entry(search_frame, textvariable=search_var, width=20)
    search_entry.pack(side="left", padx=(4, 0))

    body = tk.PanedWindow(parent, orient="vertical", sashrelief="flat", sashwidth=6)
    body.pack(fill="both", expand=True, padx=16, pady=(0, 16))

    month_frame = tk.Frame(body)
    day_frame = tk.Frame(body)
    body.add(month_frame, height=380)
    body.add(day_frame, height=260)

    grid_cells = tk.Frame(month_frame)

    def render_month():
        for child in grid_cells.winfo_children():
            child.destroy()
        cursor = month_state["cursor"]
        month_label.config(text=cursor.strftime("%B %Y"))

        weekday_names = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        for i, name in enumerate(weekday_names):
            tk.Label(grid_cells, text=name, font=("Segoe UI", 9, "bold"), fg="#666").grid(
                row=0, column=i, sticky="nsew", padx=1, pady=1
            )

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
            is_selected = day == _state["selected_date"]
            cell = tk.Frame(
                grid_cells, bg="#e8f0fe" if is_selected else ("white" if in_month else "#f5f5f5"),
                highlightbackground="#ddd", highlightthickness=1,
            )
            cell.grid(row=row + 1, column=col, sticky="nsew", padx=1, pady=1)

            tk.Label(
                cell, text=str(day.day), anchor="ne",
                bg=cell["bg"], fg="black" if in_month else "#aaa",
                font=("Segoe UI", 9),
            ).pack(fill="x")

            for event in occurrences_by_day.get(day, [])[:3]:
                tk.Label(
                    cell, text="●", fg=event["color"], bg=cell["bg"], font=("Segoe UI", 8),
                ).pack(anchor="w", padx=2)

            def on_click(d=day):
                _state["selected_date"] = d
                render_month()
                render_day()

            cell.bind("<Button-1>", lambda e, d=day: on_click(d))
            for child in cell.winfo_children():
                child.bind("<Button-1>", lambda e, d=day: on_click(d))

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

    tk.Button(nav_frame, text="◀", command=prev_month, width=3).pack(side="left")
    tk.Button(nav_frame, text="▶", command=next_month, width=3).pack(side="left")
    tk.Button(nav_frame, text="Today", command=lambda: (_jump_today(month_state, render_month_cb=render_month, render_day_cb=lambda: render_day())),).pack(side="left", padx=(6, 0))
    tk.Button(nav_frame, text="+ New Event", command=lambda: open_event_editor(win, initial_date=_state["selected_date"])).pack(side="left", padx=(12, 0))

    # --- day schedule (bottom pane) ---
    day_header = tk.Frame(day_frame)
    day_header.pack(fill="x")
    day_title = tk.Label(day_header, font=("Segoe UI", 11, "bold"))
    day_title.pack(side="left", pady=4)

    day_canvas_container = tk.Frame(day_frame)
    day_canvas_container.pack(fill="both", expand=True)
    day_canvas = tk.Canvas(day_canvas_container, highlightthickness=0, bg="white")
    day_scrollbar = tk.Scrollbar(day_canvas_container, orient="vertical", command=day_canvas.yview)
    day_canvas.configure(yscrollcommand=day_scrollbar.set)
    day_canvas.pack(side="left", fill="both", expand=True)
    day_scrollbar.pack(side="right", fill="y")

    HOUR_HEIGHT = 48
    LABEL_WIDTH = 56

    def render_day():
        day_canvas.delete("all")
        selected = _state["selected_date"]
        day_title.config(text=selected.strftime("%A, %B %d, %Y"))

        total_height = HOUR_HEIGHT * 24
        day_canvas.configure(scrollregion=(0, 0, 600, total_height))

        for hour in range(24):
            y = hour * HOUR_HEIGHT
            label = datetime(2000, 1, 1, hour).strftime("%I %p").lstrip("0")
            day_canvas.create_line(0, y, 2000, y, fill="#eee")
            day_canvas.create_text(4, y + 2, anchor="nw", text=label, font=("Segoe UI", 8), fill="#888")

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

        for occ_start, occ_end, event in sorted(day_events, key=lambda t: t[0]):
            start_minutes = max(0, (occ_start - range_start).total_seconds() / 60)
            end_minutes = min(24 * 60, (occ_end - range_start).total_seconds() / 60)
            y0 = LABEL_WIDTH and (start_minutes / 60) * HOUR_HEIGHT
            y1 = (end_minutes / 60) * HOUR_HEIGHT
            y1 = max(y1, y0 + 16)
            rect = day_canvas.create_rectangle(
                LABEL_WIDTH, y0, 560, y1, fill=event["color"], outline=""
            )
            label_text = event["title"]
            if event.get("focusProfile") and event["focusProfile"].get("enabled"):
                label_text = "🎯 " + label_text
            text = day_canvas.create_text(
                LABEL_WIDTH + 6, y0 + 2, anchor="nw", text=label_text,
                fill="white", font=("Segoe UI", 9), width=560 - LABEL_WIDTH - 12,
            )
            for item in (rect, text):
                day_canvas.tag_bind(
                    item, "<Button-1>",
                    lambda e, ev=event: open_event_editor(win, event_id=ev["id"]),
                )

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


def _register_next_up_widget(parent):
    label = tk.Label(parent, font=("Segoe UI", 9), fg="#555", justify="left", anchor="w")
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
    existing_process_set = {p.lower() for p in (existing_focus or {}).get("processWhitelist", [])}
    process_container, process_vars, process_add_row = checklist_widget.build_checklist(
        focus_subscreen, apps, existing_process_set,
        key_fn=lambda a: a["process_name"], label_fn=lambda a: f"{a['display_name']} ({a['process_name']})",
    )
    process_container.pack(fill="x", padx=10, pady=(2, 0))

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
    existing_domains = list((existing_focus or {}).get("domainWhitelist", []))
    domain_container, domain_vars, domain_add_row = checklist_widget.build_checklist(
        focus_subscreen, existing_domains, {d.lower() for d in existing_domains},
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
