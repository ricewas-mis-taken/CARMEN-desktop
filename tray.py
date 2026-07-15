"""System tray icon setup and menu."""
import tkinter as tk

import pystray
from PIL import Image, ImageDraw

import gui_thread
import history_gui
import picker_gui
import session_manager


def _generate_icon_image():
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    padding = 6
    draw.ellipse(
        (padding, padding, size - padding, size - padding),
        fill=(45, 140, 255, 255),
    )
    return image


def _format_status_text():
    status = session_manager.get_status()
    if not status["isActive"]:
        return "Carmen Focus — no active session"
    minutes = status["secondsRemaining"] // 60
    seconds = status["secondsRemaining"] % 60
    return (
        f"Carmen Focus — {minutes}m {seconds}s remaining\n"
        f"Lock mode: {status['lockMode']}\n"
        f"Violations: {status['violationCount']}"
    )


def format_end_summary(summary):
    # Nuclear ends get a distinct, unmissable prefix in both the tray toast
    # and session_history.json's text — this is the one end path a user
    # chose deliberately mid-session, as opposed to the clock running out or
    # a plain POST /session/end, so it should read differently at a glance.
    end_type = summary.get("endType", "manual")
    prefix = "NUCLEAR session end" if end_type == "nuclear" else "Session ended"
    reason = summary.get("reason")
    if end_type == "nuclear" and reason:
        prefix += f" — reason: {reason}"

    log = summary.get("violationLog", [])
    if not log:
        return f"{prefix}. No violations — nice work."

    counts = {}
    for entry in log:
        # Process violations have a "process" key, domain violations (from
        # the browser extension) have a "url" key instead.
        label = entry.get("process") or entry.get("url") or "?"
        counts[label] = counts.get(label, 0) + 1
    breakdown = ", ".join(f"{name} x{count}" for name, count in counts.items())
    return f"{prefix}. {summary['violationCount']} violation(s): {breakdown}"


def _build_nuclear_reason_dialog(root, icon):
    # No session running — nothing to nuke, nothing to explain. Skip the
    # prompt so clicking the menu item with no active session is still a
    # harmless no-op, same as it was before this dialog existed.
    if not session_manager.is_active():
        summary = session_manager.end_session(end_type="nuclear")
        icon.notify(format_end_summary(summary), title="Carmen Focus")
        return

    win = tk.Toplevel(root)
    win.title("Carmen Focus — Nuclear End")
    win.geometry("360x180")
    win.attributes("-topmost", True)

    tk.Label(
        win,
        text="Why are you ending this session early?",
        font=("Segoe UI", 10),
        justify="center",
        wraplength=320,
        pady=10,
    ).pack()

    reason_var = tk.StringVar(master=win)
    entry = tk.Entry(win, textvariable=reason_var, width=40)
    entry.pack(pady=(0, 6))
    entry.focus_set()

    status_label = tk.Label(win, text="", font=("Segoe UI", 9), fg="#c62828")
    status_label.pack()

    def confirm():
        reason = reason_var.get().strip()
        if not reason:
            status_label.config(text="Enter a reason before ending.")
            return
        summary = session_manager.end_session(end_type="nuclear", reason=reason)
        icon.notify(format_end_summary(summary), title="Carmen Focus")
        win.destroy()

    def cancel():
        win.destroy()

    button_frame = tk.Frame(win)
    button_frame.pack(pady=14)
    tk.Button(button_frame, text="End Session (Nuclear)", command=confirm).pack(side="left", padx=6)
    tk.Button(button_frame, text="Cancel", command=cancel).pack(side="left", padx=6)

    entry.bind("<Return>", lambda e: confirm())


def build_tray_icon(on_quit):
    icon_image = _generate_icon_image()

    def on_status(icon, item):
        icon.notify(_format_status_text(), title="Carmen Focus Status")

    def on_pick_apps(icon, item):
        picker_gui.open_whitelist_picker()

    def on_start_session(icon, item):
        picker_gui.open_timer_dialog()

    def on_end_session(icon, item):
        # Nuclear-ending mid-session is a deliberate, disruptive act — ask
        # why before it happens so the reason lands in session_history.json
        # alongside it, instead of a bare "someone ended it early" with no
        # context by the time anyone reviews the log.
        gui_thread.run_on_gui_thread(lambda root: _build_nuclear_reason_dialog(root, icon))

    def on_view_history(icon, item):
        history_gui.open_history_viewer()

    def on_quit_clicked(icon, item):
        on_quit()
        icon.stop()

    menu = pystray.Menu(
        pystray.MenuItem("Status", on_status),
        pystray.MenuItem("Start Focus Session", on_start_session),
        pystray.MenuItem("Pick Apps to Whitelist", on_pick_apps),
        pystray.MenuItem("Session History", on_view_history),
        pystray.MenuItem("End Session (Nuclear)", on_end_session),
        pystray.MenuItem("Quit", on_quit_clicked),
    )

    icon = pystray.Icon("carmen_focus", icon_image, "Carmen Focus", menu)
    return icon
