"""Tkinter windows launched from the tray menu: the app whitelist picker and
the start-session timer dialog. Both are built as Toplevel windows on the
single shared GUI-thread root (gui_thread.py) instead of spinning up their
own Tk()/thread — Tkinter is not thread-safe, and two Tk() roots alive in
different threads at once (e.g. one of these dialogs open while an
enforcement popup fires) crashes the whole process with a fatal Tcl error."""
import os
import tkinter as tk
from tkinter import filedialog, messagebox

import config
import gui_thread
import installed_apps
import session_history
import session_manager


def open_whitelist_picker():
    gui_thread.run_on_gui_thread(_build_whitelist_picker)


def open_timer_dialog():
    gui_thread.run_on_gui_thread(_build_timer_dialog)


def _build_whitelist_picker(root):
    # Mid-session, this picker isn't the "set the whitelist for the next
    # session" editor anymore — it's an "add extras to the running session"
    # tool instead, since overwriting processWhitelist wholesale here would
    # silently rewrite what's currently being enforced out from under an
    # active session. Newly checked apps get whitelisted immediately (via
    # add_process_to_whitelist) but only after a reason is given for each,
    # collected on the follow-up page built by _build_reason_dialog.
    session_active = session_manager.is_active()

    if session_active:
        saved = {name.lower() for name in session_manager.get_status()["processWhitelist"]}
    else:
        saved = {name.lower() for name in config.load_config().get("processWhitelist", [])}
    apps = installed_apps.list_installed_apps()

    win = tk.Toplevel(root)
    win.title("Carmen Focus — Pick Apps to Whitelist")
    win.geometry("440x640")
    win.attributes("-topmost", True)

    if session_active:
        instructions = (
            "A session is active — checked apps below are already allowed.\n"
            "Check any more you want to add. You'll be asked to explain each\n"
            "new one before it's added."
        )
    else:
        instructions = "Check the apps allowed during a focus session.\nPreviously saved picks are pre-checked."

    tk.Label(
        win,
        text=instructions,
        font=("Segoe UI", 10),
        justify="center",
        pady=10,
    ).pack()

    list_container = tk.Frame(win)
    list_container.pack(fill="both", expand=True, padx=10)

    canvas = tk.Canvas(list_container, highlightthickness=0)
    scrollbar = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
    list_frame = tk.Frame(canvas)

    list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=list_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    vars_by_process = {}

    def add_checkbox_row(process_name, label_text, checked):
        # Shared by every source of rows (installed-apps scan, the
        # last-session quick-pick section, and manually typed/browsed
        # entries) so nothing ever gets double-listed — first writer for a
        # given process name (case-insensitive) wins.
        key = process_name.lower()
        if key in {p.lower() for p in vars_by_process}:
            return
        var = tk.BooleanVar(master=win, value=checked)
        tk.Checkbutton(
            list_frame,
            text=label_text,
            variable=var,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")
        vars_by_process[process_name] = var

    # Only offered when picking the whitelist for the *next* session (not
    # mid-session) — a quick way to re-check whatever the previous session
    # actually ended up whitelisting, without having to find each one again
    # in the installed-apps scan below (which can miss anything without a
    # Start Menu shortcut). "Temporary" in the sense that it's just whatever
    # session_history's last entry happens to hold — it naturally reflects
    # a different session once another one completes.
    if not session_active:
        history = session_history.load_all()
        prev_session = history[-1] if history else None
        prev_additions = prev_session.get("processWhitelistAdditions", []) if prev_session else []
        prev_apps = prev_session.get("processWhitelist", []) if prev_session else []

        # Rendered before the plain processWhitelist section below, and with
        # its reason attached — these are specifically the apps that got let
        # in mid-session (via the reason-required flow, either the tray
        # picker or the lock overlay's own "Whitelist" button) rather than
        # picked at session start, so they're worth calling out on their own
        # even though processWhitelist already contains them too.
        if prev_additions:
            tk.Label(
                list_frame, text="Added mid-session last time — quick re-add:",
                font=("Segoe UI", 9, "bold"), anchor="w", fg="#555",
            ).pack(fill="x", anchor="w", pady=(4, 0))
            for addition in prev_additions:
                process_name = addition.get("process")
                if not process_name:
                    continue
                reason = addition.get("reason")
                label = f"{process_name}   — {reason}" if reason else process_name
                add_checkbox_row(process_name, label, checked=False)
            tk.Frame(list_frame, height=1, bg="#ccc").pack(fill="x", pady=6)

        if prev_apps:
            tk.Label(
                list_frame, text="From your last session — quick re-add:",
                font=("Segoe UI", 9, "bold"), anchor="w", fg="#555",
            ).pack(fill="x", anchor="w", pady=(4, 0))
            for process_name in prev_apps:
                add_checkbox_row(process_name, process_name, checked=False)
            tk.Frame(list_frame, height=1, bg="#ccc").pack(fill="x", pady=6)

    if not apps:
        tk.Label(list_frame, text="No installed apps found.", fg="#888").pack(anchor="w", pady=8)
    for app in apps:
        add_checkbox_row(
            app["process_name"],
            f"{app['display_name']}   ({app['process_name']})",
            checked=app["process_name"].lower() in saved,
        )

    manual_frame = tk.Frame(win)
    manual_frame.pack(fill="x", padx=10, pady=(8, 0))
    tk.Label(manual_frame, text="Not listed? Add by name or file:", font=("Segoe UI", 9)).pack(anchor="w")

    manual_row = tk.Frame(manual_frame)
    manual_row.pack(fill="x", pady=(2, 0))
    manual_var = tk.StringVar(master=win)
    manual_entry = tk.Entry(manual_row, textvariable=manual_var)
    manual_entry.pack(side="left", fill="x", expand=True)

    manual_status = tk.Label(manual_frame, text="", font=("Segoe UI", 8), fg="#c62828")
    manual_status.pack(anchor="w")

    def add_manual_entry(process_name):
        # Reduce to just the basename even for a typed (not browsed) entry —
        # is_whitelisted() and enforcement everywhere else compare on
        # process name alone, never a full path, so a typed "C:\...\app.exe"
        # would otherwise pass validation but silently never match the
        # actually-running process.
        process_name = os.path.basename(process_name.strip())
        if not process_name:
            manual_status.config(text="Enter an exe name or browse for a file.")
            return
        if not process_name.lower().endswith(".exe"):
            manual_status.config(text="Process name must end in .exe.")
            return
        add_checkbox_row(process_name, process_name, checked=True)
        manual_status.config(text="", fg="#c62828")
        manual_var.set("")

    def browse_for_exe():
        # Only need the filename to match the running process by, same as
        # everywhere else in this app (is_whitelisted() etc. compare on
        # process_name alone, not a full path) — so basename is all that's
        # kept from whatever path the user picks.
        path = filedialog.askopenfilename(
            title="Pick an executable",
            filetypes=[("Executables", "*.exe"), ("All files", "*.*")],
        )
        if path:
            add_manual_entry(os.path.basename(path))

    tk.Button(manual_row, text="Browse...", command=browse_for_exe).pack(side="left", padx=(6, 0))
    tk.Button(manual_row, text="Add", command=lambda: add_manual_entry(manual_var.get())).pack(
        side="left", padx=(6, 0)
    )
    manual_entry.bind("<Return>", lambda e: add_manual_entry(manual_var.get()))

    status_label = tk.Label(win, text="", font=("Segoe UI", 9), fg="#2e7d32")
    status_label.pack(pady=(6, 0))

    def save():
        selected = [name for name, var in vars_by_process.items() if var.get()]

        if session_active:
            # Only newly checked apps need a reason — apps already on the
            # running session's whitelist are already in effect and have
            # nothing new to log.
            extras = [name for name in selected if name.lower() not in saved]
            if not extras:
                status_label.config(text="No new apps selected — nothing to add.")
                return
            win.destroy()
            # Already running on the shared GUI thread (this is a Tkinter
            # button callback) — no need to go through run_on_gui_thread's
            # queue to open the next page.
            _build_reason_dialog(root, extras)
            return

        current_cfg = config.load_config()
        current_cfg["processWhitelist"] = selected
        config.save_config(current_cfg)
        status_label.config(text=f"Saved {len(selected)} app(s) to the whitelist.")

    button_label = "Add Selected to Session" if session_active else "Save Whitelist"
    tk.Button(win, text=button_label, command=save).pack(pady=12)


def _build_reason_dialog(root, process_names):
    """Second page shown after saving mid-session extras — one reason field
    per newly selected app, all required, before add_process_to_whitelist()
    actually applies any of them. Keeps process_names in the closure rather
    than re-deriving from checkboxes, so this page is a pure "explain what
    you just picked" step."""
    win = tk.Toplevel(root)
    win.title("Carmen Focus — Explain Additions")
    win.geometry("440x480")
    win.attributes("-topmost", True)

    tk.Label(
        win,
        text="Why does each of these need to be added to this session?",
        font=("Segoe UI", 10),
        justify="center",
        wraplength=400,
        pady=10,
    ).pack()

    list_container = tk.Frame(win)
    list_container.pack(fill="both", expand=True, padx=10)

    canvas = tk.Canvas(list_container, highlightthickness=0)
    scrollbar = tk.Scrollbar(list_container, orient="vertical", command=canvas.yview)
    list_frame = tk.Frame(canvas)

    list_frame.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))
    canvas.create_window((0, 0), window=list_frame, anchor="nw")
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    reason_vars = {}
    for process_name in process_names:
        tk.Label(list_frame, text=process_name, font=("Segoe UI", 9, "bold"), anchor="w").pack(
            fill="x", anchor="w", pady=(8, 0)
        )
        var = tk.StringVar(master=win)
        tk.Entry(list_frame, textvariable=var, width=48).pack(fill="x", anchor="w")
        reason_vars[process_name] = var

    status_label = tk.Label(win, text="", font=("Segoe UI", 9), fg="#c62828")
    status_label.pack(pady=(6, 0))

    def confirm():
        reasons = {name: var.get().strip() for name, var in reason_vars.items()}
        missing = [name for name, reason in reasons.items() if not reason]
        if missing:
            status_label.config(text=f"Enter a reason for: {', '.join(missing)}")
            return

        added = 0
        for process_name, reason in reasons.items():
            _, addition = session_manager.add_process_to_whitelist(process_name, reason)
            if addition is not None:
                added += 1

        win.destroy()
        if added < len(reasons):
            # Session ended mid-form (naturally, nuclear, or via the API)
            # before every entry could be applied — say so rather than
            # silently under-reporting or applying the rest to whatever
            # session starts next (see add_process_to_whitelist's docstring).
            messagebox.showwarning(
                "Carmen Focus",
                f"Session ended before all apps could be added — "
                f"{added} of {len(reasons)} were whitelisted.",
            )
        else:
            messagebox.showinfo(
                "Carmen Focus",
                f"Added {added} app(s) to the session whitelist.",
            )

    def cancel():
        win.destroy()

    button_frame = tk.Frame(win)
    button_frame.pack(pady=14)
    tk.Button(button_frame, text="Confirm", command=confirm).pack(side="left", padx=6)
    tk.Button(button_frame, text="Cancel", command=cancel).pack(side="left", padx=6)


