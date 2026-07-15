"""Tkinter windows launched from the tray menu: the app whitelist picker and
the start-session timer dialog. Both are built as Toplevel windows on the
single shared GUI-thread root (gui_thread.py) instead of spinning up their
own Tk()/thread — Tkinter is not thread-safe, and two Tk() roots alive in
different threads at once (e.g. one of these dialogs open while an
enforcement popup fires) crashes the whole process with a fatal Tcl error."""
import tkinter as tk

import config
import gui_thread
import installed_apps
import session_manager


def open_whitelist_picker():
    gui_thread.run_on_gui_thread(_build_whitelist_picker)


def open_timer_dialog():
    gui_thread.run_on_gui_thread(_build_timer_dialog)


def _build_whitelist_picker(root):
    cfg = config.load_config()
    saved = {name.lower() for name in cfg.get("processWhitelist", [])}
    apps = installed_apps.list_installed_apps()

    win = tk.Toplevel(root)
    win.title("Carmen Focus — Pick Apps to Whitelist")
    win.geometry("440x560")
    win.attributes("-topmost", True)

    tk.Label(
        win,
        text="Check the apps allowed during a focus session.\nPreviously saved picks are pre-checked.",
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
    if not apps:
        tk.Label(list_frame, text="No installed apps found.", fg="#888").pack(anchor="w", pady=8)
    for app in apps:
        var = tk.BooleanVar(master=win, value=app["process_name"].lower() in saved)
        tk.Checkbutton(
            list_frame,
            text=f"{app['display_name']}   ({app['process_name']})",
            variable=var,
            anchor="w",
            justify="left",
        ).pack(fill="x", anchor="w")
        vars_by_process[app["process_name"]] = var

    status_label = tk.Label(win, text="", font=("Segoe UI", 9), fg="#2e7d32")
    status_label.pack(pady=(6, 0))

    def save():
        selected = [name for name, var in vars_by_process.items() if var.get()]
        current_cfg = config.load_config()
        current_cfg["processWhitelist"] = selected
        config.save_config(current_cfg)
        status_label.config(text=f"Saved {len(selected)} app(s) to the whitelist.")

    tk.Button(win, text="Save Whitelist", command=save).pack(pady=12)


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
