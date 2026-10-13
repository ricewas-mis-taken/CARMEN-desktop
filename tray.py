"""System tray icon setup and menu."""
import pystray
from PIL import Image, ImageDraw
from PySide6.QtWidgets import QApplication

import calendar_gui
import config
import auth_manager
import picker_gui
import qt_gui_thread
import qt_ui.main_window as main_window
import qt_ui.mini_timer as mini_timer
import qt_ui.quicknote as quicknote
import qt_ui.nuclear_dialog as nuclear_dialog
import session_manager
import tasks_store


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
    if status.get("isBurnout"):
        # "Until I burnout" has no fixed duration to count down to -- same
        # stopwatch treatment as the Focus/Tasks tabs, pause-aware elapsed
        # time instead of secondsRemaining counting down from the internal
        # 8-hour ceiling.
        elapsed_seconds = tasks_store.worked_seconds(
            status.get("startTime"), None, status.get("violationLog")
        ) if status.get("startTime") else 0
        minutes, seconds = divmod(elapsed_seconds, 60)
        return (
            f"Carmen Focus — Until burnout, {minutes}m {seconds}s elapsed\n"
            f"Lock mode: {status['lockMode']}\n"
            f"Violations: {status['violationCount']}"
        )
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

    # violationLog also carries "kind": "pause"/"resume" bookkeeping entries
    # (see session_manager's pause_session()/resume_session()) alongside
    # actual process/domain violations — those have neither a "process" nor
    # a "url" key, so counting them here would both wrongly clear the "no
    # violations" message for a session that paused but never violated
    # anything, and show a bogus "? x1" breakdown entry.
    log = [e for e in summary.get("violationLog", []) if e.get("kind") in ("process", "domain")]
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


def _open_nuclear_reason_dialog(icon):
    # No session running — nothing to nuke, nothing to explain. Skip the
    # prompt so clicking the menu item with no active session is still a
    # harmless no-op, same as it was before this dialog existed.
    if not session_manager.is_active():
        summary = session_manager.end_session(end_type="nuclear")
        icon.notify(format_end_summary(summary), title="Carmen Focus")
        return
    nuclear_dialog.open_nuclear_reason_dialog(icon, format_end_summary)


def build_tray_icon(on_quit):
    icon_image = _generate_icon_image()

    def on_open_window(icon, item):
        # The tray icon is now a small persistent launcher for the main
        # sidebar window (Calendar / Focus tabs) rather than a standalone
        # focus on/off toggle — starting/ending sessions and picking apps
        # now live inside the Focus tab (calendar_gui.py), reached from
        # here rather than directly off the tray menu.
        calendar_gui.open_main_window()

    def on_mini_timer(icon, item):
        qt_gui_thread.run_on_gui_thread(mini_timer.open_mini_timer)

    def on_quicknote(icon, item):
        qt_gui_thread.run_on_gui_thread(quicknote.open_quicknote)

    def on_status(icon, item):
        icon.notify(_format_status_text(), title="Carmen Focus Status")

    def on_copy_api_token(icon, item):
        # Every state-changing endpoint in api_server.py now requires this
        # token (see _require_token) -- the browser extension needs it once,
        # pasted into its popup's pairing field, to keep working. It's
        # deliberately not fetchable over the API itself: an unauthenticated
        # endpoint that hands out the auth token would defeat the point of
        # requiring one.
        token = config.get_api_token()
        qt_gui_thread.run_on_gui_thread(lambda: QApplication.clipboard().setText(token))
        icon.notify("API token copied -- paste it into the extension's popup.", title="Carmen Focus")

    def on_quit_clicked(icon, item):
        on_quit()
        icon.stop()

    def _signed_in(item):
        return auth_manager.is_logged_in()

    def _signed_out(item):
        return not auth_manager.is_logged_in()

    def on_sign_in(icon, item):
        qt_gui_thread.run_on_gui_thread(main_window.open_sign_in)

    menu = pystray.Menu(
        # default=True makes a left-click on the icon itself fire this item
        # directly (pystray's win32 backend), instead of opening the
        # right-click context menu — that's the "small persistent tray icon
        # that opens a main window on click" behavior. It's also included
        # normally in the right-click menu as "Open Carmen Focus".
        pystray.MenuItem("Open Carmen Focus", on_open_window, default=True),
        pystray.MenuItem("Mini Timer", on_mini_timer),
        pystray.MenuItem("QuickNote", on_quicknote),
        pystray.MenuItem("Status", on_status),
        # The extension's token is only handed out once signed in, so every user
        # signs in first (and from then on syncs) before the extension is paired.
        pystray.MenuItem("Sign in", on_sign_in, visible=_signed_out),
        pystray.MenuItem("Copy API Token (for browser extension)", on_copy_api_token, visible=_signed_in),
        pystray.MenuItem("Quit", on_quit_clicked),
    )

    icon = pystray.Icon("carmen_focus", icon_image, "Carmen Focus", menu)
    return icon