def _build_timer_dialog(root):
    cfg = config.load_config()

    win = tk.Toplevel(root)
    win.title("Carmen Focus — Start Session")
    win.geometry("300x260")
    win.attributes("-topmost", True)

    tk.Label(win, text="Duration (minutes)", font=("Segoe UI", 10)).pack(pady=(18, 4))
    duration_var = tk.StringVar(master=win, value=str(cfg.get("last_duration_minutes", 25)))
    tk.Entry(win, textvariable=duration_var, justify="center").pack()

    tk.Label(win, text="Lock mode", font=("Segoe UI", 10)).pack(pady=(18, 4))
    lock_mode_var = tk.StringVar(master=win, value=cfg.get("last_lock_mode", "soft"))
    mode_frame = tk.Frame(win)
    mode_frame.pack()
    tk.Radiobutton(mode_frame, text="Soft", variable=lock_mode_var, value="soft").pack(side="left", padx=6)
    tk.Radiobutton(mode_frame, text="Hard", variable=lock_mode_var, value="hard").pack(side="left", padx=6)

    process_count = len(cfg.get("processWhitelist", []))
    tk.Label(
        win,
        text=f"Using saved whitelist: {process_count} app(s)",
        font=("Segoe UI", 8),
        fg="#888",
    ).pack(pady=(10, 0))

    status_label = tk.Label(win, text="", font=("Segoe UI", 9), fg="#c62828")
    status_label.pack(pady=(6, 0))

    def start():
        try:
            duration_minutes = float(duration_var.get())
            if duration_minutes <= 0:
                raise ValueError
        except ValueError:
            status_label.config(text="Enter a valid duration.")
            return

        lock_mode = lock_mode_var.get()
        current_cfg = config.load_config()
        process_whitelist = current_cfg.get("processWhitelist", [])
        domain_whitelist = current_cfg.get("domainWhitelist", [])

        # Calls the same function POST /session/start uses, so this session
        # is immediately visible to the browser extension via GET /status —
        # there's only ever one shared session state.
        session_manager.start_session(duration_minutes, lock_mode, process_whitelist, domain_whitelist)

        current_cfg["last_duration_minutes"] = duration_minutes
        current_cfg["last_lock_mode"] = lock_mode
        config.save_config(current_cfg)

        win.destroy()

    tk.Button(win, text="Start Session", command=start).pack(pady=16)
